"""Vercel Python function: serves the Lesson Sync FastAPI app.

Serverless notes handled here:
  - backend/ is added to sys.path so `import main/analyzer/generator/parsers` works
  - UPLOAD_DIR defaults to /tmp (the only writable location on Vercel)
"""
import os
import sys

# Serverless safety: on Vercel, never inherit local proxy settings or local .env
# files — they leak from the upload bundle and break outbound HTTPS (functions
# must go direct to the internet). VERCEL is set in all Vercel runtimes.
if os.getenv("VERCEL"):
    for _k in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        os.environ.pop(_k, None)
else:
    from dotenv import load_dotenv  # local dev: backend/.env provides the key/proxy

    load_dotenv()

_API_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.join(os.path.dirname(_API_DIR), "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("UPLOAD_DIR", os.path.join("/tmp", "lesson-sync"))

from main import app  # noqa: E402  (FastAPI app from backend/main.py)

# Vercel's Python runtime expects an ASGI app named `app` at module level.
