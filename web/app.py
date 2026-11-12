"""MyTradeBot – FastAPI web server. Run via web_main.py."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from queue import Empty, Full, Queue
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from backend.app import app as account_api
from core.database import Database
from core.licensing import LicenseManager, device_id
from core.mt5_bridge import MT5Bridge
from core.risk import LotSizeMode, RiskEngine, RiskSettings
from core.strategies import build_strategy
from core.trading_engine import TradingConfig, TradingEngine
from web.news import NewsDataError, get_nfp_cpi_events

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

app = FastAPI(title="MyTradeBot")
app.mount("/account-api", account_api)


@app.middleware("http")
async def apply_browser_security_headers(request: Request, call_next):
    if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return JSONResponse(
            status_code=421,
            content={"detail": "MyTradeBot control is restricted to this computer"},
        )
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
        expected_origin = f"{request.url.scheme}://{request.headers.get('host', '').lower()}"
        if origin.rstrip("/").lower() != expected_origin:
            return JSONResponse(
                status_code=403,
                content={"detail": "Cross-origin control requests are not allowed"},
            )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; "
        "frame-ancestors 'none'; worker-src 'self'"
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ── Shared state ──────────────────────────────────────────────────────────────
bridge = MT5Bridge()
db = Database()
LOCAL_ACCOUNT_API_URL = os.getenv(
    "MYTRADEBOT_LICENSE_API_URL",
    "http://127.0.0.1:8000/account-api",
).rstrip("/")
if not db.get_setting("license_api_url", ""):
    db.set_setting("license_api_url", LOCAL_ACCOUNT_API_URL)
_engine: TradingEngine | None = None
_engine_thread: threading.Thread | None = None
_engine_stop = threading.Event()
_log_queue: Queue[str] = Queue(maxsize=200)
_bot_status = {"running": False, "run": 0, "status": "Stopped"}
_log = logging.getLogger(__name__)


def _load_risk() -> RiskEngine:
    d = RiskSettings()
    try:
        vals = {n: db.get_setting(n, getattr(d, n)) for n in RiskSettings.__dataclass_fields__}
        vals["lot_size_mode"] = LotSizeMode(vals["lot_size_mode"])
        return RiskEngine(RiskSettings(**vals))
    except (TypeError, ValueError) as exc:
        raise ValueError("Saved risk settings are invalid; correct and save them before starting the bot") from exc


def _push_log(msg: str, level: str = "info") -> None:
    entry = json.dumps({"t": datetime.now().strftime("%H:%M:%S"), "m": msg, "l": level})
    try:
        _log_queue.put_nowait(entry)
    except Full:
        _log.warning("Web log queue is full; dropping an engine log event")


def _run_engine_loop(
    engine: TradingEngine,
    stop: threading.Event,
    auto_restart: bool = False,
    engine_factory=None,
) -> None:
    global _engine
    while not stop.is_set():
        try:
            for event in engine.tick():
                _push_log(event.message, event.level)
                if event.level == "halt":
                    _bot_status["running"] = False
                    if not auto_restart or engine_factory is None:
                        _bot_status["status"] = event.message
                        stop.set()
                        return
                    _bot_status["status"] = f"{event.message}; restarting shortly"
                    _push_log("Run target reached; next run starts in two seconds", "info")
                    if stop.wait(2.0):
                        return
                    try:
                        engine = engine_factory()
                    except ValueError as exc:
                        _bot_status["running"] = False
                        _bot_status["status"] = f"Auto-restart failed: {exc}"
                        stop.set()
                        _push_log(_bot_status["status"], "error")
                        return
                    _engine = engine
                    _bot_status["run"] += 1
                    _bot_status["running"] = True
                    _bot_status["status"] = f"Run #{_bot_status['run']} — scanning"
                    _push_log(f"Run #{_bot_status['run']} started", "info")
                    break
        except Exception as exc:
            _push_log(f"Engine error: {exc}", "error")
        stop.wait(1.0)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


@app.get("/manifest.webmanifest")
def pwa_manifest():
    return FileResponse(
        BASE / "static" / "manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/icon.svg")
def pwa_icon():
    return FileResponse(BASE / "static" / "icon.svg", media_type="image/svg+xml")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(
        BASE / "static" / "icon.svg",
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=604800"},
    )


@app.get("/icons/{icon_name}")
def pwa_icon_png(icon_name: str):
    icons = {
        "icon-192.png": "icon-192.png",
        "icon-512.png": "icon-512.png",
        "apple-touch-icon.png": "apple-touch-icon.png",
    }
    if icon_name not in icons:
        raise HTTPException(status_code=404, detail="App icon not found")
    return FileResponse(
        BASE / "static" / "icons" / icons[icon_name],
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@app.get("/service-worker.js")
def pwa_service_worker():
    return FileResponse(
        BASE / "static" / "service-worker.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@app.get("/api/health")
def health():
    return {"status": "ok", "trading_host": "local"}


@app.get("/api/device-id")
def trading_device_id():
    return {"device_id": device_id()}


@app.get("/api/news/nfp-cpi")
def nfp_cpi_news():
    try:
        return get_nfp_cpi_events(os.getenv("TRADING_ECONOMICS_API_KEY", ""))
    except NewsDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ── Account ───────────────────────────────────────────────────────────────────

@app.get("/api/account")
def get_account():
    if not bridge.is_connected:
        return {"connected": False}
    a = bridge.get_account_info()
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_pnl = db.realized_profit_between(day_start.isoformat(), now.isoformat())
    run_pnl = 0.0
    if _engine and _engine._run_start:
        run_pnl = db.realized_profit_between(_engine._run_start.isoformat(), now.isoformat())
    return {
        "connected": True,
        "login": a.login, "name": a.name, "server": a.server,
        "broker": a.broker, "currency": a.currency,
        "balance": round(a.balance, 2), "equity": round(a.equity, 2),
        "floating": round(a.equity - a.balance, 2),
        "free_margin": round(a.free_margin, 2),
        "today_pnl": round(today_pnl, 2),
        "run_pnl": round(run_pnl, 2),
    }


# ── Connection ────────────────────────────────────────────────────────────────

class ConnectRequest(BaseModel):
    login: int = 0
    password: str = ""
    server: str = ""
    path: str = ""


@app.post("/api/connect")
def connect(req: ConnectRequest):
    if req.login:
        db.set_setting("mt5_login", str(req.login))
    if req.server:
        db.set_setting("mt5_server", req.server)
    ok = bridge.connect(path=req.path, login=req.login,
                        password=req.password, server=req.server)
    if ok:
        _push_log(f"Connected to MT5 — login={req.login} server={req.server}", "info")
    else:
        _push_log("MT5 connection failed", "error")
    return {"ok": ok}


@app.post("/api/disconnect")
def disconnect():
    _stop_bot_internal()
    bridge.disconnect()
    _push_log("Disconnected from MT5", "warning")
    return {"ok": True}


# ── Symbols ───────────────────────────────────────────────────────────────────

@app.get("/api/symbols")
def get_symbols():
    return {"symbols": bridge.get_symbols()[:500] if bridge.is_connected else []}


# ── Bot control ───────────────────────────────────────────────────────────────

class BotStartRequest(BaseModel):
    symbol: str
    strategy: str = "Combined"
    run_profit_target: float = 50.0
    min_confidence: float = 0.80
    timeframe_minutes: int = 15
    higher_timeframe_minutes: int = 60
    auto_restart: bool = True


@app.post("/api/bot/start")
def start_bot(req: BotStartRequest):
    global _engine, _engine_thread, _engine_stop

    if not bridge.is_connected:
        return {"ok": False, "error": "Not connected to MT5"}
    if _bot_status["running"]:
        return {"ok": False, "error": "Bot already running"}
    account_login = bridge.get_account_info().login
    license_manager = LicenseManager(db, db.get_setting("license_api_url", ""))
    license_status = license_manager.validate(account_login)
    if not license_status.allowed:
        return {"ok": False, "error": license_status.message}

    try:
        cfg = TradingConfig(
            symbol=req.symbol,
            timeframe_minutes=req.timeframe_minutes,
            higher_timeframe_minutes=req.higher_timeframe_minutes,
            run_profit_target=req.run_profit_target,
            min_confidence=req.min_confidence,
        )
        cfg.validate()
        strategy_name = req.strategy
        build_strategy(strategy_name)

        def create_engine() -> TradingEngine:
            return TradingEngine(
                bridge, db, _load_risk(), build_strategy(strategy_name), cfg,
                license_checker=lambda: license_manager.validate(account_login),
            )

        _engine = create_engine()
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    _engine_stop = threading.Event()
    _engine_thread = threading.Thread(
        target=_run_engine_loop,
        args=(_engine, _engine_stop, req.auto_restart, create_engine),
        daemon=True,
    )
    _bot_status["running"] = True
    _bot_status["run"] += 1
    _bot_status["status"] = f"Run #{_bot_status['run']} — scanning"
    _engine_thread.start()
    _push_log(
        f"Run #{_bot_status['run']} started | {req.strategy} on {req.symbol} "
        f"| target: {req.run_profit_target:.2f} | conf: {req.min_confidence*100:.0f}%",
        "info",
    )
    return {"ok": True, "run": _bot_status["run"]}


@app.post("/api/bot/stop")
def stop_bot():
    _stop_bot_internal()
    _push_log("Bot stopped by user", "warning")
    return {"ok": True}


def _stop_bot_internal():
    global _engine, _engine_thread
    if _engine_stop:
        _engine_stop.set()
    _bot_status["running"] = False
    _bot_status["status"] = "Stopped"
    _engine = None
    _engine_thread = None


@app.get("/api/bot/status")
def bot_status():
    return _bot_status


@app.post("/api/bot/emergency")
def emergency_stop():
    _stop_bot_internal()
    results = bridge.close_all_positions()
    closed = sum(1 for r in results if r.success)
    _push_log(f"EMERGENCY STOP — closed {closed} position(s)", "error")
    return {"ok": True, "closed": closed}


# ── Positions ─────────────────────────────────────────────────────────────────

@app.get("/api/positions")
def get_positions():
    return {"positions": [
        {"ticket": p.ticket, "symbol": p.symbol, "type": p.order_type,
         "volume": p.volume, "open_price": p.open_price,
         "profit": round(p.profit, 2)}
        for p in bridge.get_positions()
    ]}


@app.post("/api/positions/{ticket}/close")
def close_position(ticket: int):
    pos = next((p for p in bridge.get_positions() if p.ticket == ticket), None)
    if not pos:
        return {"ok": False, "error": "Position not found"}
    r = bridge.close_position(pos)
    _push_log(f"Close ticket={ticket}: {'OK' if r.success else r.message}",
              "info" if r.success else "error")
    return {"ok": r.success, "message": r.message}


# ── History & performance ─────────────────────────────────────────────────────

@app.get("/api/history")
def get_history(symbol: str = "", order_type: str = ""):
    rows = db.get_filtered_trades(symbol, order_type, limit=200)
    return {"trades": rows}


@app.get("/api/performance")
def get_performance():
    s = db.performance_summary()
    s["profit_factor"] = s["profit_factor"] if s["profit_factor"] is not None else 0
    s["equity_curve"] = [{"t": t, "v": v} for t, v in s["equity_curve"]]
    return s


# ── Settings ──────────────────────────────────────────────────────────────────

@app.get("/api/settings")
def get_settings():
    d = RiskSettings()
    settings = {n: db.get_setting(n, getattr(d, n)) for n in RiskSettings.__dataclass_fields__}
    settings["license_api_url"] = db.get_setting("license_api_url", LOCAL_ACCOUNT_API_URL)
    return settings


class SettingsSaveRequest(BaseModel):
    lot_size_mode: str = "risk_percent"
    risk_percent_per_trade: float = 0.5
    fixed_lot: float = 0.01
    max_open_positions: int = 1
    max_trades_per_day: int = 3
    cooldown_seconds: int = 900
    max_daily_loss_percent: float = 2.0
    max_drawdown_percent: float = 8.0
    max_weekly_loss_percent: float = 5.0
    max_spread_points: float = 30.0
    minimum_free_margin: float = 100.0
    license_api_url: str = ""


@app.post("/api/settings")
def save_settings(req: SettingsSaveRequest):
    vals = req.model_dump()
    try:
        risk_values = {
            key: value
            for key, value in vals.items()
            if key in RiskSettings.__dataclass_fields__
        }
        s = RiskSettings(
            **{
                **risk_values,
                "lot_size_mode": LotSizeMode(risk_values["lot_size_mode"]),
            }
        )
        s.validate()
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    license_api_url = vals["license_api_url"].strip().rstrip("/")
    if license_api_url and not license_api_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        return {"ok": False, "error": "Use HTTPS for remote licensing services."}
    for k, v in risk_values.items():
        db.set_setting(k, v)
    db.set_setting("license_api_url", license_api_url or LOCAL_ACCOUNT_API_URL)
    return {"ok": True}


# ── SSE log stream ────────────────────────────────────────────────────────────

@app.get("/api/logs/stream")
async def log_stream():
    async def generator() -> AsyncGenerator[str, None]:
        while True:
            try:
                entry = await asyncio.to_thread(_log_queue.get, True, 15.0)
                yield f"data: {entry}\n\n"
            except Empty:
                yield "data: {\"t\":\"\",\"m\":\"\",\"l\":\"ping\"}\n\n"
    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
