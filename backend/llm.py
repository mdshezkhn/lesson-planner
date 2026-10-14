"""Multi-provider LLM router for Lesson Sync — one call, any provider.

Route spec strings (comma-separated fallback chain, tried left to right):
    "openrouter:nvidia/nemotron-3.5-lightning:free,gemini:gemini-2.5-flash"

Supported provider prefixes:
    openrouter:  OpenRouter (https://openrouter.ai/keys)   — many :free models
    nvidia:      NVIDIA NIM  (https://build.nvidia.com)    — free credits
    groq:        Groq        (https://console.groq.com/keys) — free tier
    zhipu:       Zhipu/BigModel (https://open.bigmodel.cn) — China-direct, no VPN needed
    gemini:      Google AI Studio (existing google-genai path, kept intact)
    custom:      ANY OpenAI-compatible endpoint (LLM_CUSTOM_BASE_URL, e.g. local
                 Ollama at http://localhost:11434/v1)
    mock:        returns a canned payload (tests / offline demo)

Env vars:
    OPENROUTER_API_KEY / OPENROUTER_BASE_URL (default https://openrouter.ai/api/v1)
    NVIDIA_API_KEY     / NVIDIA_BASE_URL     (default https://integrate.api.nvidia.com/v1)
    GROQ_API_KEY       / GROQ_BASE_URL       (default https://api.groq.com/openai/v1)
    LLM_CUSTOM_BASE_URL / LLM_CUSTOM_API_KEY / LLM_CUSTOM_MODEL_HEADER?
    LLM_TIMEOUT   seconds per attempt           (default 40)
    LLM_DEADLINE  total wall-clock budget for a full chain (default 55 — keeps
                  us inside Vercel's 60s function ceiling)
    LLM_ATTEMPTS  tries per provider for transient errors (default 2)
"""
from __future__ import annotations

import json
import os
import time
from collections import namedtuple

DEFAULT_TIMEOUT = 40.0
DEFAULT_DEADLINE = 55.0
DEFAULT_ATTEMPTS = 2

RouteStep = namedtuple("RouteStep", "provider model")

_OPENAI_COMPAT = {
    "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    "nvidia": ("NVIDIA_API_KEY", "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
    "groq": ("GROQ_API_KEY", "GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
    "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "https://api.openai.com/v1"),
    "zhipu": ("ZHIPU_API_KEY", "ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),  # China-direct, no VPN needed
    "custom": ("LLM_CUSTOM_API_KEY", "LLM_CUSTOM_BASE_URL", None),  # base URL required
}

_TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 524}


# --------------------------------------------------------------------------- #
# Route parsing
# --------------------------------------------------------------------------- #
def parse_route(spec: str) -> list[RouteStep]:
    steps = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        provider, _, model = part.partition(":")
        steps.append(RouteStep(provider.strip().lower(), model.strip()))
    if not steps:
        raise ValueError("Empty LLM route spec.")
    return steps


def _endpoint(provider: str) -> tuple[str, str] | None:
    """Return (base_url, api_key) for an OpenAI-compatible provider, or None if not configured."""
    if provider not in _OPENAI_COMPAT:
        return None
    key_env, base_env, default_base = _OPENAI_COMPAT[provider]
    api_key = os.getenv(key_env, "").strip()
    base_url = (os.getenv(base_env) or default_base or "").strip().rstrip("/")
    if not api_key or not base_url:
        return None
    return base_url, api_key


def _gemini_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def configured_providers() -> list[str]:
    names = [p for p in _OPENAI_COMPAT if _endpoint(p)]
    if _gemini_configured():
        names.append("gemini")
    return names


def any_provider_configured() -> bool:
    return bool(configured_providers())


# --------------------------------------------------------------------------- #
# Lenient JSON loading (shared with analyzer; tolerates ``` fences and prose)
# --------------------------------------------------------------------------- #
def loads_lenient(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


# --------------------------------------------------------------------------- #
# Individual provider calls
# --------------------------------------------------------------------------- #
def _openai_compat_chat(step: RouteStep, prompt: str, timeout: float, temperature: float) -> str:
    import httpx

    base_url, api_key = _endpoint(step.provider)  # type: ignore[misc]
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if step.provider == "openrouter":
        # Optional attribution headers; OpenRouter shows them on the dashboards.
        headers["HTTP-Referer"] = os.getenv("OPENROUTER_SITE_URL", "https://lesson-sync.vercel.app")
        headers["X-Title"] = "Lesson Sync"
    payload = {
        "model": step.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    if step.provider == "zhipu" and any(t in step.model for t in ("glm-4.5", "glm-5")):
        # GLM-4.5+/GLM-5 always think; Zhipu rejects {"thinking": {"type": ...}} on
        # GLM-5 (error 1210) but accepts the OpenAI-style reasoning_effort. Default
        # effort burned 329s on a real deck — "low" is the fix (mirrors Gemini
        # thinking_budget=0).
        payload["reasoning_effort"] = "low"
    if step.provider == "zhipu":
        # China-direct guarantee: bigmodel.cn is reachable without any VPN, so
        # deliberately IGNORE the machine's proxy settings (a dead/off proxy
        # would otherwise break the one provider that always works there).
        with httpx.Client(trust_env=False, timeout=timeout) as client:
            r = client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
    else:
        r = httpx.post(f"{base_url}/chat/completions", headers=headers, json=payload, timeout=timeout)
    if r.status_code in _TRANSIENT_STATUS:
        raise TransientError(f"{step.provider} HTTP {r.status_code}: {r.text[:200]}")
    if r.status_code >= 400:
        raise RuntimeError(f"{step.provider} HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""


def _gemini_chat(step: RouteStep, prompt: str, temperature: float) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model=step.model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=temperature,
            # Disable "thinking" — 2.5 Flash spends 20-40s reasoning by default,
            # which risks the 60s serverless ceiling on real decks.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return response.text or ""


def _mock_chat(step: RouteStep, prompt: str) -> str:
    return json.dumps({"_mock_provider": step.model})


def generate_image(prompt: str, route_spec: str, size: str = "1024x1024", timeout: float = 45.0) -> tuple[str, dict]:
    """Generate one image through the route chain; return (image_url, engine_meta).

    Currently supports zhipu cogview models (China-direct, cogview-3-flash is free).
    Follows the same fallback-chain / attempt-log conventions as chat_json.
    """
    attempts: list[dict] = []
    last_error: Exception | None = None
    for step in parse_route(route_spec):
        if step.provider == "mock":
            return "mock://illustration", {"provider": "mock", "model": step.model or "mock", "attempts": attempts}
        if step.provider != "zhipu":
            attempts.append({"provider": step.provider, "model": step.model, "skipped": "no image API for this provider"})
            continue
        if not (endpoint := _endpoint("zhipu")):
            attempts.append({"provider": "zhipu", "model": step.model, "skipped": "ZHIPU_API_KEY not set"})
            continue
        base_url, api_key = endpoint
        t0 = time.monotonic()
        try:
            import httpx

            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            with httpx.Client(trust_env=False, timeout=timeout) as client:  # China-direct, like chat
                r = client.post(f"{base_url}/images/generations", headers=headers, json={"model": step.model, "prompt": prompt, "size": size})
            if r.status_code in _TRANSIENT_STATUS:
                raise TransientError(f"zhipu images HTTP {r.status_code}: {r.text[:160]}")
            if r.status_code >= 400:
                raise RuntimeError(f"zhipu images HTTP {r.status_code}: {r.text[:200]}")
            url = (r.json().get("data") or [{}])[0].get("url")
            if not url:
                raise RuntimeError("zhipu images: no url in response")
            attempts.append({"provider": "zhipu", "model": step.model, "ok": True, "seconds": round(time.monotonic() - t0, 1)})
            return url, {"provider": "zhipu", "model": step.model, "attempts": attempts}
        except Exception as exc:
            attempts.append({"provider": "zhipu", "model": step.model, "ok": False, "seconds": round(time.monotonic() - t0, 1), "error": f"{exc.__class__.__name__}: {str(exc)[:140]}"})
            last_error = exc
    raise RuntimeError(f"All image providers failed. Route: {route_spec}. Last error: {last_error}")


class TransientError(Exception):
    """Rate-limits, 5xx, timeouts — worth retrying / falling through the chain."""


# --------------------------------------------------------------------------- #
# The router
# --------------------------------------------------------------------------- #
def chat_json(
    prompt: str,
    route_spec: str,
    temperature: float = 0.3,
    validate=None,
) -> tuple[dict, dict]:
    """Run `prompt` through the route chain; return (parsed_json, engine_meta).

    `validate(dict)` may raise to reject a syntactically-valid but wrong reply —
    a rejected reply is treated like a transient failure and the chain moves on.
    Every attempt (ok or failed) is recorded in engine_meta["attempts"].
    """
    deadline = time.monotonic() + float(os.getenv("LLM_DEADLINE", DEFAULT_DEADLINE))
    timeout_cfg = float(os.getenv("LLM_TIMEOUT", DEFAULT_TIMEOUT))
    attempts_max = max(1, int(os.getenv("LLM_ATTEMPTS", DEFAULT_ATTEMPTS)))

    attempts: list[dict] = []
    last_error: Exception | None = None

    for step in parse_route(route_spec):
        # budget guard: don't start an attempt we can't finish
        remaining = deadline - time.monotonic()
        if remaining < 8.0:
            attempts.append({"provider": step.provider, "model": step.model, "skipped": "deadline budget exhausted"})
            continue

        if step.provider == "mock":
            text = _mock_chat(step, prompt)
            engine = {"provider": "mock", "model": step.model or "mock", "attempts": attempts}
            return loads_lenient(text), engine

        if step.provider == "gemini":
            if not _gemini_configured():
                attempts.append({"provider": "gemini", "model": step.model, "skipped": "GEMINI_API_KEY not set"})
                continue
            call = lambda s=step, t=temperature: _gemini_chat(s, prompt, t)  # noqa: E731
            label = f"gemini:{step.model}"
        elif endpoint := _endpoint(step.provider):
            # timeout evaluated at CALL time so retries shrink with the deadline
            call = lambda e=endpoint, s=step: _openai_compat_chat(  # noqa: E731
                s, prompt, min(timeout_cfg, max(5.0, deadline - time.monotonic())), temperature
            )
            label = f"{step.provider}:{step.model}"
        else:
            key_env = _OPENAI_COMPAT.get(step.provider, (f"{step.provider.upper()}_API_KEY",))[0]
            attempts.append({"provider": step.provider, "model": step.model, "skipped": f"{key_env} not set"})
            continue

        for attempt in range(1, attempts_max + 1):
            t0 = time.monotonic()
            try:
                text = call()
                data = loads_lenient(text)
                if validate:
                    validate(data)
                attempts.append({"provider": step.provider, "model": step.model, "ok": True, "seconds": round(time.monotonic() - t0, 1)})
                return data, {"provider": step.provider, "model": step.model, "attempts": attempts}
            except Exception as exc:
                # httpx timeouts/connect errors are NOT built-in TimeoutError
                # subclasses, so match by class name too (dependency-free).
                name = type(exc).__name__
                transient = (
                    isinstance(exc, TransientError)
                    or isinstance(exc, TimeoutError)
                    or any(t in name for t in ("Timeout", "Connect", "Read", "Write", "Remote"))
                    or "429" in str(exc)
                    or "UNAVAILABLE" in str(exc)
                )
                attempts.append(
                    {
                        "provider": step.provider,
                        "model": step.model,
                        "ok": False,
                        "seconds": round(time.monotonic() - t0, 1),
                        "error": f"{exc.__class__.__name__}: {str(exc)[:160]}",
                        "transient": transient,
                    }
                )
                last_error = exc
                if not transient or attempt == attempts_max:
                    break  # fall through to next provider
                if time.monotonic() + 2.0 > deadline:
                    break
                time.sleep(2.0)

    raise RuntimeError(
        f"All providers in the route failed. Route was: {route_spec}. "
        f"Attempts: {json.dumps(attempts)[:600]}. Last error: {last_error}"
    )
