"""Gap analysis between old lesson material and new objectives (multi-provider).

The heavy lifting is delegated to llm.py — a provider router that runs the
prompt through a fallback chain (default: OpenRouter free models -> NVIDIA NIM
-> Gemini), so the app works with free keys and survives rate limits.

Produces a strict-JSON report:
  objectives  – the current-year objectives, normalised into discrete items
  coverage    – per-objective status (covered / partial / missing) with evidence
  slides      – per-slide verdict (keep / revise / remove) + concrete feedback
  new_slides  – proposed slides that fill the gaps
  summary     – overall diagnosis, gaps – short bullet list
  _engine     – which provider/model produced it + per-attempt log
"""
from __future__ import annotations

import os

import agents
import llm

MODEL_DEFAULT = "gemini-2.5-flash"  # kept for /health and legacy GEMINI_MODEL users

VALID_VERDICTS = {"keep", "revise", "remove"}
VALID_STATUSES = {"covered", "partial", "missing"}


def _validate_report_shape(data: dict) -> None:
    """Reject syntactically-valid JSON that is not an analysis report."""
    if not isinstance(data, dict) or not isinstance(data.get("slides"), list) or not data["slides"]:
        raise ValueError("reply is not a gap-analysis report (no slides[])")


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
def build_analysis_prompt(
    material_text: str,
    material_kind: str,
    source_name: str,
    objectives: str,
    grade_level: str,
    categories: str,
    interactive: bool,
) -> str:
    return f"""You are an expert ESL/EAL curriculum analyst and instructional coach.

A teacher is reusing last year's lesson material for a NEW lesson this year.

CURRENT-YEAR LESSON CONTEXT
- Grade level: {grade_level}
- Skill categories: {categories}
- Include interactive games/activities: {interactive}
- Current-year lesson objectives (verbatim from the teacher):
\"\"\"{objectives.strip() or '(no objectives provided)'}\"\"\"

PREVIOUS-YEAR MATERIAL ({material_kind}: {source_name}), unit = slide/page/section:
{material_text}

YOUR TASK — analyse alignment and produce a strict JSON report.

1. objectives: normalise the objectives into 1-6 discrete, assessable items with ids O1, O2, ...
2. coverage: for EVERY objective, judge status = "covered" (material clearly teaches it),
   "partial" (touched but incomplete or at the wrong depth), or "missing".
   Give evidence citing unit numbers, and a short comment.
3. slides: for EVERY unit give verdict "keep" (aligned), "revise" (salvageable: misaligned,
   outdated, wrong level, or needs objective tagging), or "remove" (off-topic/duplicative).
   Give kind, specific, actionable feedback a teacher can act on (reference the objective ids).
   For verdict "revise" also provide revised_title and revised_content (revised_content =
   bullet lines separated by \\n, max 5 bullets, max 12 words per bullet — the deck uses very
   large font). Match the grade level's language level. Use UK-style classroom English.
4. new_slides: propose slides that close the gaps between coverage and the objectives
   (missing/partial objectives, plus one warm-up game/activity slide if interactive=true and
   none exists). Each needs: after_index (unit number to insert after; use 0 for the start),
   title, content (bullet lines separated by \\n, max 5 bullets), objective_ids it serves,
   reason. Order the list by after_index ascending.
5. summary: 2-4 sentences diagnosing overall alignment. gaps: short bullet strings naming
   each uncovered/partial objective.

Return ONLY valid JSON, no markdown fences, in this exact shape:
{{
  "objectives": [{{"id": "O1", "text": "..."}}],
  "coverage": [{{"objective_id": "O1", "status": "covered|partial|missing", "evidence": "Unit 3: ...", "comment": "..."}}],
  "slides": [{{"index": 1, "title": "...", "verdict": "keep|revise|remove", "matched_objective_ids": ["O1"], "feedback": "...", "revised_title": null, "revised_content": null}}],
  "new_slides": [{{"after_index": 3, "title": "...", "content": "bullet 1\\nbullet 2", "objective_ids": ["O2"], "reason": "..."}}],
  "summary": "...",
  "gaps": ["..."]
}}"""


# --------------------------------------------------------------------------- #
# Validation / normalisation
# --------------------------------------------------------------------------- #
def normalize_report(report: dict) -> dict:
    report.setdefault("objectives", [])
    report.setdefault("coverage", [])
    report.setdefault("slides", [])
    report.setdefault("new_slides", [])
    report.setdefault("summary", "")
    report.setdefault("gaps", [])

    for cov in report["coverage"]:
        if cov.get("status") not in VALID_STATUSES:
            cov["status"] = "partial"
    for sl in report["slides"]:
        if sl.get("verdict") not in VALID_VERDICTS:
            sl["verdict"] = "keep"
        sl.setdefault("revised_title", None)
        sl.setdefault("revised_content", None)
        sl.setdefault("matched_objective_ids", [])
        sl.setdefault("feedback", "")
    for ns in report["new_slides"]:
        ns.setdefault("after_index", 0)
        ns.setdefault("objective_ids", [])
        ns.setdefault("reason", "")
    report["slides"].sort(key=lambda s: s.get("index", 0))
    report["new_slides"].sort(key=lambda s: s.get("after_index", 0))
    return report


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def illustration_prompt(slide_title: str, content: str, grade_level: str = "", categories: str = "") -> str:
    """Deterministic image prompt for a slide — no extra LLM call, no latency."""
    topic = (slide_title or "lesson activity").strip()[:120]
    detail = " ".join(line.strip("•-– ") for line in (content or "").splitlines() if line.strip())[:220]
    level = grade_level.strip() or "primary school"
    focus = categories.strip() or "English lesson"
    return (
        f"Kid-friendly flat cartoon illustration for a {level} {focus} classroom. "
        f"Scene depicts: {topic}. {detail}. "
        "Bright cheerful colours, warm and encouraging mood, diverse young learners, "
        "clean uncluttered composition, soft rounded shapes, "
        "absolutely no text, letters, numbers or words anywhere in the image."
    )


def illustratable_slides(report: dict, max_images: int = 3) -> list[dict]:
    """New gap-filling slides first; design-from-scratch decks fall back to content slides."""
    report = normalize_report(report)
    targets = [ns for ns in report.get("new_slides", []) if ns.get("title")]
    if not targets:
        targets = [s for s in report.get("slides", []) if s.get("content") and s.get("verdict") != "remove"]
    return targets[:max(1, min(max_images, 4))]


def analyze_material(
    material_text: str,
    material_kind: str,
    source_name: str,
    objectives: str,
    grade_level: str,
    categories: str,
    interactive: bool,
) -> dict:
    prompt = build_analysis_prompt(
        material_text, material_kind, source_name, objectives, grade_level, categories, interactive
    )
    route = agents.route_for("analyst")
    report, engine = llm.chat_json(prompt, route, temperature=0.3, validate=_validate_report_shape)
    report = normalize_report(report)
    report["_engine"] = {**engine, "route": route, "task": "analyst"}
    return report


def design_lesson(
    objectives: str, grade_level: str, categories: str, interactive: bool
) -> dict:
    """Design a brand-new lesson deck from scratch (no reference material)."""
    prompt = f"""You are an expert ESL/EAL lesson designer.

Design a complete, engaging lesson presentation from scratch.

- Grade level: {grade_level}
- Skill categories: {categories}
- Include interactive games/activities: {interactive}
- Lesson objectives:
'''{objectives.strip()}'''

Requirements: 8-12 slides including a title slide, a warm-up/starter, clear
teaching segments that build each objective in order, guided practice, an
independent task{", and at least one interactive game/activity slide" if interactive else ""}.
Slide content lines are shown in very large font: max 5 bullets per slide,
max 12 words per bullet.

Return ONLY valid JSON, no markdown fences:
{{
  "title": "Lesson title",
  "objectives": ["objective 1", "objective 2"],
  "slides": [{{ "title": "Slide title", "content": "bullet 1\nbullet 2" }}]
}}"""
    import agents
    import llm

    result, engine = llm.chat_json(prompt, agents.route_for("designer"))
    result["_engine"] = engine.get("attempts", [])
    result.setdefault("title", "Lesson")
    result.setdefault("slides", [])
    result.setdefault("objectives", [])
    if not isinstance(result.get("slides"), list) or not result["slides"]:
        raise ValueError("AI returned no slides.")
    return result


def design_lesson_mock() -> dict:
    return {
        "title": "Mock Lesson",
        "objectives": ["Mock objective"],
        "slides": [
            {"title": "Warm-up", "content": "Mock warm-up\nMock game"},
            {"title": "Teaching point", "content": "Mock teaching\nMock example"},
            {"title": "Practice", "content": "Mock practice"},
        ],
    }


def mock_report(n_units: int = 5) -> dict:
    """Offline demo report (MOCK_MODE=1 or missing key) so the flow is testable."""
    slides = []
    for i in range(1, n_units + 1):
        verdict = "keep" if i == 1 else ("revise" if i % 2 == 0 else "remove")
        slides.append(
            {
                "index": i,
                "title": f"Sample slide {i}",
                "verdict": verdict,
                "matched_objective_ids": ["O1"] if i == 1 else [],
                "feedback": "Mock feedback: this is placeholder analysis output (MOCK_MODE).",
                "revised_title": f"Revised slide {i}" if verdict == "revise" else None,
                "revised_content": "Revised bullet one\nRevised bullet two" if verdict == "revise" else None,
            }
        )
    return normalize_report(
        {
            "objectives": [
                {"id": "O1", "text": "Identify the main idea of a short text"},
                {"id": "O2", "text": "Use supporting details to justify answers"},
            ],
            "coverage": [
                {"objective_id": "O1", "status": "covered", "evidence": "Unit 1", "comment": "Mock"},
                {"objective_id": "O2", "status": "missing", "evidence": "-", "comment": "Mock gap"},
            ],
            "slides": slides,
            "new_slides": [
                {
                    "after_index": 1,
                    "title": "Supporting details: find the evidence",
                    "content": "Read the paragraph\nHighlight two details\nShare with a partner",
                    "objective_ids": ["O2"],
                    "reason": "Objective O2 is not addressed anywhere in the old deck.",
                }
            ],
            "summary": "Mock summary: old deck partially matches the new objectives; one gap detected.",
            "gaps": ["O2: using supporting details is never practised"],
        }
    )
