"""
Vercel serverless entrypoint for the MyTradeBot licensing API.

Vercel runs this file as the ASGI handler for every request matched by
vercel.json routes.  We add the repo root to sys.path so that the
`backend` package is importable without an installed package.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Make `backend` importable from the repo root ─────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.middleware.cors import CORSMiddleware
from backend.app import create_app, Settings

# ── Build the app ─────────────────────────────────────────────────────────────
_settings = Settings()
app = create_app(_settings)

# ── CORS ──────────────────────────────────────────────────────────────────────
# Allow the desktop client and any future web front-end to call the API.
# In production set ALLOWED_ORIGINS to your exact domain(s).
_origins_env = os.getenv("ALLOWED_ORIGINS", "*")
_origins = [o.strip() for o in _origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
