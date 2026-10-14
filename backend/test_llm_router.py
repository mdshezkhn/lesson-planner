"""Multi-provider router tests (backend/llm.py) — no external API keys needed.

Spins up a local OpenAI-compatible mock server that behaves differently per
model name:
    "good"        -> valid gap-analysis JSON
    "flaky"       -> 429 once, then valid JSON           (retry path)
    "garbage"     -> non-JSON prose                      (lenient-parse fail -> fallback)
    "wrongshape"  -> valid JSON but not a report         (validate() reject -> fallback)

Covers: route parsing, skipped providers (missing keys), transient retry,
fallback chains, engine meta, and analyzer.analyze_material end-to-end.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ["LLM_ATTEMPTS"] = "2"
os.environ["LLM_DEADLINE"] = "20"
os.environ.pop("OPENROUTER_API_KEY", None)
os.environ.pop("NVIDIA_API_KEY", None)
os.environ.pop("GROQ_API_KEY", None)

import llm  # noqa: E402

VALID_REPORT = {
    "objectives": [{"id": "O1", "text": "main idea"}],
    "coverage": [{"objective_id": "O1", "status": "covered", "evidence": "Unit 1", "comment": "ok"}],
    "slides": [{"index": 1, "title": "T", "verdict": "keep", "feedback": "fine"}],
    "new_slides": [],
    "summary": "s",
    "gaps": [],
}


class MockOpenAIHandler(BaseHTTPRequestHandler):
    state = {"flaky_count": 0}

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        model = body["model"]
        if model == "flaky":
            self.state["flaky_count"] += 1
            if self.state["flaky_count"] == 1:
                self._respond(429, {"error": "rate limited"})
                return
            content = json.dumps(VALID_REPORT)
        elif model == "good":
            content = "```json\n" + json.dumps(VALID_REPORT) + "\n```"  # fences tolerated
        elif model == "garbage":
            content = "Sorry! I cannot answer that right now."
        elif model == "wrongshape":
            content = json.dumps({"hello": ["world"]})
        else:
            self._respond(500, {"error": "unknown model"})
            return
        self._respond(200, {"choices": [{"message": {"content": content}}]})

    def _respond(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # silence request log
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), MockOpenAIHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["LLM_CUSTOM_BASE_URL"] = f"http://127.0.0.1:{server.server_address[1]}/v1"
os.environ["LLM_CUSTOM_API_KEY"] = "test-key"

results = []


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    return bool(cond)


def check_shape(d):
    """Same validator the analyzer uses — reject JSON that isn't a report."""
    if not (isinstance(d, dict) and isinstance(d.get("slides"), list) and d["slides"]):
        raise ValueError("not a gap-analysis report")


# ---------- route parsing ----------
steps = llm.parse_route("openrouter:a:free, gemini:b ,groq:c")
results.append(ok([s.provider for s in steps] == ["openrouter", "gemini", "groq"], f"parse_route -> {steps}"))
try:
    llm.parse_route("  ")
    results.append(ok(False, "empty route should raise"))
except ValueError:
    results.append(ok(True, "empty route raises ValueError"))

# ---------- retry on transient 429 ----------
data, engine = llm.chat_json("x", "custom:flaky", validate=lambda d: None)
results.append(ok(engine["model"] == "flaky" and engine["attempts"][0].get("transient"), f"429 retried then ok -> {engine['attempts']}"))

# ---------- fallback chain: garbage -> wrongshape -> flaky(429)->good ----------
data, engine = llm.chat_json("x", "custom:garbage,custom:wrongshape,custom:flaky", validate=check_shape)
results.append(ok(engine["model"] == "flaky", f"fell through to flaky -> {engine['provider']}:{engine['model']}"))
results.append(ok(sum(1 for a in engine["attempts"] if a.get("ok")) == 1, f"attempt log: {engine['attempts']}"))

# ---------- validate() rejection moves down the chain ----------
data, engine = llm.chat_json("x", "custom:wrongshape,custom:good", validate=check_shape)
results.append(ok(engine["model"] == "good" and engine["attempts"][0]["ok"] is False, "invalid report shape rejected"))

# ---------- unconfigured providers are skipped, chain survives ----------
data, engine = llm.chat_json("x", "groq:anything,gemini:gemini-2.5-flash,custom:good")
skipped = [a["provider"] for a in engine["attempts"] if "skipped" in a]
results.append(ok(engine["model"] == "good" and skipped == ["groq", "gemini"], f"skipped without keys: {skipped}"))

# ---------- everything fails -> single clear error ----------
try:
    llm.chat_json("x", "custom:garbage,custom:wrongshape", validate=check_shape)
    results.append(ok(False, "all-fail route should raise"))
except RuntimeError as exc:
    results.append(ok("All providers" in str(exc), f"clear error when whole chain fails: {str(exc)[:60]}…"))

# ---------- end-to-end via analyzer.analyze_material ----------
import analyzer  # noqa: E402

os.environ["LLM_ROUTE"] = "custom:good"
report = analyzer.analyze_material("Unit 1: main idea", "pptx", "old.pptx", "main idea", "3rd Grade", "Reading", True)
results.append(ok(len(report["slides"]) == 1 and report["_engine"]["provider"] == "custom", "analyzer end-to-end via router"))
results.append(ok(report["_engine"]["task"] == "analyst" and ":" in report["_engine"]["route"], f"engine meta: {report['_engine']['task']} via {report['_engine']['route'][:40]}…"))

# ---------- agents.route_for precedence ----------
os.environ["AGENT_ANALYST"] = "custom:per-task"
os.environ["LLM_ROUTE"] = "custom:global"
results.append(ok(analyzer.agents.route_for("analyst") == "custom:per-task", "AGENT_ANALYST beats LLM_ROUTE"))
os.environ.pop("AGENT_ANALYST")
results.append(ok(analyzer.agents.route_for("analyst") == "custom:global", "LLM_ROUTE beats default"))
os.environ.pop("LLM_ROUTE")
results.append(ok("openrouter" in analyzer.agents.route_for("analyst"), "default route restored"))

server.shutdown()
print("\n" + ("ALL PASS" if all(results) else "SOME FAILED"))
raise SystemExit(0 if all(results) else 1)
