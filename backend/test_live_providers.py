"""Live one-shot provider check — real API calls, minimal tokens.

Usage: python test_live_providers.py [openrouter|nvidia|both]
Bypasses the local proxy for openrouter.ai / nvidia / ngc domains (both are
reachable directly from this network; the .env proxy exists for Google).
"""
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

# Direct connection for non-Google providers; keep proxy only for Google.
os.environ["HTTPS_PROXY"] = ""
os.environ["HTTP_PROXY"] = ""
os.environ["ALL_PROXY"] = ""

import httpx  # noqa: E402

results = []


def check(name, cond, extra=""):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {extra}", flush=True)


def openrouter_key_info():
    key = os.environ.get("OPENROUTER_API_KEY", "")
    r = httpx.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}, timeout=20)
    return r.status_code, r.json() if r.status_code == 200 else {}


def chat(provider: str, model: str) -> tuple[bool, str, float]:
    if provider == "openrouter":
        base, key = "https://openrouter.ai/api/v1", os.environ["OPENROUTER_API_KEY"]
    elif provider == "zhipu":
        base, key = "https://open.bigmodel.cn/api/paas/v4", os.environ["ZHIPU_API_KEY"]
    else:
        base, key = "https://integrate.api.nvidia.com/v1", os.environ["NVIDIA_API_KEY"]
    payload_extra: dict = {}
    if provider == "zhipu" and any(t in model for t in ("glm-4.5", "glm-5")):
        payload_extra["reasoning_effort"] = "low"  # GLM-5 always thinks; default burns minutes
    t0 = time.monotonic()
    try:
        r = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": 'Reply with exactly this JSON and nothing else: {"ok": true}'}],
                "temperature": 0,
                "max_tokens": 2000,
                **payload_extra,
            },
            timeout=75,
        )
        dt = time.monotonic() - t0
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}: {r.text[:180]}", dt
        msg = r.json()["choices"][0]["message"]
        text = msg.get("content") or ""  # reasoning models may put tokens in reasoning, content=None
        ok_json = '"ok"' in text or "'ok'" in text
        extra = f"{dt:.1f}s  reply={text[:60]!r}"
        if not ok_json and msg.get("reasoning"):
            extra += "  [reasoning tokens present, content empty — raise max_tokens]"
        return ok_json, extra, dt
    except Exception as exc:
        return False, f"{exc.__class__.__name__}: {str(exc)[:150]}", time.monotonic() - t0


which = sys.argv[1] if len(sys.argv) > 1 else "both"

if which in ("openrouter", "both"):
    code, info = openrouter_key_info()
    if code == 200:
        limit = info.get("data", {}).get("limit", None) or "no-limit(free tier)"
        usage = info.get("data", {}).get("usage", "?")
        check("openrouter key valid", True, f"usage={usage} limit={limit}")
    else:
        check("openrouter key valid", False, f"HTTP {code}")
    for model in ("nvidia/nemotron-3.5-lightning:free", "openrouter/free"):
        ok, extra, _ = chat("openrouter", model)
        check(f"openrouter chat {model}", ok, extra)

if which in ("nvidia", "both"):
    key = os.environ["NVIDIA_API_KEY"]
    try:
        r = httpx.get("https://integrate.api.nvidia.com/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        if r.status_code == 200:
            ids = [m["id"] for m in r.json().get("data", [])]
            check("nvidia key valid + model list", True, f"{len(ids)} models; llama-3.3 present={any('llama-3.3-70b-instruct' in i for i in ids)}")
        else:
            check("nvidia key valid", False, f"HTTP {r.status_code}: {r.text[:150]}")
            ids = []
    except Exception as exc:
        check("nvidia model list", False, f"{exc.__class__.__name__}: {str(exc)[:120]}")
        ids = []
    candidates = [
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "google/gemma-4-31b-it",
        "nvidia/nemotron-3-super-120b-a12b",
    ]
    for model in candidates:
        ok, extra, _ = chat("nvidia", model)
        check(f"nvidia chat {model}", ok, extra)

if which in ("zhipu", "all"):
    ok, extra, _ = chat("zhipu", "glm-5.3-flash")
    check("zhipu chat glm-5.3-flash (China-direct, free tier)", ok, extra)

print("\n" + ("ALL PASS" if all(results) else "SOME FAILED"))
sys.exit(0 if all(results) else 1)
