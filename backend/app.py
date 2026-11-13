"""FastAPI licensing backend; PostgreSQL in production, SQLite only for local tests."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen
from uuid import uuid4

import bcrypt
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field, field_validator
from backend.plans import (
    AI_FEATURES_ENABLED,
    PAID_PLANS_ENABLED,
    PLANS,
    TRIAL_DAYS,
    TRIAL_PLAN,
    plan_public_data,
)
from backend.mpesa import DarajaClient, MpesaCheckout
from backend.payments import (ManualProvider, MobileMoneyCallbackProvider,
                              PaymentProvider, StripeCheckoutProvider)

log = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_phone_number(value: str) -> str:
    phone = re.sub(r"[\s()-]", "", value)
    if re.fullmatch(r"0[17]\d{8}", phone):
        phone = f"+254{phone[1:]}"
    elif re.fullmatch(r"254[17]\d{8}", phone):
        phone = f"+{phone}"
    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        raise ValueError("Enter a valid phone number in international format")
    return phone


class Settings:
    def __init__(self) -> None:
        self.environment = os.getenv("MYTRADEBOT_ENV", "development")
        # Default to /tmp on serverless (Vercel filesystem is read-only except /tmp).
        # In production always set DATABASE_URL to a PostgreSQL connection string.
        _default_db = "sqlite:////tmp/mytradebot-licenses.db"
        self.database_url = os.getenv("DATABASE_URL", _default_db)
        self.jwt_secret = os.getenv("JWT_SECRET", "development-only-change-me")
        self.license_secret = os.getenv("LICENSE_SIGNING_SECRET", self.jwt_secret)
        self.admin_email = os.getenv("ADMIN_EMAIL", "rakitashadrack@gmail.com").lower()
        self.developer_email = os.getenv(
            "DEVELOPER_EMAIL", "masonrakitafx@gmail.com"
        ).lower()
        self.admin_bootstrap_token = os.getenv("ADMIN_BOOTSTRAP_TOKEN", "")
        self.rate_limit_per_minute = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
        self.allowed_origins = os.getenv("ALLOWED_ORIGINS", "")
        self.payment_provider = os.getenv("PAYMENT_PROVIDER", "manual")
        self.sms_provider = os.getenv("SMS_PROVIDER", "disabled")
        self.africastalking_username = os.getenv("AFRICASTALKING_USERNAME", "")
        self.africastalking_api_key = os.getenv("AFRICASTALKING_API_KEY", "")
        self.mpesa_environment = os.getenv("MPESA_ENVIRONMENT", "sandbox")
        self.mpesa_consumer_key = os.getenv("MPESA_CONSUMER_KEY", "")
        self.mpesa_consumer_secret = os.getenv("MPESA_CONSUMER_SECRET", "")
        self.mpesa_shortcode = os.getenv("MPESA_SHORTCODE", "")
        self.mpesa_passkey = os.getenv("MPESA_PASSKEY", "")
        self.mpesa_callback_url = os.getenv("MPESA_CALLBACK_URL", "")
        self.mpesa_callback_token = os.getenv("MPESA_CALLBACK_TOKEN", "")
        self.mpesa_transaction_type = os.getenv(
            "MPESA_TRANSACTION_TYPE", "CustomerPayBillOnline"
        )
        self.mpesa_plan_prices_kes = {
            plan: os.getenv(f"MPESA_PRICE_{plan.upper()}_KES", "")
            for plan in ("pro", "ultimate", "platinum")
        }
        if self.environment == "production":
            production_secrets = (
                self.jwt_secret,
                self.license_secret,
                self.admin_bootstrap_token,
            )
            mpesa_required = (
                self.mpesa_environment,
                self.mpesa_consumer_key,
                self.mpesa_consumer_secret,
                self.mpesa_shortcode,
                self.mpesa_passkey,
                self.mpesa_callback_url,
                self.mpesa_callback_token,
                *self.mpesa_plan_prices_kes.values(),
            )
            insecure = (not self.database_url.startswith("postgresql") or
                        self.payment_provider not in {"stripe", "mobile_money", "mpesa_daraja"} or
                        (self.payment_provider == "mpesa_daraja" and
                         (any(not value for value in mpesa_required) or
                          len(self.mpesa_callback_token) < 32 or
                          self.mpesa_environment != "production")) or
                        self.sms_provider != "africastalking" or
                        not self.africastalking_username or not self.africastalking_api_key or
                        not self.allowed_origins or
                        any(origin.strip() == "*" for origin in self.allowed_origins.split(",")) or
                        len(self.jwt_secret) < 32 or len(self.license_secret) < 32 or
                        len(self.admin_bootstrap_token) < 32 or
                        len({self.jwt_secret, self.license_secret, self.admin_bootstrap_token}) < 3 or
                        any("replace-with" in secret.lower() for secret in production_secrets) or
                        self.jwt_secret == "development-only-change-me")
            if insecure:
                raise RuntimeError(
                    "Production requires PostgreSQL, three distinct 32+ character secrets, "
                    "a configured payment provider, and Africa's Talking SMS credentials; "
                    "Daraja also requires production credentials, callback configuration, and KES prices"
                )


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    phone_number: str | None = None

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        return normalize_phone_number(value) if value else None


class LoginRequest(RegisterRequest):
    pass


class PhoneCodeRequest(BaseModel):
    phone_number: str
    email: EmailStr

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        return normalize_phone_number(value)


class PhoneCodeVerifyRequest(PhoneCodeRequest):
    code: str = Field(pattern=r"^\d{6}$")


class SubscriptionCheckoutRequest(BaseModel):
    plan: str = Field(pattern="^(pro|ultimate|platinum)$")


class SubscriptionRequest(BaseModel):
    plan: str = Field(pattern="^(monthly|annual)$")
    mt5_account: str = Field(min_length=3, max_length=32, pattern=r"^[0-9]+$")
    device_id: str = Field(min_length=16, max_length=256)


class LicenseValidationRequest(BaseModel):
    mt5_account: str = Field(min_length=3, max_length=32, pattern=r"^[0-9]+$")
    device_id: str = Field(min_length=16, max_length=256)


class LicenseBindRequest(LicenseValidationRequest):
    pass


class RevokeRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class SmsProvider(Protocol):
    def send(self, phone_number: str, message: str) -> None: ...


class AfricaTalkingSmsProvider:
    """Send an OTP using the Africa's Talking messaging API."""

    _ENDPOINT = "https://api.africastalking.com/version1/messaging"

    def __init__(self, username: str, api_key: str) -> None:
        self.username = username
        self.api_key = api_key

    def send(self, phone_number: str, message: str) -> None:
        body = urlencode({
            "username": self.username,
            "to": phone_number,
            "message": message,
        }).encode()
        request = UrlRequest(
            self._ENDPOINT,
            data=body,
            headers={
                "apiKey": self.api_key,
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=15) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("Africa's Talking SMS request failed") from exc

        message_data = result.get("SMSMessageData")
        recipients = message_data.get("Recipients") if isinstance(message_data, dict) else None
        if not isinstance(recipients, list) or not recipients or any(
            not isinstance(item, dict) or item.get("status") != "Success"
            for item in recipients
        ):
            raise RuntimeError("Africa's Talking did not accept the SMS")


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
                is_admin {bool_type} NOT NULL DEFAULT {bool_default}, created_at TEXT NOT NULL,
                phone_number TEXT, phone_verified_at TEXT)""")
            if self.is_sqlite:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
                if "phone_number" not in columns:
                    conn.execute("ALTER TABLE users ADD COLUMN phone_number TEXT")
                if "phone_verified_at" not in columns:
                    conn.execute("ALTER TABLE users ADD COLUMN phone_verified_at TEXT")
            else:
                conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_number TEXT")
                conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_verified_at TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_phone_number_unique "
                         "ON users(phone_number)")
            conn.execute("""CREATE TABLE IF NOT EXISTS phone_login_codes (
                id TEXT PRIMARY KEY, phone_number TEXT NOT NULL, email TEXT NOT NULL,
                code_hash TEXT NOT NULL, expires_at TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, consumed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, plan TEXT NOT NULL,
                status TEXT NOT NULL, starts_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                provider TEXT NOT NULL, payment_reference TEXT, created_at TEXT NOT NULL)""")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS subscriptions_user_created "
                "ON subscriptions(user_id, created_at)"
            )
            conn.execute("""CREATE TABLE IF NOT EXISTS mpesa_payments (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, plan TEXT NOT NULL,
                phone_number TEXT NOT NULL, amount_kes INTEGER NOT NULL,
                checkout_request_id TEXT UNIQUE NOT NULL, merchant_request_id TEXT NOT NULL,
                status TEXT NOT NULL, receipt_number TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL)""")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS mpesa_receipt_unique "
                "ON mpesa_payments(receipt_number) WHERE receipt_number IS NOT NULL"
            )
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

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self.connection() as conn:
            return conn.execute(self._sql(sql), params).rowcount

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


def create_app(
    settings: Settings | None = None,
    database: Database | None = None,
    sms_provider: SmsProvider | None = None,
) -> FastAPI:
    settings = settings or Settings()
    database = database or Database(settings.database_url)
    app = FastAPI(title="MyTradeBot Licensing API", version="0.1.0")
    limiter = RateLimiter(settings.rate_limit_per_minute)
    phone_limiter = RateLimiter(3)
    payment_provider = _payment_provider(settings)
    sms_provider = sms_provider or _sms_provider(settings)
    mpesa_client, mpesa_missing = _mpesa_client(settings)

    @app.middleware("http")
    async def limit_requests(request: Request, call_next):
        client = request.client.host if request.client else "unknown"
        limiter.check(f"{client}:{request.url.path}")
        return await call_next(request)

    def issue_access_token(user) -> str:
        return jwt.encode({"sub": str(row_value(user, "id")), "email": row_value(user, "email"),
                           "admin": bool(row_value(user, "is_admin")), "exp": utc_now() + timedelta(hours=12)},
                          settings.jwt_secret, algorithm="HS256")

    def start_trial(user_id: str, started_at: datetime) -> None:
        database.execute(
            """INSERT INTO subscriptions
               (id,user_id,plan,status,starts_at,expires_at,provider,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                str(uuid4()),
                user_id,
                TRIAL_PLAN,
                "trialing",
                started_at.isoformat(),
                (started_at + timedelta(days=TRIAL_DAYS)).isoformat(),
                "trial",
                started_at.isoformat(),
            ),
        )

    def grant_developer_access(user_id: str, granted_at: datetime) -> None:
        existing = database.one(
            """SELECT id FROM subscriptions
               WHERE user_id=? AND provider='developer' AND status='active' AND expires_at>?
               LIMIT 1""",
            (user_id, granted_at.isoformat()),
        )
        if existing:
            return
        database.execute(
            """INSERT INTO subscriptions
               (id,user_id,plan,status,starts_at,expires_at,provider,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                str(uuid4()),
                user_id,
                "platinum",
                "active",
                granted_at.isoformat(),
                "9999-12-31T23:59:59+00:00",
                "developer",
                granted_at.isoformat(),
            ),
        )

    developer_user = database.one(
        "SELECT id FROM users WHERE lower(email)=?",
        (settings.developer_email,),
    )
    if developer_user:
        grant_developer_access(str(row_value(developer_user, "id")), utc_now())

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

    @app.get("/", response_class=HTMLResponse)
    def portal():
        return FileResponse(
            Path(__file__).parent / "static" / "index.html",
            media_type="text/html",
        )

    @app.get("/manifest.webmanifest")
    def pwa_manifest():
        return FileResponse(
            Path(__file__).parent / "static" / "manifest.webmanifest",
            media_type="application/manifest+json",
        )

    @app.get("/icon.svg")
    def pwa_icon():
        return FileResponse(
            Path(__file__).parent / "static" / "icon.svg",
            media_type="image/svg+xml",
        )

    @app.get("/favicon.ico")
    def favicon():
        return FileResponse(
            Path(__file__).parent / "static" / "icon.svg",
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=604800"},
        )

    @app.get("/service-worker.js")
    def pwa_service_worker():
        return FileResponse(
            Path(__file__).parent / "static" / "service-worker.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/plans")
    def get_plans():
        return {
            "trial_days": TRIAL_DAYS,
            "trial_plan": TRIAL_PLAN,
            "ai_features_enabled": AI_FEATURES_ENABLED,
            "mpesa_checkout_enabled": (
                PAID_PLANS_ENABLED
                and settings.payment_provider == "mpesa_daraja"
                and mpesa_client is not None
            ),
            "monthly_prices_kes": (
                mpesa_client.plan_prices_kes if mpesa_client is not None else None
            ),
            "plans": [plan_public_data(plan) for plan in PLANS.values()],
        }

    @app.get("/billing/availability")
    def billing_availability():
        daraja_configured = settings.payment_provider == "mpesa_daraja" and mpesa_client is not None
        enabled = PAID_PLANS_ENABLED and daraja_configured
        return {
            "enabled": enabled,
            "provider": "mpesa_daraja" if daraja_configured else None,
            "message": (
                "M-Pesa checkout is ready."
                if enabled
                else "Paid plans are not open yet; remote PC control, AI features, and email receipts must be finished and tested."
                if daraja_configured
                else (
                    "Checkout is disabled until Daraja credentials, callback URL, and all KES prices are configured."
                    if not mpesa_missing
                    else "Checkout is disabled; the owner must configure: " + ", ".join(mpesa_missing)
                )
            ),
            "monthly_prices_kes": (
                mpesa_client.plan_prices_kes if enabled and mpesa_client is not None else None
            ),
        }

    @app.post("/auth/register", status_code=status.HTTP_201_CREATED)
    def register(
        request: RegisterRequest,
        admin_bootstrap_token: str | None = Header(default=None, alias="X-Admin-Bootstrap-Token"),
    ):
        email = request.email.lower()
        bootstrap_required = email == settings.admin_email
        if bootstrap_required and (
            not settings.admin_bootstrap_token
            or not admin_bootstrap_token
            or not secrets.compare_digest(admin_bootstrap_token, settings.admin_bootstrap_token)
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Private one-time setup token required for this account",
            )
        if email == settings.admin_email:
            if database.one("SELECT id FROM users WHERE is_admin=?", (True,)):
                raise HTTPException(status.HTTP_409_CONFLICT, "Administrator account has already been created")
        if database.one("SELECT id FROM users WHERE email=?", (email,)):
            raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
        if request.phone_number and database.one(
            "SELECT id FROM users WHERE phone_number=?",
            (request.phone_number,),
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, "Phone number already registered")
        user_id = str(uuid4())
        database.execute(
                         "INSERT INTO users (id,email,password_hash,is_admin,created_at,phone_number) "
                         "VALUES (?,?,?,?,?,?)",
                         (user_id, email, _hash_password(request.password),
                          int(email == settings.admin_email) if database.is_sqlite else email == settings.admin_email,
                          utc_now().isoformat(), request.phone_number))
        user = database.one("SELECT * FROM users WHERE id=?", (user_id,))
        start_trial(user_id, utc_now())
        return {"access_token": issue_access_token(user), "token_type": "bearer"}

    @app.post("/auth/login")
    def login(request: LoginRequest):
        user = database.one("SELECT * FROM users WHERE email=?", (request.email.lower(),))
        if not user or not _verify_password(request.password, row_value(user, "password_hash")):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
        return {"access_token": issue_access_token(user), "token_type": "bearer"}

    @app.post("/auth/phone/request", status_code=status.HTTP_202_ACCEPTED)
    def request_phone_code(request: PhoneCodeRequest):
        if sms_provider is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Phone sign-in is unavailable until Africa's Talking is configured",
            )
        phone_limiter.check(request.phone_number)
        code = f"{secrets.randbelow(1_000_000):06d}"
        code_hash = hmac.new(
            settings.jwt_secret.encode(),
            f"{request.phone_number}\0{code}".encode(),
            hashlib.sha256,
        ).hexdigest()
        created_at = utc_now()
        challenge_id = str(uuid4())
        database.execute(
            "UPDATE phone_login_codes SET consumed=1 WHERE phone_number=? AND consumed=0",
            (request.phone_number,),
        )
        database.execute(
            """INSERT INTO phone_login_codes
               (id,phone_number,email,code_hash,expires_at,created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                challenge_id,
                request.phone_number,
                str(request.email).lower(),
                code_hash,
                (created_at + timedelta(minutes=5)).isoformat(),
                created_at.isoformat(),
            ),
        )
        try:
            sms_provider.send(
                request.phone_number,
                f"Your MyTradeBot sign-in code is {code}. It expires in 5 minutes. Never share it.",
            )
        except (OSError, RuntimeError) as exc:
            database.execute(
                "UPDATE phone_login_codes SET consumed=1 WHERE id=?",
                (challenge_id,),
            )
            log.error("Could not send MyTradeBot sign-in code: %s", exc)
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "Could not deliver the sign-in code; try again later",
            ) from exc
        return {
            "message": "If this number can sign in or register, a code has been sent.",
            "expires_in_seconds": 300,
        }

    @app.post("/auth/phone/verify")
    def verify_phone_code(
        request: PhoneCodeVerifyRequest,
        admin_bootstrap_token: str | None = Header(default=None, alias="X-Admin-Bootstrap-Token"),
    ):
        now = utc_now()
        challenge = database.one(
            """SELECT * FROM phone_login_codes
               WHERE phone_number=? AND consumed=0
               ORDER BY created_at DESC LIMIT 1""",
            (request.phone_number,),
        )
        if not challenge or datetime.fromisoformat(row_value(challenge, "expires_at")) <= now:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "The sign-in code is invalid or expired")
        if row_value(challenge, "attempts") >= 5:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many incorrect codes")

        expected = hmac.new(
            settings.jwt_secret.encode(),
            f"{request.phone_number}\0{request.code}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, row_value(challenge, "code_hash")):
            database.execute(
                "UPDATE phone_login_codes SET attempts=attempts+1 WHERE id=? AND consumed=0",
                (row_value(challenge, "id"),),
            )
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "The sign-in code is invalid or expired")

        user = database.one(
            "SELECT * FROM users WHERE phone_number=?",
            (request.phone_number,),
        )
        email = str(row_value(challenge, "email")).lower()
        is_admin = user is None and email == settings.admin_email
        if is_admin and (
            not settings.admin_bootstrap_token
            or not admin_bootstrap_token
            or not secrets.compare_digest(admin_bootstrap_token, settings.admin_bootstrap_token)
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Administrator bootstrap authorization required",
            )
        if is_admin and database.one("SELECT id FROM users WHERE is_admin=?", (True,)):
            raise HTTPException(status.HTTP_409_CONFLICT, "Administrator account has already been created")

        consumed = database.execute(
            """UPDATE phone_login_codes SET consumed=1
               WHERE id=? AND consumed=0 AND attempts<5 AND expires_at>? AND code_hash=?""",
            (row_value(challenge, "id"), now.isoformat(), expected),
        )
        if consumed != 1:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "The sign-in code is invalid or expired")

        if user is None:
            if database.one("SELECT id FROM users WHERE email=?", (email,)):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "This email is already in use; sign in to that account and link its phone number",
                )
            user_id = str(uuid4())
            database.execute(
                """INSERT INTO users
                   (id,email,password_hash,is_admin,created_at,phone_number)
                   VALUES (?,?,?,?,?,?)""",
                (
                    user_id,
                    email,
                    _hash_password(secrets.token_urlsafe(32)),
                    int(is_admin) if database.is_sqlite else is_admin,
                    now.isoformat(),
                    request.phone_number,
                ),
            )
            user = database.one("SELECT * FROM users WHERE id=?", (user_id,))
            start_trial(user_id, now)
        database.execute(
            "UPDATE users SET phone_verified_at=? WHERE id=?",
            (now.isoformat(), row_value(user, "id")),
        )
        user = database.one("SELECT * FROM users WHERE id=?", (row_value(user, "id"),))
        return {"access_token": issue_access_token(user), "token_type": "bearer"}

    @app.post("/account/phone/request", status_code=status.HTTP_202_ACCEPTED)
    def request_phone_link(request: PhoneCodeRequest, user=Depends(current_user)):
        if str(request.email).lower() != str(row_value(user, "email")).lower():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Use the email address of the signed-in account",
            )
        existing = database.one(
            "SELECT id FROM users WHERE phone_number=? AND id!=?",
            (request.phone_number, row_value(user, "id")),
        )
        if existing:
            raise HTTPException(status.HTTP_409_CONFLICT, "Phone number is already linked to another account")
        return request_phone_code(request)

    @app.post("/account/phone/verify")
    def verify_phone_link(request: PhoneCodeVerifyRequest, user=Depends(current_user)):
        email = str(row_value(user, "email")).lower()
        if str(request.email).lower() != email:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Use the email address of the signed-in account",
            )
        now = utc_now()
        challenge = database.one(
            """SELECT * FROM phone_login_codes
               WHERE phone_number=? AND email=? AND consumed=0
               ORDER BY created_at DESC LIMIT 1""",
            (request.phone_number, email),
        )
        if not challenge or datetime.fromisoformat(row_value(challenge, "expires_at")) <= now:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "The sign-in code is invalid or expired")
        if row_value(challenge, "attempts") >= 5:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many incorrect codes")
        expected = hmac.new(
            settings.jwt_secret.encode(),
            f"{request.phone_number}\0{request.code}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, row_value(challenge, "code_hash")):
            database.execute(
                "UPDATE phone_login_codes SET attempts=attempts+1 WHERE id=? AND consumed=0",
                (row_value(challenge, "id"),),
            )
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "The sign-in code is invalid or expired")
        existing = database.one(
            "SELECT id FROM users WHERE phone_number=? AND id!=?",
            (request.phone_number, row_value(user, "id")),
        )
        if existing:
            raise HTTPException(status.HTTP_409_CONFLICT, "Phone number is already linked to another account")
        consumed = database.execute(
            """UPDATE phone_login_codes SET consumed=1
               WHERE id=? AND consumed=0 AND attempts<5 AND expires_at>? AND code_hash=?""",
            (row_value(challenge, "id"), now.isoformat(), expected),
        )
        if consumed != 1:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "The sign-in code is invalid or expired")
        database.execute(
            "UPDATE users SET phone_number=?, phone_verified_at=? WHERE id=?",
            (request.phone_number, now.isoformat(), row_value(user, "id")),
        )
        return {"phone_number": request.phone_number, "phone_verified": True}

    @app.get("/account")
    def account(user=Depends(current_user)):
        user_id = str(row_value(user, "id"))
        subscription = database.one(
            """SELECT id,plan,status,starts_at,expires_at,provider
               FROM subscriptions WHERE user_id=?
               ORDER BY CASE WHEN status='active' AND expires_at>? THEN 0 ELSE 1 END,
                        CASE WHEN provider='developer' THEN 0 ELSE 1 END,
                        created_at DESC LIMIT 1""",
            (user_id, utc_now().isoformat()),
        )
        if subscription:
            expires_at = datetime.fromisoformat(row_value(subscription, "expires_at"))
            if row_value(subscription, "status") in {"trialing", "active"} and expires_at <= utc_now():
                database.execute(
                    "UPDATE subscriptions SET status='expired' WHERE id=? AND status IN ('trialing','active')",
                    (row_value(subscription, "id"),),
                )
                subscription = database.one(
                    """SELECT id,plan,status,starts_at,expires_at,provider
                       FROM subscriptions WHERE id=?""",
                    (row_value(subscription, "id"),),
                )
        return {
            "email": row_value(user, "email"),
            "phone_number": row_value(user, "phone_number"),
            "phone_verified": bool(row_value(user, "phone_verified_at")),
            "is_admin": bool(row_value(user, "is_admin")),
            "subscription": dict(subscription) if subscription else None,
            "plans": [plan_public_data(plan) for plan in PLANS.values()],
            "ai_features_enabled": AI_FEATURES_ENABLED,
        }

    @app.post("/subscriptions/checkout", status_code=status.HTTP_201_CREATED)
    def start_subscription_checkout(
        request: SubscriptionCheckoutRequest,
        user=Depends(current_user),
    ):
        if (
            not PAID_PLANS_ENABLED
            or settings.payment_provider != "mpesa_daraja"
            or mpesa_client is None
        ):
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Paid checkout is not enabled; no payment was started",
            )
        phone_number = row_value(user, "phone_number")
        if not phone_number:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Verify a phone number before starting checkout",
            )
        amount = mpesa_client.plan_prices_kes[request.plan]
        payment_id = str(uuid4())
        account_reference = payment_id.replace("-", "")[:12].upper()
        try:
            checkout = mpesa_client.start_checkout(
                phone_number,
                request.plan,
                account_reference,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except RuntimeError as exc:
            log.error("M-Pesa checkout failed for user %s: %s", row_value(user, "id"), exc)
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "M-Pesa could not start the payment prompt; try again later",
            ) from exc
        now = utc_now().isoformat()
        database.execute(
            """INSERT INTO mpesa_payments
               (id,user_id,plan,phone_number,amount_kes,checkout_request_id,
                merchant_request_id,status,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                payment_id,
                str(row_value(user, "id")),
                request.plan,
                phone_number,
                amount,
                checkout.checkout_request_id,
                checkout.merchant_request_id,
                "pending",
                now,
                now,
            ),
        )
        return {
            "payment_id": payment_id,
            "status": "pending",
            "amount_kes": amount,
            "message": checkout.customer_message,
        }

    @app.get("/subscriptions/payments/{payment_id}")
    def get_subscription_payment(payment_id: str, user=Depends(current_user)):
        payment = database.one(
            """SELECT id,plan,amount_kes,status,receipt_number,created_at
               FROM mpesa_payments WHERE id=? AND user_id=?""",
            (payment_id, str(row_value(user, "id"))),
        )
        if not payment:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
        return dict(payment)

    @app.post("/payments/mpesa/{callback_token}")
    async def mpesa_callback(callback_token: str, request: Request):
        if (
            not PAID_PLANS_ENABLED
            or mpesa_client is None
            or not settings.mpesa_callback_token
            or not secrets.compare_digest(callback_token, settings.mpesa_callback_token)
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Callback not found")
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid M-Pesa callback") from exc
        callback_body = payload.get("Body") if isinstance(payload, dict) else None
        callback = callback_body.get("stkCallback") if isinstance(callback_body, dict) else None
        if not isinstance(callback, dict):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid M-Pesa callback")
        checkout_id = callback.get("CheckoutRequestID")
        merchant_id = callback.get("MerchantRequestID")
        if not isinstance(checkout_id, str) or not isinstance(merchant_id, str):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid M-Pesa callback identifiers")
        result_code = callback.get("ResultCode")
        successful_result = (
            (type(result_code) is int and result_code == 0)
            or (isinstance(result_code, str) and result_code == "0")
        )
        if not successful_result:
            database.execute(
                """UPDATE mpesa_payments SET status='failed',updated_at=?
                   WHERE checkout_request_id=? AND merchant_request_id=? AND status='pending'""",
                (utc_now().isoformat(), checkout_id, merchant_id),
            )
            return {"ResultCode": 0, "ResultDesc": "Callback accepted"}

        metadata = callback.get("CallbackMetadata")
        items = metadata.get("Item") if isinstance(metadata, dict) else None
        values = {
            item.get("Name"): item.get("Value")
            for item in items
            if isinstance(item, dict) and isinstance(item.get("Name"), str)
        } if isinstance(items, list) else {}
        amount = values.get("Amount")
        receipt = values.get("MpesaReceiptNumber")
        phone_value = values.get("PhoneNumber")
        if (
            not isinstance(amount, (int, float))
            or isinstance(amount, bool)
            or not float(amount).is_integer()
            or not isinstance(receipt, str)
            or not receipt
            or phone_value is None
        ):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Incomplete M-Pesa payment confirmation")
        callback_phone = str(phone_value)
        if callback_phone.startswith("254"):
            callback_phone = "+" + callback_phone

        now = utc_now()
        lock_suffix = "" if database.is_sqlite else " FOR UPDATE"
        with database.connection() as conn:
            payment = conn.execute(
                database._sql(
                    "SELECT * FROM mpesa_payments WHERE checkout_request_id=?"
                    + lock_suffix
                ),
                (checkout_id,),
            ).fetchone()
            if not payment or row_value(payment, "merchant_request_id") != merchant_id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment request not found")
            if row_value(payment, "status") != "pending":
                return {"ResultCode": 0, "ResultDesc": "Callback already processed"}
            if (
                int(amount) != row_value(payment, "amount_kes")
                or callback_phone != row_value(payment, "phone_number")
            ):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "M-Pesa payment details did not match")

            existing = conn.execute(
                database._sql(
                    "SELECT id FROM users WHERE id=?" + lock_suffix
                ),
                (row_value(payment, "user_id"),),
            ).fetchone()
            if not existing:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment account not found")
            existing = conn.execute(
                database._sql(
                    """SELECT status,expires_at FROM subscriptions
                       WHERE user_id=? AND status IN ('trialing','active')
                       ORDER BY created_at DESC LIMIT 1"""
                ),
                (row_value(payment, "user_id"),),
            ).fetchone()
            starts_at = now
            if existing and row_value(existing, "status") == "active":
                current_expiry = datetime.fromisoformat(row_value(existing, "expires_at"))
                if current_expiry > now:
                    starts_at = current_expiry
            expires_at = starts_at + timedelta(days=30)
            conn.execute(
                database._sql(
                    """UPDATE subscriptions SET status='expired'
                       WHERE user_id=? AND status IN ('trialing','active')"""
                ),
                (row_value(payment, "user_id"),),
            )
            conn.execute(
                database._sql(
                    """INSERT INTO subscriptions
                       (id,user_id,plan,status,starts_at,expires_at,provider,
                        payment_reference,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)"""
                ),
                (
                    str(uuid4()),
                    row_value(payment, "user_id"),
                    row_value(payment, "plan"),
                    "active",
                    starts_at.isoformat(),
                    expires_at.isoformat(),
                    "mpesa_daraja",
                    receipt,
                    now.isoformat(),
                ),
            )
            conn.execute(
                database._sql(
                    """UPDATE mpesa_payments SET status='paid',receipt_number=?,updated_at=?
                       WHERE id=? AND status='pending'"""
                ),
                (receipt, now.isoformat(), row_value(payment, "id")),
            )
            phone_number = row_value(payment, "phone_number")
            plan_name = PLANS[row_value(payment, "plan")].name

        if sms_provider is not None:
            try:
                sms_provider.send(
                    phone_number,
                    f"MyTradeBot payment received. Your {plan_name} plan is active.",
                )
            except (OSError, RuntimeError) as exc:
                log.error("M-Pesa confirmation SMS failed: %s", exc)
        return {"ResultCode": 0, "ResultDesc": "Payment processed"}

    @app.post("/subscriptions", status_code=status.HTTP_201_CREATED)
    def create_subscription(request: SubscriptionRequest, user=Depends(current_user)):
        if settings.payment_provider == "mpesa_daraja":
            raise HTTPException(
                status.HTTP_410_GONE,
                "Legacy device licenses are not available with the M-Pesa product plans",
            )
        existing = database.one("SELECT id FROM licenses WHERE mt5_account=? AND device_hash=?", (request.mt5_account, device_hash(request.device_id)))
        if existing:
            raise HTTPException(status.HTTP_409_CONFLICT, "This MT5 account is already bound to this device")
        days = 365 if request.plan == "annual" else 31
        license_id = str(uuid4())
        expires = utc_now() + timedelta(days=days)
        checkout = payment_provider.create_checkout(license_id, request.plan, row_value(user, "email"))
        database.execute("INSERT INTO licenses (id,user_id,plan,mt5_account,device_hash,status,expires_at,created_at) VALUES (?,?,?,?,?,?,?,?)",
                         (license_id, row_value(user, "id"), request.plan, request.mt5_account, device_hash(request.device_id),
                          "pending", expires.isoformat(), utc_now().isoformat()))
        return {"license_id": license_id, "status": "pending_payment",
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
        with database.connection() as conn:
            license_row = conn.execute(
                database._sql("SELECT * FROM licenses WHERE id=?"),
                (license_id,),
            ).fetchone()
            if not license_row:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment license not found")
            updated = conn.execute(
                database._sql("UPDATE licenses SET status='active' WHERE id=? AND status='pending'"),
                (license_id,),
            ).rowcount
            if updated:
                started_at = utc_now()
                duration = timedelta(days=365 if row_value(license_row, "plan") == "annual" else 30)
                conn.execute(
                    database._sql(
                        """INSERT INTO subscriptions
                           (id,user_id,plan,status,starts_at,expires_at,provider,
                            payment_reference,created_at)
                           VALUES (?,?,?,?,?,?,?,?,?)"""
                    ),
                    (
                        str(uuid4()),
                        row_value(license_row, "user_id"),
                        row_value(license_row, "plan"),
                        "active",
                        started_at.isoformat(),
                        (started_at + duration).isoformat(),
                        payment_provider.name,
                        license_id,
                        started_at.isoformat(),
                    ),
                )
        return {"accepted": True}

    @app.post("/licenses/bind")
    def bind_license(request: LicenseBindRequest, user=Depends(current_user)):
        user_id = str(row_value(user, "id"))
        now = utc_now()
        subscription = database.one(
            """SELECT id,plan,expires_at FROM subscriptions
               WHERE user_id=? AND status='active' AND provider!='trial' AND expires_at>?
               ORDER BY expires_at DESC LIMIT 1""",
            (user_id, now.isoformat()),
        )
        if not subscription:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Active paid or developer access is required to run the bot",
            )
        fingerprint = device_hash(request.device_id)
        existing = database.one(
            "SELECT id,user_id FROM licenses WHERE mt5_account=? AND device_hash=?",
            (request.mt5_account, fingerprint),
        )
        if existing and str(row_value(existing, "user_id")) != user_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This MT5 account and device are already linked to another account",
            )
        expires_at = row_value(subscription, "expires_at")
        if existing:
            database.execute(
                """UPDATE licenses SET plan=?,status='active',expires_at=?
                   WHERE id=? AND user_id=?""",
                (
                    row_value(subscription, "plan"),
                    expires_at,
                    row_value(existing, "id"),
                    user_id,
                ),
            )
        else:
            database.execute(
                """INSERT INTO licenses
                   (id,user_id,plan,mt5_account,device_hash,status,expires_at,created_at)
                   VALUES (?,?,?,?,?,'active',?,?)""",
                (
                    str(uuid4()),
                    user_id,
                    row_value(subscription, "plan"),
                    request.mt5_account,
                    fingerprint,
                    expires_at,
                    now.isoformat(),
                ),
            )
        return {"valid": True, "expires_at": expires_at}

    @app.post("/licenses/validate")
    def validate_license(request: LicenseValidationRequest):
        license_row = database.one("SELECT * FROM licenses WHERE mt5_account=? AND device_hash=?", (request.mt5_account, device_hash(request.device_id)))
        if not license_row or row_value(license_row, "status") != "active" or datetime.fromisoformat(row_value(license_row, "expires_at")) <= utc_now():
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No active bot entitlement for this account and device")
        subscription = database.one(
            """SELECT expires_at FROM subscriptions
               WHERE user_id=? AND status='active' AND provider!='trial' AND expires_at>?
               ORDER BY expires_at DESC LIMIT 1""",
            (str(row_value(license_row, "user_id")), utc_now().isoformat()),
        )
        if not subscription:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No active bot entitlement for this account and device")
        expiry = min(
            datetime.fromisoformat(row_value(license_row, "expires_at")),
            datetime.fromisoformat(row_value(subscription, "expires_at")),
        )
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
        payment_summary = database.one(
            """SELECT COUNT(*) AS count,
                      COALESCE(SUM(CASE WHEN status='paid' THEN amount_kes ELSE 0 END),0) AS received_kes,
                      COALESCE(SUM(CASE WHEN status='paid' THEN 1 ELSE 0 END),0) AS successful,
                      COALESCE(SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),0) AS pending
               FROM mpesa_payments"""
        )
        return {"users": len(database.all("SELECT id FROM users")),
                "active_licenses": len(database.all("SELECT id FROM licenses WHERE status='active'")),
                "licenses": [dict(row) for row in database.all("SELECT id,mt5_account,plan,status,expires_at FROM licenses ORDER BY created_at DESC LIMIT 100")],
                "subscriptions": [dict(row) for row in database.all(
                    """SELECT s.id,s.plan,s.status,s.starts_at,s.expires_at,s.provider,
                              u.email,u.phone_number
                       FROM subscriptions s JOIN users u ON u.id=s.user_id
                       ORDER BY s.created_at DESC LIMIT 200"""
                )],
                "payment_summary": dict(payment_summary),
                "payments": [dict(row) for row in database.all(
                    """SELECT p.id,p.plan,p.phone_number,p.amount_kes,p.status,
                              p.receipt_number,p.created_at,p.updated_at,u.email
                       FROM mpesa_payments p JOIN users u ON u.id=p.user_id
                       ORDER BY p.created_at DESC LIMIT 200"""
                )]}

    @app.post("/admin/subscriptions/{subscription_id}/revoke")
    def revoke_subscription(subscription_id: str, request: RevokeRequest, user=Depends(admin_user)):
        updated = database.execute(
            """UPDATE subscriptions SET status='revoked'
               WHERE id=? AND status IN ('trialing','active')""",
            (subscription_id,),
        )
        if not updated:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Active subscription not found")
        log.warning(
            "Administrator %s revoked subscription %s: %s",
            row_value(user, "email"),
            subscription_id,
            request.reason,
        )
        return {"revoked": True, "subscription_id": subscription_id}

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
<link rel="icon" href="/icon.svg" type="image/svg+xml"/>
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
    if settings.payment_provider == "mpesa_daraja":
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
    raise RuntimeError("PAYMENT_PROVIDER must be manual, stripe, mobile_money, or mpesa_daraja")


def _mpesa_client(settings: Settings) -> tuple[DarajaClient | None, list[str]]:
    values = {
        "MPESA_CONSUMER_KEY": settings.mpesa_consumer_key,
        "MPESA_CONSUMER_SECRET": settings.mpesa_consumer_secret,
        "MPESA_SHORTCODE": settings.mpesa_shortcode,
        "MPESA_PASSKEY": settings.mpesa_passkey,
        "MPESA_CALLBACK_URL": settings.mpesa_callback_url,
        "MPESA_CALLBACK_TOKEN": settings.mpesa_callback_token,
        "MPESA_PRICE_PRO_KES": settings.mpesa_plan_prices_kes["pro"],
        "MPESA_PRICE_ULTIMATE_KES": settings.mpesa_plan_prices_kes["ultimate"],
        "MPESA_PRICE_PLATINUM_KES": settings.mpesa_plan_prices_kes["platinum"],
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        return None, missing
    try:
        prices = {
            plan: int(value)
            for plan, value in settings.mpesa_plan_prices_kes.items()
        }
    except ValueError as exc:
        raise RuntimeError("M-Pesa plan prices must be whole KES amounts") from exc
    if len(settings.mpesa_callback_token) < 32:
        raise RuntimeError("MPESA_CALLBACK_TOKEN must contain at least 32 characters")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", settings.mpesa_callback_token):
        raise RuntimeError("MPESA_CALLBACK_TOKEN must use 32-128 URL-safe characters")
    expected_callback_path = f"/payments/mpesa/{settings.mpesa_callback_token}"
    if not settings.mpesa_callback_url.endswith(expected_callback_path):
        raise RuntimeError("MPESA_CALLBACK_URL must end with the configured callback token path")
    try:
        client = DarajaClient(
            environment=settings.mpesa_environment,
            consumer_key=settings.mpesa_consumer_key,
            consumer_secret=settings.mpesa_consumer_secret,
            shortcode=settings.mpesa_shortcode,
            passkey=settings.mpesa_passkey,
            callback_url=settings.mpesa_callback_url,
            transaction_type=settings.mpesa_transaction_type,
            plan_prices_kes=prices,
        )
    except ValueError as exc:
        raise RuntimeError(f"Invalid M-Pesa configuration: {exc}") from exc
    return client, []


def _sms_provider(settings: Settings) -> SmsProvider | None:
    if settings.sms_provider == "disabled":
        return None
    if settings.sms_provider == "africastalking":
        if not settings.africastalking_username or not settings.africastalking_api_key:
            raise RuntimeError(
                "Africa's Talking is selected but username/API key are missing"
            )
        return AfricaTalkingSmsProvider(
            settings.africastalking_username,
            settings.africastalking_api_key,
        )
    raise RuntimeError("SMS_PROVIDER must be disabled or africastalking")


app = create_app()

