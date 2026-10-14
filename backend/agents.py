"""Agent registry — which model pipeline runs each Lesson Sync task.

A "route" is a fallback chain of provider:model steps, e.g.
    openrouter:nvidia/nemotron-3.5-lightning:free,nvidia:meta/llama-3.3-70b-instruct,gemini:gemini-2.5-flash

Resolution order for a task name (see llm.parse_route for provider prefixes):
    1. AGENT_<TASK>  (per-task override, e.g. AGENT_ANALYST)
    2. LLM_ROUTE     (global override — same chain for every task)
    3. DEFAULT_ROUTES[task]

Free-first defaults (checked 2026-10 against https://openrouter.ai/api/v1/models —
free model IDs rotate, so override via env rather than editing code):
    analyst    – gap analysis of the old deck (long context, JSON)
    rewriter   – revises slide content (future rewriter agent)
    worksheet  – builds the practice worksheet (future agent)
    illustrator– slide illustrations (future image agent; needs an image model)
"""
from __future__ import annotations

import os

# Free OpenRouter models first, NVIDIA NIM (free credits) next, Gemini last —
# the app already ships with a GEMINI_API_KEY, so the chain never dead-ends.
DEFAULT_ROUTES: dict[str, str] = {
    # NOTE: model IDs below were verified live against these two accounts on
    # 2026-10 (see test_live_providers.py). NVIDIA NIM calls require the exact
    # account-visible IDs (nvidia/nemotron-3.5-lightning-30b-a3b, google/gemma-4-31b-it);
    # OpenRouter free IDs carry the :free suffix and rotate — check
    # https://openrouter.ai/api/v1/models if a step starts failing.
    "analyst": ",".join(
        [
            "zhipu:glm-5.3-flash",                             # China-direct (no VPN needed), free, ~26s
            "groq:openai/gpt-oss-120b",                        # fastest when reachable (~8s, 131k ctx)
            "nvidia:nvidia/nemotron-3-super-120b-a12b",        # ~28s, 262k ctx — big decks
            "openrouter:openrouter/free",                      # rotating free router (slow but reliable)
            "gemini:gemini-2.5-flash",                         # 1M ctx final fallback
        ]
    ),
    "rewriter": ",".join(
        [
            "zhipu:glm-5.3-flash",
            "groq:openai/gpt-oss-120b",
            "nvidia:nvidia/nemotron-3.5-lightning-30b-a3b",
            "openrouter:google/gemma-4-31b-it:free",
            "gemini:gemini-2.5-flash",
        ]
    ),
    "worksheet": ",".join(
        [
            "zhipu:glm-5.3-flash",
            "groq:openai/gpt-oss-120b",
            "nvidia:nvidia/nemotron-3-super-120b-a12b",
            "openrouter:openrouter/free",
            "gemini:gemini-2.5-flash",
        ]
    ),
    "illustrator": "zhipu:cogview-3-flash",  # free tier; China-direct. cogview-4-250304 = paid upgrade (env override)
}


def route_for(task: str) -> str:
    per_task = os.getenv(f"AGENT_{task.upper()}", "").strip()
    if per_task:
        return per_task
    global_route = os.getenv("LLM_ROUTE", "").strip()
    if global_route:
        return global_route
    return DEFAULT_ROUTES.get(task, DEFAULT_ROUTES["analyst"])
