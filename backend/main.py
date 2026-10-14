"""Lesson Sync — FastAPI backend (stateless, runs locally AND on Vercel serverless).

Endpoints
  GET  /health        – liveness + config check
  POST /analyze       – old material + objectives -> gap-analysis report
  POST /illustrate    – report -> kid-friendly images for the new slides (URLs)
  POST /generate      – report (+ original file for edit mode) -> updated .pptx
  POST /upload-chunk  – one chunk of a large file (Vercel Blob storage path)

Large-file support: Vercel serverless caps request bodies at ~4.5 MB. Files over
that are uploaded in ~3 MB chunks to Vercel Blob (`/upload-chunk`), then
assembled server-side inside `/analyze` and `/generate`. Requires the project to
have a Vercel Blob store connected (env: BLOB_STORE_ID or BLOB_READ_WRITE_TOKEN).

Env vars: see README. Key ones:
  GEMINI_API_KEY, OPENROUTER_API_KEY, NVIDIA_API_KEY, GROQ_API_KEY,
  LLM_ROUTE / AGENT_<TASK> (model routing — see agents.py), MOCK_MODE,
  APP_ACCESS_CODE, ALLOWED_ORIGINS,
  MAX_UPLOAD_MB (default 50), UPLOAD_DIR (defaults to the OS temp dir).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

import analyzer
import agents
import generator
import llm
import parsers

if not os.getenv("VERCEL"):
    # Local dev: load backend/.env (API key, optional proxy). On Vercel the env
    # comes from project settings; dotenv is skipped so no local file can leak
    # proxy settings into production.
    load_dotenv()

UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(tempfile.gettempdir(), "lesson-sync-uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

MOCK_MODE = os.getenv("MOCK_MODE", "").lower() in {"1", "true", "yes"}
ACCESS_CODE = os.getenv("APP_ACCESS_CODE", "").strip()  # admin (Teacher Mo)
# Optional static colleague list, e.g.
# APP_USERS=[{"name":"Fatima","code":"fat-7K2M"},{"name":"Omar","code":"oma-9X4Q"}]
APP_USERS = os.getenv("APP_USERS", "")
USERS_FILE = "users.json"
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()] or ["*"]
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))
MAX_CHUNKS = 25  # ceil(MAX_UPLOAD_MB / 3MB) must stay below this

app = FastAPI(title="Lesson Sync API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _env_users() -> list:
    try:
        parsed = json.loads(APP_USERS)
        return [u for u in parsed if u.get("name") and u.get("code")]
    except Exception:
        return []


_USERS_CACHE = {"at": 0.0, "users": []}


def _load_users(token: str | None = None) -> list:
    """Users = static APP_USERS env entries + blob-managed colleagues (if storage works)."""
    import time

    if time.time() - _USERS_CACHE["at"] < 30:
        return list(_USERS_CACHE["users"])
    users = {u["code"]: {"name": u["name"], "code": u["code"], "role": "user", "source": "env"}
             for u in _env_users()}
    client = _sync_blob_client()
    if client is not None:
        try:
            res = client.get(f"lesson-sync/{USERS_FILE}", access="private", token=token)
            for u in json.loads(res.content or b"{}").get("users", []):
                if u.get("code"):
                    users[u["code"]] = {"name": u["name"], "code": u["code"],
                                        "role": u.get("role", "user"), "source": "blob",
                                        "added": u.get("added", "")}
        except Exception:
            pass  # no file yet or storage hiccup: env users still work
    values = list(users.values())
    _USERS_CACHE["at"] = time.time()
    _USERS_CACHE["users"] = values
    return list(values)


def _find_user(code: str, token: str | None = None):
    code = (code or "").strip()
    if not code:
        return None
    if ACCESS_CODE and code == ACCESS_CODE:
        return {"name": "Teacher Mo", "role": "admin", "code": code}
    for u in _load_users(token):
        if u["code"] == code:
            return u
    return None


def _check_access(access_code: str, token: str | None = None) -> None:
    """Every endpoint requires a valid admin or colleague code."""
    if _find_user(access_code, token) is None:
        raise HTTPException(401, "Invalid or missing access code.")


def _save_upload(upload: UploadFile, target_dir: str, name: str) -> str:
    path = os.path.join(target_dir, name)
    with open(path, "wb") as out:
        shutil.copyfileobj(upload.file, out)
    if os.path.getsize(path) > MAX_UPLOAD_MB * 1024 * 1024:
        os.remove(path)
        raise HTTPException(413, f"File too large (limit {MAX_UPLOAD_MB} MB).")
    return path


def _blob_client():
    try:
        from vercel.blob import AsyncBlobClient
    except ImportError as exc:
        raise HTTPException(
            501, "Large-file storage is not installed on this server. Please upload a file under 4 MB."
        ) from exc
    if not (os.getenv("BLOB_STORE_ID") or os.getenv("BLOB_READ_WRITE_TOKEN")):
        raise HTTPException(
            501, "Large-file storage is not configured on this server. Please upload a file under 4 MB."
        )
    return AsyncBlobClient()


def _sync_blob_client():
    try:
        from vercel.blob import BlobClient
    except ImportError:
        return None
    if not (os.getenv("BLOB_STORE_ID") or os.getenv("BLOB_READ_WRITE_TOKEN")):
        return None
    try:
        return BlobClient()
    except Exception:
        return None  # e.g. OIDC-only store without a static token


def _blob_token(request: Request | None = None):
    """Blob auth token. On Vercel the OIDC token arrives as the
    x-vercel-oidc-token REQUEST HEADER (see /docs/oidc); locally it is the
    static BLOB_READ_WRITE_TOKEN from backend/.env. Both work with the API."""
    if request is not None:
        oidc = request.headers.get("x-vercel-oidc-token")
        if oidc:
            return oidc
    return os.getenv("BLOB_READ_WRITE_TOKEN") or None


def _delete_parts_sync(blob_dir: str, chunk_count: int, token: str | None = None) -> None:
    client = _sync_blob_client()
    if client is None:
        return
    try:
        client.delete([f"lesson-sync/{blob_dir}/{i:04d}.part" for i in range(chunk_count)], token=token)
    except Exception:
        pass  # best-effort cleanup


def _objectives_text(objectives: str, objectives_file: UploadFile | None, target_dir: str) -> str:
    text = (objectives or "").strip()
    if objectives_file and objectives_file.filename:
        suffix = os.path.splitext(objectives_file.filename)[1].lower()
        path = _save_upload(objectives_file, target_dir, f"objectives{suffix or '.txt'}")
        if suffix in parsers.SUPPORTED_EXTENSIONS:
            material = parsers.parse_file(path)
            blocks = []
            for unit in material.units:
                parts = [unit.title or "", unit.text or ""]
                blocks.append("\n".join(p for p in parts if p))
            text = (text + "\n" + "\n".join(blocks)).strip()
        else:  # plain text file
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = (text + "\n" + fh.read()).strip()
    return text


async def _assemble_from_blob(client, blob_dir: str, chunk_count: int, out_path: str, token: str | None = None) -> None:
    for i in range(chunk_count):
        path = f"lesson-sync/{blob_dir}/{i:04d}.part"
        try:
            res = await client.get(path, access="private", token=token)
        except Exception as exc:
            raise HTTPException(
                400, "Upload session not found or incomplete. Please upload the file again."
            ) from exc
        with open(out_path, "ab") as out:
            out.write(res.content)


# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {
        "status": "ok",
        "mock_mode": MOCK_MODE,
        "gemini_key_present": bool(os.getenv("GEMINI_API_KEY")),
        "gemini_model": os.getenv("GEMINI_MODEL", analyzer.MODEL_DEFAULT),
        "providers_configured": llm.configured_providers(),
        "analyst_route": agents.route_for("analyst"),
        "access_protected": bool(ACCESS_CODE),
        "allowed_origins": ALLOWED_ORIGINS,
        "max_upload_mb": MAX_UPLOAD_MB,
        "large_files_enabled": bool(os.getenv("BLOB_STORE_ID") or os.getenv("BLOB_READ_WRITE_TOKEN")),
        "has_oidc_token": bool(os.getenv("VERCEL_OIDC_TOKEN")),
        "has_blob_store_id": bool(os.getenv("BLOB_STORE_ID")),
    }


def _require_admin(request: Request) -> str | None:
    token = request.headers.get("x-vercel-oidc-token")
    user = _find_user(request.headers.get("x-access-code", ""), token)
    if user is None:
        raise HTTPException(401, "Invalid or missing access code.")
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin access required.")
    return token


@app.get("/login")
def login(request: Request, code: str = ""):
    user = _find_user(code, request.headers.get("x-vercel-oidc-token"))
    if user is None:
        raise HTTPException(401, "Invalid or missing access code.")
    return {"ok": True, "name": user["name"], "role": user.get("role", "user")}


@app.get("/users")
def list_users(request: Request):
    token = _require_admin(request)
    return {"users": _load_users(token)}


@app.post("/users")
async def add_user(request: Request):
    token = _require_admin(request)
    client = _blob_client()
    body = await request.json()
    name = str(body.get("name", "")).strip()
    if not name or len(name) > 60:
        raise HTTPException(400, "Please provide the colleague's name.")
    import secrets as _secrets
    from datetime import date as _date

    slug = "".join(ch for ch in name.lower().split()[0] if ch.isalpha())[:4] or "user"
    users = _load_users(token)
    existing = {u["code"] for u in users}
    code = f"{slug}-{_secrets.token_hex(2).upper()}"
    while code in existing:
        code = f"{slug}-{_secrets.token_hex(2).upper()}"
    users.append({"name": name, "code": code, "role": "user",
                  "added": _date.today().isoformat()})
    try:
        await client.put(f"lesson-sync/{USERS_FILE}",
                         json.dumps({"users": users}).encode(),
                         access="private", content_type="application/json", overwrite=True,
                         token=token)
    except Exception as exc:
        raise HTTPException(501, f"Colleague storage error: {type(exc).__name__}: {exc}") from exc
    _USERS_CACHE["at"] = 0.0
    return {"ok": True, "name": name, "code": code}


@app.delete("/users")
async def remove_user(request: Request, code: str = ""):
    token = _require_admin(request)
    client = _blob_client()
    users = _load_users(token)
    kept = [u for u in users if not (u["code"] == code and u.get("source") == "blob")]
    if len(kept) == len(users):
        raise HTTPException(400, "Colleague not found (static env users can only be removed via APP_USERS).")
    try:
        await client.put(f"lesson-sync/{USERS_FILE}",
                         json.dumps({"users": kept}).encode(),
                         access="private", content_type="application/json", overwrite=True,
                         token=token)
    except Exception as exc:
        raise HTTPException(501, f"Colleague storage error: {type(exc).__name__}: {exc}") from exc
    _USERS_CACHE["at"] = 0.0
    return {"ok": True}


@app.post("/upload-chunk")
async def upload_chunk(request: Request):
    """One ~3MB chunk of a large file. Headers: x-sid, x-index, x-access-code."""
    _check_access(request.headers.get("x-access-code", ""), request.headers.get("x-vercel-oidc-token"))
    blob_token = _blob_token(request)

    sid = request.headers.get("x-sid", "")
    if not sid or len(sid) > 40 or not sid.isalnum():
        raise HTTPException(400, "Invalid upload session id.")
    try:
        index = int(request.headers.get("x-index", ""))
    except ValueError as exc:
        raise HTTPException(400, "Invalid chunk index.") from exc
    if not 0 <= index < MAX_CHUNKS:
        raise HTTPException(400, f"File too large (max {MAX_CHUNKS} chunks).")

    body = await request.body()
    if not body:
        raise HTTPException(400, "Empty chunk.")
    if len(body) > 4 * 1024 * 1024:
        raise HTTPException(400, "Chunk too large.")

    client = _blob_client()
    pathname = f"lesson-sync/{sid}/{index:04d}.part"
    try:
        await client.put(
            pathname,
            body,
            access="private",
            content_type="application/octet-stream",
            overwrite=True,
            token=blob_token,
        )
    except Exception as exc:
        raise HTTPException(502, f"Chunk storage failed: {exc}") from exc
    return {"pathname": pathname, "bytes": len(body)}


@app.post("/analyze")
async def analyze(
    request: Request,
    objectives: str = Form(""),
    gradeLevel: str = Form(""),
    categories: str = Form(""),
    interactive: bool = Form(False),
    accessCode: str = Form(""),
    clientMaterial: str = Form(""),
    blobDir: str = Form(""),
    chunkCount: int = Form(0),
    filename: str = Form("source.pptx"),
    file: UploadFile | None = File(None),
    objectivesFile: UploadFile | None = File(None),
):
    _check_access(accessCode, request.headers.get("x-vercel-oidc-token"))
    blob_token = request.headers.get("x-vercel-oidc-token") or os.getenv("BLOB_READ_WRITE_TOKEN") or None

    request_dir = os.path.join(UPLOAD_DIR, uuid.uuid4().hex[:12])
    os.makedirs(request_dir, exist_ok=True)

    # ---- path A: browser-extracted material (any file size, text only) -----
    material = None
    source_path = None
    if clientMaterial:
        try:
            cm = json.loads(clientMaterial)
            kind = cm.get("kind")
            units_in = cm.get("units")
            if kind not in {"pptx", "ppt"} or not isinstance(units_in, list) or not units_in:
                raise ValueError
        except Exception as exc:
            raise HTTPException(400, "Invalid client-extracted material.") from exc
        if len(units_in) > 400:
            raise HTTPException(400, "Too many slides (max 400).")
        units = []
        for i, u in enumerate(units_in[:400], start=1):
            units.append(
                parsers.Unit(
                    index=i,
                    title=str(u.get("title", ""))[:300],
                    text=str(u.get("text", ""))[:20000],
                    notes=str(u.get("notes", ""))[:5000],
                )
            )
        material = parsers.ParsedMaterial(
            kind=kind, source_name=str(filename or "client deck"), units=units
        )

    # ---- path B/C: blob-assembled or direct upload --------------------------
    if material is None and blobDir:
        if not blobDir.isalnum():
            raise HTTPException(400, "Invalid upload session id.")
        if not 1 <= chunkCount <= MAX_CHUNKS:
            raise HTTPException(400, "Invalid upload session (chunk count).")
        client = _blob_client()
        suffix = os.path.splitext(filename)[1].lower()
        if suffix not in parsers.SUPPORTED_EXTENSIONS:
            raise HTTPException(400, f"Unsupported file type '{suffix}'. Supported: .pptx, .ppt, .pdf, .docx")
        source_path = os.path.join(request_dir, f"source{suffix}")
        await _assemble_from_blob(client, blobDir, chunkCount, source_path, token=blob_token)
    elif material is None:
        if not file or not file.filename:
            raise HTTPException(400, "Please upload the previous-year presentation or worksheet.")
        suffix = os.path.splitext(file.filename)[1].lower()
        if suffix not in parsers.SUPPORTED_EXTENSIONS:
            raise HTTPException(400, f"Unsupported file type '{suffix}'. Supported: .pptx, .ppt, .docx")
        source_path = _save_upload(file, request_dir, f"source{suffix}")

    if source_path is not None and os.path.getsize(source_path) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File too large (limit {MAX_UPLOAD_MB} MB).")

    objectives_text = _objectives_text(objectives, objectivesFile, request_dir)
    if not objectives_text:
        raise HTTPException(400, "Please provide the current-year lesson objectives (text or file).")

    if material is None:
        try:
            material = await run_in_threadpool(parsers.parse_file, source_path)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, f"Could not read the uploaded file: {exc}") from exc

    if MOCK_MODE or not llm.any_provider_configured():
        report = analyzer.mock_report(n_units=min(len(material.units), 6))
        report["_mock"] = True
    else:
        try:
            report = await run_in_threadpool(
                analyzer.analyze_material,
                material.to_prompt_text(),
                material.kind,
                material.source_name,
                objectives_text,
                gradeLevel,
                categories,
                interactive,
            )
        except Exception as exc:
            shutil.rmtree(request_dir, ignore_errors=True)
            if blobDir:
                _delete_parts_sync(blobDir, chunkCount)
            shutil.rmtree(request_dir, ignore_errors=True)
        if blobDir:
            _delete_parts_sync(blobDir, chunkCount, token=blob_token)
        raise HTTPException(502, f"AI analysis failed: {exc}") from exc

    engine = report.get("_engine")
    response = {
        "material_kind": material.kind,
        "n_units": len(material.units),
        "editable_original": material.kind == "pptx",
        "mock": report.get("_mock", False),
        "engine": engine,
        "report": report,
    }

    # For .pptx uploads (edit mode possible), keep the blob parts so /generate
    # can re-assemble the original deck without a second upload.
    if blobDir and not clientMaterial and material.kind == "pptx":
        response["source"] = {"blob_dir": blobDir, "chunk_count": chunkCount, "filename": filename}
        return response

    shutil.rmtree(request_dir, ignore_errors=True)
    if blobDir:  # non-pptx: parts are no longer needed
        _delete_parts_sync(blobDir, chunkCount, token=blob_token)
    return response


@app.post("/illustrate")
async def illustrate(
    request: Request,
    report: str = Form(""),
    accessCode: str = Form(""),
    gradeLevel: str = Form(""),
    categories: str = Form(""),
    maxImages: int = Form(3),
):
    """Generate kid-friendly illustrations for the new gap-filling slides.

    Returns image URLs (hosted by Zhipu, short-lived) the frontend previews and
    then passes back to /generate as `illustrations` for embedding into the deck.
    """
    _check_access(accessCode, request.headers.get("x-vercel-oidc-token"))
    try:
        report_data = analyzer.normalize_report(json.loads(report))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"'report' must be valid JSON: {exc}") from exc

    targets = analyzer.illustratable_slides(report_data, max_images=maxImages)
    if not targets:
        raise HTTPException(400, "No slides to illustrate in this report.")

    mock = MOCK_MODE or not llm.any_provider_configured()
    route = agents.route_for("illustrator")
    illustrations = []
    engine_meta = None
    for t in targets:
        title = t.get("title", "")
        prompt = analyzer.illustration_prompt(title, t.get("content", ""), gradeLevel, categories)
        entry = {"title": title, "after_index": t.get("after_index", t.get("index", 0)), "prompt": prompt}
        if mock:
            entry.update({"image_url": "", "mock": True})
            illustrations.append(entry)
            continue
        try:
            url, eng = await run_in_threadpool(llm.generate_image, prompt, route)
            engine_meta = engine_meta or {**eng, "route": route, "task": "illustrator"}
            entry["image_url"] = url
        except Exception as exc:
            entry.update({"image_url": "", "error": str(exc)[:160]})
        illustrations.append(entry)

    return {"illustrations": illustrations, "mock": mock, "engine": engine_meta}


@app.post("/generate")
async def generate(
    request: Request,
    report: str = Form(""),
    mode: str = Form("edit"),  # "edit" (keep original design) | "rebuild" (fresh deck)
    slideActions: str = Form("{}"),
    includeNewSlides: bool = Form(True),
    accessCode: str = Form(""),
    gradeLevel: str = Form(""),
    categories: str = Form(""),
    interactive: bool = Form(False),
    sourceName: str = Form("previous material"),
    blobDir: str = Form(""),
    chunkCount: int = Form(0),
    filename: str = Form("source.pptx"),
    userName: str = Form(""),
    objectives: str = Form(""),
    designMode: bool = Form(False),  # no reference material: design from scratch
    illustrations: str = Form("[]"),  # [{"title": "...", "url": "https://..."}] to embed
    file: UploadFile | None = File(None),
):
    _check_access(accessCode, request.headers.get("x-vercel-oidc-token"))
    blob_token = request.headers.get("x-vercel-oidc-token") or os.getenv("BLOB_READ_WRITE_TOKEN") or None

    try:
        illos = [
            {"title": str(i.get("title", "")), "url": str(i.get("url", ""))}
            for i in json.loads(illustrations or "[]")
            if isinstance(i, dict) and i.get("title") and i.get("url")
        ]
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"'illustrations' must be valid JSON: {exc}") from exc

    if designMode:
        if not objectives.strip():
            raise HTTPException(400, "Please provide the lesson objectives to design from.")
        if MOCK_MODE or not os.getenv("GEMINI_API_KEY"):
            design = analyzer.design_lesson_mock()
        else:
            try:
                design = await run_in_threadpool(
                    analyzer.design_lesson, objectives, gradeLevel, categories, interactive
                )
            except Exception as exc:
                raise HTTPException(502, f"AI design failed: {exc}") from exc
        report_data = analyzer.normalize_report(
            {
                "title": design.get("title", "Lesson"),
                "objectives": [{"id": f"O{i}", "text": t} for i, t in enumerate(design.get("objectives", []), 1)],
                "slides": design.get("slides", []),
                "coverage": [],
                "gaps": [],
                "summary": f"Lesson designed from scratch for: {objectives.strip()[:200]}",
            }
        )
    else:
        try:
            report_data = analyzer.normalize_report(json.loads(report))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise HTTPException(400, f"'report' must be valid JSON: {exc}") from exc

    try:
        actions = json.loads(slideActions or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"slideActions must be valid JSON: {exc}") from exc
    actions.setdefault("include_new_slides", includeNewSlides)

    if designMode:
        mode = "rebuild"  # scratch design never edits an original deck
    mode = "edit" if mode == "edit" else "rebuild"

    request_dir = os.path.join(UPLOAD_DIR, uuid.uuid4().hex[:12])
    os.makedirs(request_dir, exist_ok=True)

    blob_cleanup: tuple | None = None
    try:
        if mode == "edit":
            if blobDir:
                if not blobDir.isalnum() or not 1 <= chunkCount <= MAX_CHUNKS:
                    raise HTTPException(400, "Invalid upload session.")
                client = _blob_client()
                suffix = os.path.splitext(filename)[1].lower() or ".pptx"
                if suffix != ".pptx":
                    raise HTTPException(400, "Editing the original design requires a .pptx file.")
                source_path = os.path.join(request_dir, f"source{suffix}")
                await _assemble_from_blob(client, blobDir, chunkCount, source_path, token=blob_token)
                blob_cleanup = (blobDir, chunkCount, blob_token)
            else:
                if not file or not file.filename:
                    raise HTTPException(400, "Edit mode needs the original .pptx uploaded.")
                suffix = os.path.splitext(file.filename)[1].lower()
                if suffix != ".pptx":
                    raise HTTPException(400, "Editing the original design requires a .pptx upload — use rebuild mode instead.")
                source_path = _save_upload(file, request_dir, f"source{suffix}")
            out_path = os.path.join(request_dir, "Updated_Lesson_Presentation.pptx")
            await run_in_threadpool(generator.edit_deck, source_path, report_data, {**actions, "illustrations": illos}, out_path)
        else:
            out_path = os.path.join(request_dir, "Updated_Lesson_Presentation.pptx")
            await run_in_threadpool(
                generator.rebuild_deck,
                report_data,
                {
                    "grade_level": gradeLevel,
                    "categories": categories,
                    "interactive": interactive,
                    "source_name": sourceName if not designMode else "designed from scratch",
                    "prepared_for": userName,
                    "design_title": report_data.get("title", ""),
                    "illustrations": illos,
                },
                out_path,
            )
    except HTTPException:
        shutil.rmtree(request_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(request_dir, ignore_errors=True)
        raise HTTPException(500, f"Could not build the updated deck: {exc}") from exc

    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename="Updated_Lesson_Presentation.pptx",
        background=BackgroundTask(_cleanup_after_response, request_dir, blob_cleanup),
    )


def _cleanup_dir(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _cleanup_after_response(request_dir: str, blob_cleanup: tuple | None) -> None:
    _cleanup_dir(request_dir)
    if blob_cleanup:
        _delete_parts_sync(blob_cleanup[0], blob_cleanup[1], token=blob_cleanup[2])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
