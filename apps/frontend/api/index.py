"""Vercel runtime configuration for the static MyTradeBot portal.

The public frontend is hosted by Vercel. The licensing API runs independently
on Render and is never deployed by this Vercel project.
"""
from __future__ import annotations

import json
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

app = FastAPI(title="MyTradeBot Portal Configuration")


@app.get("/")
@app.get("/config.js")
@app.get("/api/index")
def frontend_config() -> Response:
    api_url = os.getenv("MYTRADEBOT_API_URL", "").strip().rstrip("/")
    if not api_url.startswith("https://"):
        raise HTTPException(
            status_code=500,
            detail="MYTRADEBOT_API_URL must be the HTTPS URL of the Render API",
        )
    script = "window.MYTRADEBOT_API_URL=" + json.dumps(api_url) + ";\n"
    return Response(
        content=script,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )
