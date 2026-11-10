"""FastAPI licensing backend; PostgreSQL in production, SQLite only for local tests."""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Generator
from uuid import uuid4

import bcrypt
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from backend.payments import (ManualProvider, MobileMoneyCallbackProvider,
                              PaymentProvider, StripeCheckoutProvider)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Settings:
    def __init__(self) -> None:
        self.environment = os.getenv("MYTRADEBOT_ENV", "development")
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./mytradebot-licenses.db")
        self.jwt_secret = os.getenv("JWT_SECRET", "development-only-change-me")
        self.license_secret = os.getenv("LICENSE_SIGNING_SECRET", self.jwt_secret)
        self.admin_email = os.getenv("ADMIN_EMAIL", "admin@mytradebot.local").lower()
        self.admin_bootstrap_token = os.getenv(
            "ADMIN_BOOTSTRAP_TOKEN", "development-only-bootstrap-token"
        )
        self.rate_limit_per_minute = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
        self.payment_provider = os.getenv("PAYMENT_PROVIDER", "manual")
        if self.environment == "production":
            insecure = (not self.database_url.startswith("postgresql") or self.payment_provider == "manual" or
                        len(self.jwt_secret) < 32 or len(self.license_secret) < 32 or
                        len(self.admin_bootstrap_token) < 32 or
                        len({self.jwt_secret, self.license_secret, self.admin_bootstrap_token}) < 3 or
                        self.jwt_secret == "development-only-change-me")
            if insecure:
                raise RuntimeError(
                    "Production requires PostgreSQL, three distinct 32+ character secrets, "
                    "and a configured payment provider"
                )


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class LoginRequest(RegisterRequest):
    pass


class SubscriptionRequest(BaseModel):
    plan: str = Field(pattern="^(monthly|annual)$")
    mt5_account: str = Field(min_length=3, max_length=32, pattern=r"^[0-9]+$")
    device_id: str = Field(min_length=16, max_length=256)


class LicenseValidationRequest(BaseModel):
    mt5_account: str = Field(min_length=3, max_length=32, pattern=r"^[0-9]+$")
    device_id: str = Field(min_length=16, max_length=256)


class RevokeRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class Database:
    """Small DB adapter; production URL uses psycopg, local/test uses SQLite."""
    def __init__(self, url: str) -> None:
        self.url = url
        self.is_sqlite = url.startswith("sqlite:///")
        self.path = url.removeprefix("sqlite:///") if self.is_sqlite else ""
        self.initialize()

    @contextmanager
    def connection(self) -> Generator:
        if self.is_sqlite:
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback(); raise
            finally:
                conn.close()
            return
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary] to use PostgreSQL") from exc
        with psycopg.connect(self.url, row_factory=psycopg.rows.dict_row) as conn:
            yield conn

    def initialize(self) -> None:
        identity = "TEXT PRIMARY KEY" if self.is_sqlite else "UUID PRIMARY KEY"
        bool_type = "INTEGER" if self.is_sqlite else "BOOLEAN"
        bool_default = "0" if self.is_sqlite else "FALSE"
        with self.connection() as conn:
            conn.execute(f"""CREATE TABLE IF NOT EXISTS users (
                id {identity}, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
                is_admin {bool_type} NOT NULL DEFAULT {bool_default}, created_at TEXT NOT NULL)""")
            conn.execute(f"""CREATE TABLE IF NOT EXISTS licenses (
                id {identity}, user_id TEXT NOT NULL, plan TEXT NOT NULL, mt5_account TEXT NOT NULL,
                device_hash TEXT NOT NULL, status TEXT NOT NULL, expires_at TEXT NOT NULL,
                revoked_reason TEXT, created_at TEXT NOT NULL, UNIQUE(mt5_account, device_hash))""")

    def one(self, sql: str, params: tuple = ()):
        with self.connection() as conn:
            return conn.execute(self._sql(sql), params).fetchone()

    def all(self, sql: str, params: tuple = ()):
        with self.connection() as conn:
            return conn.execute(self._sql(sql), params).fetchall()

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self.connection() as conn:
            conn.execute(self._sql(sql), params)

    def _sql(self, sql: str) -> str:
        """sqlite uses qmark parameters while psycopg uses format parameters."""
        return sql if self.is_sqlite else sql.replace("?", "%s")


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


bearer = HTTPBearer()


class RateLimiter:
    """Simple process-local limiter; use a reverse proxy/shared limiter when scaled."""
    def __init__(self, limit: int) -> None:
        self.limit, self.hits = limit, {}

    def check(self, key: str) -> None:
        now = utc_now()
        window = now - timedelta(minutes=1)
        entries = [stamp for stamp in self.hits.get(key, []) if stamp > window]
        if len(entries) >= self.limit:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests; retry in one minute")
        entries.append(now)
        self.hits[key] = entries


def device_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def row_value(row, key: str):
    return row[key]


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or Settings()
    database = database or Database(settings.database_url)
    app = FastAPI(title="MyTradeBot Licensing API", version="0.1.0")
    limiter = RateLimiter(settings.rate_limit_per_minute)
    payment_provider = _payment_provider(settings)

    @app.middleware("http")
    async def limit_requests(request: Request, call_next):
        client = request.client.host if request.client else "unknown"
        limiter.check(f"{client}:{request.url.path}")
        return await call_next(request)

    def issue_access_token(user) -> str:
        return jwt.encode({"sub": str(row_value(user, "id")), "email": row_value(user, "email"),
                           "admin": bool(row_value(user, "is_admin")), "exp": utc_now() + timedelta(hours=12)},
                          settings.jwt_secret, algorithm="HS256")

    def current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
        try:
            payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"])
        except jwt.InvalidTokenError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired access token") from exc
        user = database.one("SELECT * FROM users WHERE id=?", (payload["sub"],))
        if not user:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists")
        return user

    def admin_user(user=Depends(current_user)):
        if not bool(row_value(user, "is_admin")):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required")
        return user

    @app.get("/health")
    def health():
        database.one("SELECT 1")
        return {"status": "ok"}

    @app.post("/auth/register", status_code=status.HTTP_201_CREATED)
    def register(
        request: RegisterRequest,
        admin_bootstrap_token: str | None = Header(default=None, alias="X-Admin-Bootstrap-Token"),
    ):
        email = request.email.lower()
        if email == settings.admin_email:
            if (
                not settings.admin_bootstrap_token
                or not admin_bootstrap_token
                or not secrets.compare_digest(admin_bootstrap_token, settings.admin_bootstrap_token)
            ):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator bootstrap authorization required")
            if database.one("SELECT id FROM users WHERE is_admin=?", (True,)):
                raise HTTPException(status.HTTP_409_CONFLICT, "Administrator account has already been created")
        if database.one("SELECT id FROM users WHERE email=?", (email,)):
            raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
        user_id = str(uuid4())
        database.execute("INSERT INTO users (id,email,password_hash,is_admin,created_at) VALUES (?,?,?,?,?)",
                         (user_id, email, _hash_password(request.password),
                          int(email == settings.admin_email) if database.is_sqlite else email == settings.admin_email,
                          utc_now().isoformat()))
        user = database.one("SELECT * FROM users WHERE id=?", (user_id,))
        return {"access_token": issue_access_token(user), "token_type": "bearer"}

    @app.post("/auth/login")
    def login(request: LoginRequest):
        user = database.one("SELECT * FROM users WHERE email=?", (request.email.lower(),))
        if not user or not _verify_password(request.password, row_value(user, "password_hash")):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
        return {"access_token": issue_access_token(user), "token_type": "bearer"}

    @app.post("/subscriptions", status_code=status.HTTP_201_CREATED)
    def create_subscription(request: SubscriptionRequest, user=Depends(current_user)):
        existing = database.one("SELECT id FROM licenses WHERE mt5_account=? AND device_hash=?", (request.mt5_account, device_hash(request.device_id)))
        if existing:
            raise HTTPException(status.HTTP_409_CONFLICT, "This MT5 account is already bound to this device")
        days = 365 if request.plan == "annual" else 31
        license_id = str(uuid4())
        expires = utc_now() + timedelta(days=days)
        checkout = payment_provider.create_checkout(license_id, request.plan, row_value(user, "email"))
        is_manual = checkout.provider == "manual"
        database.execute("INSERT INTO licenses (id,user_id,plan,mt5_account,device_hash,status,expires_at,created_at) VALUES (?,?,?,?,?,?,?,?)",
                         (license_id, row_value(user, "id"), request.plan, request.mt5_account, device_hash(request.device_id),
                          "active" if is_manual else "pending", expires.isoformat(), utc_now().isoformat()))
        return {"license_id": license_id, "status": "active" if is_manual else "pending_payment",
                "expires_at": expires.isoformat(), "payment_provider": checkout.provider,
                "checkout_url": checkout.checkout_url}

    @app.post("/payments/webhook/{provider_name}")
    async def payment_webhook(provider_name: str, request: Request, stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
                              mobile_signature: str | None = Header(default=None, alias="X-Payment-Signature")):
        if provider_name != payment_provider.name:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment provider not configured")
        license_id = payment_provider.verify_callback(await request.body(), stripe_signature or mobile_signature)
        if not license_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Payment callback rejected")
        database.execute("UPDATE licenses SET status='active' WHERE id=? AND status='pending'", (license_id,))
        return {"accepted": True}

    @app.post("/licenses/validate")
    def validate_license(request: LicenseValidationRequest):
        license_row = database.one("SELECT * FROM licenses WHERE mt5_account=? AND device_hash=?", (request.mt5_account, device_hash(request.device_id)))
        if not license_row or row_value(license_row, "status") != "active" or datetime.fromisoformat(row_value(license_row, "expires_at")) <= utc_now():
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No active license for this account and device")
        expiry = datetime.fromisoformat(row_value(license_row, "expires_at"))
        signed = jwt.encode({"license_id": str(row_value(license_row, "id")), "mt5_account": request.mt5_account,
                             "device_hash": device_hash(request.device_id), "exp": min(expiry, utc_now() + timedelta(hours=6))},
                            settings.license_secret, algorithm="HS256")
        return {"valid": True, "license_token": signed, "expires_at": expiry.isoformat()}

    @app.get("/admin/dashboard", response_class=HTMLResponse)
    def dashboard(user=Depends(admin_user)):
        users_count = len(database.all("SELECT id FROM users"))
        active = len(database.all("SELECT id FROM licenses WHERE status='active'"))
        pending = len(database.all("SELECT id FROM licenses WHERE status='pending'"))
        revoked = len(database.all("SELECT id FROM licenses WHERE status='revoked'"))
        rows = database.all("SELECT id,mt5_account,plan,status,expires_at,created_at FROM licenses ORDER BY created_at DESC LIMIT 200")
        rows_html = "".join(
            f"""<tr>
              <td style='font-family:monospace;font-size:11px'>{dict(r)['id'][:8]}…</td>
              <td>{dict(r)['mt5_account']}</td>
              <td>{dict(r)['plan']}</td>
              <td><span class='badge badge-{dict(r)["status"]}'>{dict(r)['status']}</span></td>
              <td style='font-size:11px'>{dict(r)['expires_at'][:10]}</td>
              <td style='font-size:11px'>{dict(r)['created_at'][:10]}</td>
              <td>
                <button onclick="revoke('{dict(r)['id']}')" class='btn-rev'
                  {'disabled' if dict(r)['status']=='revoked' else ''}>Revoke</button>
              </td>
            </tr>""" for r in rows
        )
        return HTMLResponse(_admin_html(users_count, active, pending, revoked, rows_html))

    @app.get("/admin/dashboard/json")
    def dashboard_json(user=Depends(admin_user)):
        return {"users": len(database.all("SELECT id FROM users")),
                "active_licenses": len(database.all("SELECT id FROM licenses WHERE status='active'")),
                "licenses": [dict(row) for row in database.all("SELECT id,mt5_account,plan,status,expires_at FROM licenses ORDER BY created_at DESC LIMIT 100")]}

    @app.post("/admin/licenses/{license_id}/revoke")
    def revoke_license(license_id: str, request: RevokeRequest, user=Depends(admin_user)):
        database.execute("UPDATE licenses SET status='revoked', revoked_reason=? WHERE id=?", (request.reason, license_id))
        return {"revoked": True, "license_id": license_id}

    return app


def _admin_html(users: int, active: int, pending: int, revoked: int, rows_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>MyTradeBot Admin</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"/>
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet"/>
<style>
:root{{--bg:#0B1220;--card:#111A2E;--border:#1E2D4A;--green:#16C784;--red:#EA3943;--text:#E6EAF2;--muted:#8A94A8;}}
body{{background:var(--bg);color:var(--text);font-family:'Segoe UI',Arial,sans-serif;font-size:14px;}}
.navbar{{background:var(--card);border-bottom:1px solid var(--border);}}
.brand{{color:var(--green);font-weight:700;font-size:1rem;}}
.mc{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:.8rem 1rem;}}
.mc-label{{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:4px;}}
.mc-value{{font-size:1.4rem;font-weight:700;}}
.table{{color:var(--text);font-size:13px;}}
.table thead th{{background:var(--card);color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:1px;border-color:var(--border);}}
.table td{{border-color:var(--border);vertical-align:middle;}}
.table tbody tr:hover{{background:rgba(22,199,132,.04);}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:12px;}}
.card-header{{background:var(--card);border-bottom:1px solid var(--border);color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:1px;}}
.badge-active{{background:rgba(22,199,132,.15);color:var(--green);border:1px solid rgba(22,199,132,.3);border-radius:4px;padding:2px 8px;font-size:11px;}}
.badge-pending{{background:rgba(240,165,0,.15);color:#F0A500;border:1px solid rgba(240,165,0,.3);border-radius:4px;padding:2px 8px;font-size:11px;}}
.badge-revoked{{background:rgba(234,57,67,.15);color:var(--red);border:1px solid rgba(234,57,67,.3);border-radius:4px;padding:2px 8px;font-size:11px;}}
.btn-rev{{background:rgba(234,57,67,.1);color:var(--red);border:1px solid var(--border);border-radius:5px;font-size:11px;padding:2px 10px;cursor:pointer;}}
.btn-rev:hover{{background:var(--red);color:#fff;}}
.btn-rev:disabled{{opacity:.4;cursor:not-allowed;}}
#msg{{font-size:12px;min-height:18px;}}
</style>
</head>
<body>
<nav class="navbar px-4 py-2 mb-4">
  <span class="brand"><i class="bi bi-graph-up-arrow me-2"></i>MyTradeBot Admin</span>
  <span style="margin-left:auto;font-size:11px;color:var(--muted)">Licensing Dashboard</span>
</nav>
<div class="container-fluid px-4">
  <div class="row g-3 mb-4">
    <div class="col-6 col-md-3"><div class="mc"><div class="mc-label"><i class="bi bi-people me-1"></i>Total Users</div><div class="mc-value">{users}</div></div></div>
    <div class="col-6 col-md-3"><div class="mc"><div class="mc-label"><i class="bi bi-check-circle me-1"></i>Active Licenses</div><div class="mc-value" style="color:var(--green)">{active}</div></div></div>
    <div class="col-6 col-md-3"><div class="mc"><div class="mc-label"><i class="bi bi-clock me-1"></i>Pending Payment</div><div class="mc-value" style="color:#F0A500">{pending}</div></div></div>
    <div class="col-6 col-md-3"><div class="mc"><div class="mc-label"><i class="bi bi-x-circle me-1"></i>Revoked</div><div class="mc-value" style="color:var(--red)">{revoked}</div></div></div>
  </div>
  <div class="card">
    <div class="card-header p-3 d-flex justify-content-between align-items-center">
      <span><i class="bi bi-key me-1"></i>Licenses (latest 200)</span>
      <div id="msg"></div>
    </div>
    <div class="card-body p-0">
      <div class="table-responsive">
        <table class="table table-sm mb-0">
          <thead><tr><th>ID</th><th>MT5 Account</th><th>Plan</th><th>Status</th><th>Expires</th><th>Created</th><th></th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </div>
  </div>
</div>
<script>
async function revoke(id) {{
  const reason = prompt('Revocation reason (required):');
  if (!reason) return;
  const token = localStorage.getItem('mtb_admin_token') || prompt('Paste your admin Bearer token:');
  if (!token) return;
  localStorage.setItem('mtb_admin_token', token);
  const r = await fetch('/admin/licenses/' + id + '/revoke', {{
    method: 'POST',
    headers: {{'Content-Type':'application/json','Authorization':'Bearer ' + token}},
    body: JSON.stringify({{reason}})
  }}).then(r => r.json()).catch(e => ({{error: e.message}}));
  const msg = document.getElementById('msg');
  if (r.revoked) {{
    msg.style.color = 'var(--green)'; msg.textContent = 'License ' + id.slice(0,8) + '… revoked.';
    setTimeout(() => location.reload(), 1200);
  }} else {{
    msg.style.color = 'var(--red)'; msg.textContent = JSON.stringify(r);
  }}
}}
</script>
</body></html>"""


def _payment_provider(settings: Settings) -> PaymentProvider:
    if settings.payment_provider == "manual":
        return ManualProvider()
    if settings.payment_provider == "stripe":
        needed = ["STRIPE_SECRET_KEY", "STRIPE_MONTHLY_PRICE_ID", "STRIPE_ANNUAL_PRICE_ID", "PAYMENT_SUCCESS_URL", "PAYMENT_CANCEL_URL", "STRIPE_WEBHOOK_SECRET"]
        if any(not os.getenv(name) for name in needed):
            raise RuntimeError("Stripe is selected but required Stripe environment variables are missing")
        return StripeCheckoutProvider(*(os.environ[name] for name in needed))
    if settings.payment_provider == "mobile_money":
        if not os.getenv("MOBILE_MONEY_CHECKOUT_URL") or not os.getenv("MOBILE_MONEY_CALLBACK_SECRET"):
            raise RuntimeError("Mobile money is selected but callback configuration is missing")
        return MobileMoneyCallbackProvider(os.environ["MOBILE_MONEY_CHECKOUT_URL"], os.environ["MOBILE_MONEY_CALLBACK_SECRET"])
    raise RuntimeError("PAYMENT_PROVIDER must be manual, stripe, or mobile_money")


app = create_app()
