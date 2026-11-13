"""
Vercel serverless entrypoint for the MyTradeBot licensing API.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Make `backend` importable from the repo root
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from fastapi.middleware.cors import CORSMiddleware
    from backend.app import create_app, Settings

    _settings = Settings()
    app = create_app(_settings)

    _origins_env = os.getenv("ALLOWED_ORIGINS", "*")
    _origins = [o.strip() for o in _origins_env.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    log.info("MyTradeBot API started (env=%s db=%s)", _settings.environment, _settings.database_url)
except Exception:
    log.exception("MyTradeBot API failed to start")
    raise
