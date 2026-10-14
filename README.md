# 🎯 Lesson Sync

**An agent that turns last year's lesson material into this year's objective-aligned lesson.**

Upload your previous-year presentation or worksheet, paste the current-year lesson
objectives, and Lesson Sync will:

1. **Read** your old material slide-by-slide (`.pptx`), page-by-page (`.pdf`) or
   section-by-section (`.docx` worksheets)
2. **Analyze** it against your current objectives with Google Gemini
3. **Report** a gap analysis: which objectives are ✅ covered / 🟡 partial / ❌ missing,
   plus **slide-by-slide feedback** (keep / revise / remove) with concrete, actionable
   suggestions
4. **Build** an updated `.pptx` you can download:
   - **✏️ Edit my deck** — keeps your original design/template, rewrites misaligned
     slides in place, deletes off-topic ones and inserts new slides to close the gaps
   - **🆕 Rebuild fresh deck** — generates a brand-new deck (Comic Sans, 36pt,
     max 5 short bullets/slide) from the analysis + your objectives

You stay in control: before generating, you can override the AI's suggested action
on every slide and include/exclude the proposed new slides.

---

## Architecture

```
React + Vite (this repo root)      FastAPI (backend/)
┌──────────────────────────┐  POST /analyze   ┌─────────────────────────────┐
│ 1. upload old material   │ ───────────────► │ parsers.py   pptx/pdf/docx  │
│    + objectives          │                  │ analyzer.py  Gemini JSON    │
│ 2. review gap report     │ ◄─────────────── │              report         │
│ 3. tweak keep/revise/    │                  │ SESSIONS (in-memory)        │
│    remove, choose mode   │  POST /generate  │                             │
│ 4. download updated .pptx│ ───────────────► │ generator.py edit/rebuild   │
└──────────────────────────┘ ◄─────────────── │              .pptx          │
                                              └─────────────────────────────┘
```

## Setup

### 1. Backend

```bash
cd backend
python -m venv .venv                       # or: python -m venv ../.venv to match the run scripts
.venv/Scripts/pip install -r requirements.txt   # Windows
# .venv/bin/pip install -r requirements.txt     # macOS/Linux

cp .env.example .env                       # then edit .env
```

Put your **Google AI Studio key** in `backend/.env` (free at
https://aistudio.google.com/apikey):

```
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-flash     # optional

# If Google is blocked on your network, set a proxy (standard env vars):
# HTTPS_PROXY=http://127.0.0.1:18081
# NO_PROXY=localhost,127.0.0.1
```

No key yet? Set `MOCK_MODE=1` in `.env` to run the whole flow with a canned sample
report — great for trying the UI.

#### Multi-provider AI agents (free tiers) 🧠

The gap analysis runs through a **model router** (`backend/llm.py` +
`backend/agents.py`) with a fallback chain per task. Any provider with an API key
is used; the others are skipped, and the app reports which engine produced each
report (green chip in the UI and `GET /health`).

Add any of these free keys to `backend/.env` (or your host's env vars):

| Provider | Get a key | Notes |
|---|---|---|
| OpenRouter | https://openrouter.ai/keys | Many `:free` models (NVIDIA Nemotron, Gemma, …); ~50 req/day free |
| NVIDIA NIM | https://build.nvidia.com | Free credits, fast Llama/Nemotron models |
| Groq | https://console.groq.com/keys | Free tier, very fast open models |
| Gemini | https://aistudio.google.com/apikey | Already wired (final fallback) |

Default analyst chain: `openrouter:nvidia/nemotron-3.5-lightning:free` →
`openrouter:openrouter/free` → `nvidia:meta/llama-3.3-70b-instruct` →
`gemini:gemini-2.5-flash`. Free model IDs rotate — **override without touching
code**:

```bash
# same chain for every task
LLM_ROUTE=openrouter:deepseek/deepseek-chat-v3.1:free,nvidia:meta/llama-3.3-70b-instruct
# or per-task (analyst / rewriter / worksheet / illustrator)
AGENT_ANALYST=openrouter:openrouter/free,gemini:gemini-2.5-flash
```

`custom:` is a wildcard for any OpenAI-compatible endpoint (e.g. local Ollama:
`LLM_CUSTOM_BASE_URL=http://localhost:11434/v1`, then
`LLM_ROUTE=custom:llama3.1`).

Run it (or double-click `run_backend.bat` on Windows):

```bash
.venv/Scripts/python.exe -m uvicorn main:app --reload --port 8000
```

### 2. Frontend

```bash
npm install
npm run dev          # http://localhost:5173
```

If the backend runs somewhere else, create a `.env` in the project root with
`VITE_API_URL=http://your-backend:8000`.

(Or double-click `run_frontend.bat` on Windows.)

---

## Testing on a laptop and a phone 📱

**Quick way (no deployment):** double-click **`run_dev_lan.bat`** — it starts both
servers and prints the phone URL. It looks like:

```
Laptop URL :  http://localhost:5173
Phone URL  :  http://192.168.x.x:5173   <- your laptop's Wi-Fi IP
```

1. **Laptop** — open the localhost URL, upload a deck, run an analysis, download the PPTX.
2. **Phone** — connect it to the **same Wi-Fi**, open the `Phone URL`, and repeat.
   In dev mode the frontend proxies API calls through itself (Vite), so the phone
   needs **only that one URL** — no CORS or `VITE_API_URL` setup.
3. **First-time Windows firewall prompt** — click *Allow* for Node and Python
   (private networks). No prompt? Test with the firewall temporarily off, or add
   inbound rules for ports 5173/8000.
4. **Phone can't connect?** Your Wi-Fi probably has *AP/client isolation* on
   (common on guest and office networks). Easiest fix: turn on the **phone's
   personal hotspot**, connect the **laptop** to it, and use the URL `run_dev_lan.bat`
   prints — the roles flip but the setup works identically.
5. **Preview phone layout on the laptop first:** Chrome DevTools → device toolbar
   (Ctrl+Shift+M) → pick a phone profile.

**Anywhere test (the deployed URL):** once you deploy (Render + Vercel, see below),
open your Vercel URL on any device, enter your `APP_ACCESS_CODE` in the 🔒 field,
and run the same checklist. Downloads land in the phone's Files/Downloads app and
open in PowerPoint/mobile Office fine.

## How to use

1. **Upload** the previous-year presentation/worksheet (`.pptx`, `.pdf`, `.docx`)
2. **Type or attach** the current-year objectives (one per line)
3. Pick **grade level** and **categories**, toggle games/activities
4. Click **🔍 Analyze & Find Gaps** → review the report:
   - objective coverage with evidence,
   - per-slide verdicts and feedback (adjust the keep/revise/remove dropdowns as you like),
   - proposed new slides that close the gaps
5. Choose **Edit my deck** (needs `.pptx` upload) or **Rebuild fresh deck**, then
   **⬇️ Download Updated Presentation**

## Notes & limits

- **Edit mode** rewrites title/body placeholders of existing slides; slides whose
  content lives in plain text boxes (not placeholders) are left untouched by design
  so your design never breaks.
- Deleted slides are really deleted; new slides reuse the layout of the slide they
  follow, so the theme stays consistent.
- **Fully stateless**: `/generate` re-receives the report (and the original `.pptx`
  for edit mode), so the backend can restart, scale, or redeploy without losing
  anything and works on any stateless host.
- Optional shared-secret gate: set `APP_ACCESS_CODE` on the backend and enter it in
  the 🔒 field once per device (stored in local storage).
- The Gemini call uses strict `application/json` responses and the report is
  re-validated server-side (`analyzer.normalize_report`).

## Deploying to the world (phone-ready) 🌍

**Fastest path — one click per platform:**

### 1. Backend + frontend both live on Vercel (chosen setup)

The FastAPI backend runs as a **Python serverless function** inside the same Vercel
project (`api/index.py`; `/analyze`, `/generate`, `/health` are rewritten to it),
so there is one platform, one URL, no CORS and no card required.

Already deployed for this repo: **https://lesson-sync-henna.vercel.app**

To redeploy after changes:

```bash
vercel --prod
```

Environment variables (Project → Settings → Environment Variables, or CLI):

| Var | Value |
|---|---|
| `GEMINI_API_KEY` | your Google AI Studio key |
| `APP_ACCESS_CODE` | the shared password users type into the 🔒 field |
| `MAX_UPLOAD_MB` | `50` (direct uploads; big decks extract text in-browser) |

### 2. About file sizes

There is **no meaningful upload limit for .pptx/.ppt**: decks over ~3.5 MB have
their slide text extracted **in your browser** (nothing but text is sent), so an
11 MB or 50 MB deck analyses in seconds. Direct uploads of small files, PDFs and
.docx worksheets stay capped at ~4.5 MB by the platform — larger PDFs will ask
you to convert or split them.

The app is **stateless** (no server-side sessions), so any stateless host works —
Render, Fly, Docker, or your own server.

### 1. Push to GitHub

```bash
cd lesson-sync
git init && git add . && git commit -m "Lesson Sync"
git remote add origin https://github.com/<you>/lesson-sync.git
git push -u origin main
```

Your `backend/.env` is gitignored — the API key never leaves your machine.

### 2. Backend on Render

- Render dashboard → **New + → Blueprint** → select the repo (it reads `render.yaml`),
  or create a **Web Service** manually:
  - Root directory: `backend`
  - Build: `pip install -r requirements.txt`
  - Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Set environment variables (Render dashboard, not a file):
  - `GEMINI_API_KEY` — your Google AI Studio key
  - `APP_ACCESS_CODE` — **strongly recommended**: a password of your choice; without
    it, anyone who finds the URL can burn your Gemini quota
  - `ALLOWED_ORIGINS` — `https://<your-app>.vercel.app` (no trailing slash)
- Verify: open `https://<service>.onrender.com/health` → should return JSON.

### 3. Frontend on Vercel

- Vercel → **Add New → Project** → import the repo (framework auto-detects Vite)
- Environment variable: `VITE_API_URL` = `https://<service>.onrender.com`
- Deploy → open the URL on **any device, including your phone** — the UI is responsive.

### 4. Log in from any device

On first use, enter your `APP_ACCESS_CODE` in the 🔒 Access code field (or you'll get
a 401). It's remembered in the browser's local storage.

### Notes for the free tiers

- **Render free** spins the service down after ~15 min idle — the first analysis after
  a break takes ~50 s (cold start). Any visit "wakes" it again.
- Gemini free tier: keep decks under ~40 slides per analysis for snappy results.
- All traffic is HTTPS automatically (Vercel + Render), so uploads from your phone are
  encrypted.
- If you make the app public without an access code, at least set `ALLOWED_ORIGINS`
  so other sites can't call your API from their pages.

## Multi-user & design-from-scratch

### 🎨 Illustrations (Zhipu CogView, China-direct)

After a gap analysis, click **🎨 Generate illustrations** — the `illustrator` agent
turns each new gap-filling slide into a kid-friendly, text-free cartoon
(`cogview-3-flash`, free tier) and shows a preview gallery. Untick any you
don't want; ticked images are downloaded and embedded into the new slides when
you build the deck. Works without VPN (the only always-on image provider);
upgrade to `cogview-4-250304` via `AGENT_ILLUSTRATOR=zhipu:cogview-4-250304`.

- **Named colleague access**: sign in with your name + your personal access code.
  The admin (first code, `APP_ACCESS_CODE`) gets a **👥 Colleague access** panel —
  type a colleague's name, click **Generate code**, share it. Codes are stored in
  the project's Blob store; a static fallback list can be set via the
  `APP_USERS` env var (`[{"name":"Fatima","code":"fat-7K2M"}, ...]`).
- **No reference material?** On the first screen choose **"No — design from
  scratch"**: the AI designs a complete lesson deck from your objectives alone
  (title, warm-up, teaching segments, practice, games) and downloads it as
  `Designed_Lesson.pptx`.
- Reports and decks are stamped **"Prepared for <name>"**.

**Note on large PDFs**: .pptx/.ppt have no size limit (text is extracted in the
browser). PDF/.docx still cap at ~4.5 MB — larger ones ask you to convert.

## Tests

Backend end-to-end tests run fully offline (mock mode):

```bash
cd backend
../.venv/Scripts/python.exe test_backend.py     # Windows
# .venv/bin/python test_backend.py              # macOS/Linux
```

Covers: all three parsers, the API round-trip, edit-mode semantics (revise in place,
delete, insert at position, design preservation, user overrides), rebuild styling,
PDF parsing and error handling.
