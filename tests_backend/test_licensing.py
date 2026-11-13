"""Backend business-flow tests without an external HTTP test-client dependency."""
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

import backend.app as backend_app
from backend.app import (AfricaTalkingSmsProvider, Database, LicenseValidationRequest, LoginRequest,
                         LicenseBindRequest, PhoneCodeRequest, PhoneCodeVerifyRequest, RegisterRequest, RevokeRequest, Settings,
                         SubscriptionCheckoutRequest, SubscriptionRequest, create_app)


def endpoint(app, path, method):
    route_endpoint = next(route.endpoint for route in app.routes if route.path == path and method in route.methods)
    if path == "/auth/register":
        def register(request, admin_bootstrap_token="test-admin-bootstrap-token"):
            return route_endpoint(request, admin_bootstrap_token=admin_bootstrap_token)
        return register
    if path == "/auth/phone/verify":
        def verify(request, admin_bootstrap_token=None):
            return route_endpoint(request, admin_bootstrap_token=admin_bootstrap_token)
        return verify
    if path == "/account/phone/request":
        def request_phone_link(request, user):
            return route_endpoint(request, user)
        return request_phone_link
    if path == "/account/phone/verify":
        def verify_phone_link(request, user):
            return route_endpoint(request, user)
        return verify_phone_link
    return route_endpoint


def service(tmp_path):
    settings = Settings()
    settings.database_url = f"sqlite:///{tmp_path / 'licenses.db'}"
    settings.jwt_secret = "test-jwt-secret-" + "j" * 32
    settings.license_secret = "test-license-secret-" + "l" * 32
    settings.admin_email = "owner@example.com"
    settings.developer_email = "masonrakitafx@gmail.com"
    settings.admin_bootstrap_token = "test-admin-bootstrap-token"
    database = Database(settings.database_url)
    return create_app(settings, database), database


class FakeSmsProvider:
    def __init__(self):
        self.messages = []

    def send(self, phone_number, message):
        self.messages.append((phone_number, message))


def phone_service(tmp_path):
    settings = Settings()
    settings.database_url = f"sqlite:///{tmp_path / 'phone-licenses.db'}"
    settings.jwt_secret = "test-jwt-secret-" + "j" * 32
    settings.license_secret = "test-license-secret-" + "l" * 32
    settings.admin_email = "owner@example.com"
    settings.developer_email = "masonrakitafx@gmail.com"
    settings.admin_bootstrap_token = "test-admin-bootstrap-token"
    database = Database(settings.database_url)
    sms = FakeSmsProvider()
    return create_app(settings, database, sms_provider=sms), database, sms


def test_phone_otp_creates_one_account_and_logs_in_again(tmp_path):
    app, database, sms = phone_service(tmp_path)
    request_code = endpoint(app, "/auth/phone/request", "POST")
    verify_code = endpoint(app, "/auth/phone/verify", "POST")
    phone, email = "+254712345678", "trader@example.com"

    result = request_code(PhoneCodeRequest(phone_number=phone, email=email))
    assert result["expires_in_seconds"] == 300
    assert sms.messages[0][0] == phone
    code = sms.messages[0][1].split("code is ", 1)[1].split(".", 1)[0]
    request = PhoneCodeVerifyRequest(phone_number=phone, email=email, code=code)

    first = verify_code(request)
    assert first["token_type"] == "bearer"
    user_id = database.one(
        "SELECT id FROM users WHERE phone_number=?",
        (phone,),
    )["id"]
    with pytest.raises(HTTPException) as consumed:
        verify_code(request)
    assert consumed.value.status_code == 401

    request_code(PhoneCodeRequest(phone_number=phone, email=email))
    code = sms.messages[1][1].split("code is ", 1)[1].split(".", 1)[0]
    verify_code(PhoneCodeVerifyRequest(phone_number=phone, email=email, code=code))
    assert database.one(
        "SELECT COUNT(*) AS count FROM users WHERE phone_number=?",
        (phone,),
    )["count"] == 1
    assert database.one(
        "SELECT id FROM users WHERE phone_number=?",
        (phone,),
    )["id"] == user_id


def test_phone_signup_receives_seven_day_ultimate_trial(tmp_path):
    app, database, sms = phone_service(tmp_path)
    request_code = endpoint(app, "/auth/phone/request", "POST")
    verify_code = endpoint(app, "/auth/phone/verify", "POST")
    phone, email = "+254712345681", "trial@example.com"
    request_code(PhoneCodeRequest(phone_number=phone, email=email))
    code = sms.messages[0][1].split("code is ", 1)[1].split(".", 1)[0]
    verify_code(PhoneCodeVerifyRequest(phone_number=phone, email=email, code=code))

    user = database.one("SELECT * FROM users WHERE phone_number=?", (phone,))
    account = endpoint(app, "/account", "GET")(user)
    subscription = account["subscription"]
    assert subscription["plan"] == "ultimate"
    assert subscription["status"] == "trialing"
    started = datetime.fromisoformat(subscription["starts_at"])
    expires = datetime.fromisoformat(subscription["expires_at"])
    assert (expires - started).days == 7
    assert account["ai_features_enabled"] is False


def test_expired_trial_is_removed_from_active_entitlement(tmp_path):
    app, database, _ = phone_service(tmp_path)
    register = endpoint(app, "/auth/register", "POST")
    register(RegisterRequest(email="expired@example.com", password="a-very-long-test-password"))
    user = database.one("SELECT * FROM users WHERE email=?", ("expired@example.com",))
    database.execute(
        "UPDATE subscriptions SET expires_at=? WHERE user_id=?",
        ("2000-01-01T00:00:00+00:00", user["id"]),
    )

    account = endpoint(app, "/account", "GET")(user)
    assert account["subscription"]["status"] == "expired"


def test_public_plans_use_requested_prices_and_ai_is_not_claimed_active(tmp_path):
    app, _, _ = phone_service(tmp_path)
    catalog = endpoint(app, "/plans", "GET")()

    assert catalog["trial_days"] == 7
    assert catalog["trial_plan"] == "ultimate"
    assert catalog["ai_features_enabled"] is False
    assert {plan["id"]: plan["monthly_price_usd"] for plan in catalog["plans"]} == {
        "pro": "40.00",
        "ultimate": "90.00",
        "platinum": "290.99",
    }


def test_admin_can_view_confirmed_and_pending_payment_activity(tmp_path):
    app, database = service(tmp_path)
    register = endpoint(app, "/auth/register", "POST")
    dashboard = endpoint(app, "/admin/dashboard/json", "GET")
    register(
        RegisterRequest(
            email="owner@example.com",
            password="new-private-owner-password",
            phone_number="+254711234567",
        )
    )
    owner = database.one("SELECT * FROM users WHERE email=?", ("owner@example.com",))
    assert owner["phone_number"] == "+254711234567"
    database.execute(
        """INSERT INTO mpesa_payments
           (id,user_id,plan,phone_number,amount_kes,checkout_request_id,
            merchant_request_id,status,receipt_number,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "payment-paid",
            owner["id"],
            "pro",
            "+254711234567",
            1200,
            "checkout-paid",
            "merchant-paid",
            "paid",
            "ABC123",
            "2026-11-11T08:00:00+00:00",
            "2026-11-11T08:01:00+00:00",
        ),
    )
    database.execute(
        """INSERT INTO mpesa_payments
           (id,user_id,plan,phone_number,amount_kes,checkout_request_id,
            merchant_request_id,status,receipt_number,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "payment-pending",
            owner["id"],
            "ultimate",
            "+254711234567",
            2400,
            "checkout-pending",
            "merchant-pending",
            "pending",
            None,
            "2026-11-11T08:02:00+00:00",
            "2026-11-11T08:02:00+00:00",
        ),
    )

    data = dashboard(owner)

    assert data["payment_summary"] == {
        "count": 2,
        "received_kes": 1200,
        "successful": 1,
        "pending": 1,
    }
    assert [payment["status"] for payment in data["payments"]] == ["pending", "paid"]
    assert data["payments"][1]["receipt_number"] == "ABC123"


def test_paid_checkout_stays_disabled_even_with_daraja_configuration(tmp_path):
    settings = Settings()
    settings.database_url = f"sqlite:///{tmp_path / 'mpesa-licenses.db'}"
    settings.jwt_secret = "test-jwt-secret-" + "j" * 32
    settings.license_secret = "test-license-secret-" + "l" * 32
    settings.payment_provider = "mpesa_daraja"
    settings.mpesa_environment = "sandbox"
    settings.mpesa_consumer_key = "test-consumer-key"
    settings.mpesa_consumer_secret = "test-consumer-secret"
    settings.mpesa_shortcode = "123456"
    settings.mpesa_passkey = "test-passkey"
    settings.mpesa_callback_token = "test-callback-token-" + "x" * 32
    settings.mpesa_callback_url = (
        "https://example.test/payments/mpesa/" + settings.mpesa_callback_token
    )
    settings.mpesa_plan_prices_kes = {
        "pro": "1000",
        "ultimate": "2000",
        "platinum": "3000",
    }
    database = Database(settings.database_url)
    app = create_app(settings, database, sms_provider=FakeSmsProvider())

    availability = endpoint(app, "/billing/availability", "GET")()
    assert availability["enabled"] is False
    assert "not open yet" in availability["message"]

    checkout = endpoint(app, "/subscriptions/checkout", "POST")
    with pytest.raises(HTTPException) as failure:
        checkout(
            SubscriptionCheckoutRequest(plan="pro"),
            {"id": "test-user", "phone_number": "+254712345678"},
        )
    assert failure.value.status_code == 503
    assert database.one("SELECT COUNT(*) AS count FROM mpesa_payments")["count"] == 0


def test_phone_otp_requires_admin_bootstrap_token(tmp_path):
    app, database, sms = phone_service(tmp_path)
    request_code = endpoint(app, "/auth/phone/request", "POST")
    verify_code = endpoint(app, "/auth/phone/verify", "POST")
    phone, email = "+254712345679", "owner@example.com"
    request_code(PhoneCodeRequest(phone_number=phone, email=email))
    code = sms.messages[0][1].split("code is ", 1)[1].split(".", 1)[0]
    request = PhoneCodeVerifyRequest(phone_number=phone, email=email, code=code)

    with pytest.raises(HTTPException) as failure:
        verify_code(request)
    assert failure.value.status_code == 403
    assert database.one("SELECT id FROM users WHERE is_admin=?", (True,)) is None

    authorized = verify_code(request, admin_bootstrap_token="test-admin-bootstrap-token")
    assert authorized["token_type"] == "bearer"
    assert database.one("SELECT id FROM users WHERE is_admin=?", (True,)) is not None


def test_phone_otp_rejects_bad_codes_and_locks_challenge(tmp_path):
    app, _, sms = phone_service(tmp_path)
    request_code = endpoint(app, "/auth/phone/request", "POST")
    verify_code = endpoint(app, "/auth/phone/verify", "POST")
    phone, email = "+254712345680", "trader@example.com"
    request_code(PhoneCodeRequest(phone_number=phone, email=email))

    for _ in range(5):
        with pytest.raises(HTTPException) as failure:
            verify_code(PhoneCodeVerifyRequest(phone_number=phone, email=email, code="000000"))
        assert failure.value.status_code == 401
    with pytest.raises(HTTPException) as locked:
        verify_code(PhoneCodeVerifyRequest(phone_number=phone, email=email, code="000000"))
    assert locked.value.status_code == 429
    assert len(sms.messages) == 1


def test_phone_numbers_accept_local_kenyan_and_international_formats():
    assert PhoneCodeRequest(
        phone_number="0711111111",
        email="trader@example.com",
    ).phone_number == "+254711111111"
    assert PhoneCodeRequest(
        phone_number="0122222222",
        email="trader@example.com",
    ).phone_number == "+254122222222"


def test_settings_normalize_developer_email(monkeypatch):
    monkeypatch.setenv("DEVELOPER_EMAIL", "MasonRakitaFx@gmail.com")

    assert Settings().developer_email == "masonrakitafx@gmail.com"


def test_africas_talking_sms_uses_api_key_and_e164_recipient(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"SMSMessageData":{"Recipients":[{"status":"Success"}]}}'

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return Response()

    monkeypatch.setattr(backend_app, "urlopen", fake_urlopen)
    AfricaTalkingSmsProvider("mytradebot", "private-api-key").send(
        "+254712345678",
        "Your sign-in code is 123456.",
    )

    request, timeout = calls[0]
    assert request.full_url == AfricaTalkingSmsProvider._ENDPOINT
    assert request.get_header("Apikey") == "private-api-key"
    assert b"to=%2B254712345678" in request.data
    assert b"123456" in request.data
    assert timeout == 15


def test_africas_talking_sms_rejects_unsuccessful_provider_response(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"SMSMessageData":{"Recipients":[{"status":"Failed"}]}}'

    monkeypatch.setattr(backend_app, "urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(RuntimeError, match="did not accept the SMS"):
        AfricaTalkingSmsProvider("mytradebot", "private-api-key").send(
            "+254712345678",
            "code",
        )


def test_pwa_shell_and_manifest_routes_are_present(tmp_path):
    app, _, _ = phone_service(tmp_path)
    root = endpoint(app, "/", "GET")()
    manifest = endpoint(app, "/manifest.webmanifest", "GET")()
    favicon = endpoint(app, "/favicon.ico", "GET")()
    worker = endpoint(app, "/service-worker.js", "GET")()
    portal = root.path.read_text()
    service_worker = worker.path.read_text()
    trading_dashboard = (
        Path(__file__).parents[1] / "web" / "templates" / "index.html"
    ).read_text()

    assert str(root.path).endswith("backend/static/index.html")
    assert str(manifest.path).endswith("manifest.webmanifest")
    assert str(favicon.path).endswith("backend/static/icon.svg")
    assert str(worker.path).endswith("service-worker.js")
    assert 'id="auth-form"' in portal
    assert '"/auth/login"' in portal
    assert '"/auth/register"' in portal
    assert 'id="admin-payments"' in portal
    assert "mytradebot-portal-v5" in service_worker
    for brand_color in ("#0D0D14", "#13131F", "#2A1F4A", "#FF6B00", "#7B2FFF", "#F0F4FF"):
        assert brand_color in portal
        assert brand_color in trading_dashboard


def test_unpaid_legacy_checkout_cannot_activate_a_license(tmp_path):
    app, database = service(tmp_path)
    register = endpoint(app, "/auth/register", "POST")
    subscribe = endpoint(app, "/subscriptions", "POST")
    validate = endpoint(app, "/licenses/validate", "POST")
    revoke = endpoint(app, "/admin/licenses/{license_id}/revoke", "POST")
    register(RegisterRequest(email="trader@example.com", password="safe-test-password"))
    trader = database.one("SELECT * FROM users WHERE email=?", ("trader@example.com",))
    created = subscribe(SubscriptionRequest(plan="monthly", mt5_account="12345678", device_id="device-identifier-0123456789"), trader)
    assert created["status"] == "pending_payment"
    with pytest.raises(HTTPException) as unpaid:
        validate(LicenseValidationRequest(mt5_account="12345678", device_id="device-identifier-0123456789"))
    assert unpaid.value.status_code == 403
    register(RegisterRequest(email="owner@example.com", password="safe-test-password"))
    owner = database.one("SELECT * FROM users WHERE email=?", ("owner@example.com",))
    assert revoke(created["license_id"], RevokeRequest(reason="customer request"), owner)["revoked"] is True
    with pytest.raises(HTTPException) as failure:
        validate(LicenseValidationRequest(mt5_account="12345678", device_id="device-identifier-0123456789"))
    assert failure.value.status_code == 403


def test_only_active_paid_subscription_can_bind_bot_license(tmp_path):
    app, database = service(tmp_path)
    register = endpoint(app, "/auth/register", "POST")
    bind = endpoint(app, "/licenses/bind", "POST")
    validate = endpoint(app, "/licenses/validate", "POST")
    register(RegisterRequest(email="trader@example.com", password="private-test-password"))
    user = database.one("SELECT * FROM users WHERE email=?", ("trader@example.com",))
    bind_request = LicenseBindRequest(
        mt5_account="12345678",
        device_id="device-identifier-0123456789",
    )

    with pytest.raises(HTTPException) as trial:
        bind(bind_request, user)
    assert trial.value.status_code == 403
    assert database.one("SELECT id FROM licenses WHERE user_id=?", (user["id"],)) is None

    now = backend_app.utc_now()
    database.execute(
        """UPDATE subscriptions SET status='active',provider='mpesa_daraja',
           expires_at=? WHERE user_id=?""",
        ((now + backend_app.timedelta(days=30)).isoformat(), user["id"]),
    )
    bound = bind(bind_request, user)
    assert bound["valid"] is True
    assert validate(
        LicenseValidationRequest(
            mt5_account="12345678",
            device_id="device-identifier-0123456789",
        )
    )["valid"] is True

    database.execute(
        "UPDATE subscriptions SET status='expired' WHERE user_id=?",
        (user["id"],),
    )
    with pytest.raises(HTTPException) as expired:
        validate(
            LicenseValidationRequest(
                mt5_account="12345678",
                device_id="device-identifier-0123456789",
            )
        )
    assert expired.value.status_code == 403


def test_existing_developer_email_gets_free_access_after_restart_without_token(tmp_path):
    settings = Settings()
    settings.database_url = f"sqlite:///{tmp_path / 'developer-licenses.db'}"
    settings.jwt_secret = "test-jwt-secret-" + "j" * 32
    settings.license_secret = "test-license-secret-" + "l" * 32
    settings.admin_email = "owner@example.com"
    settings.developer_email = "masonrakitafx@gmail.com"
    settings.admin_bootstrap_token = "test-admin-bootstrap-token"
    database = Database(settings.database_url)
    app = create_app(settings, database)
    register = endpoint(app, "/auth/register", "POST")
    email = settings.developer_email
    request = RegisterRequest(
        email=email,
        password="a-new-private-developer-password",
    )

    register(request, admin_bootstrap_token=None)
    user = database.one("SELECT * FROM users WHERE email=?", (email,))
    account = endpoint(app, "/account", "GET")(user)
    # Developer grant now happens immediately on register, not only on restart
    assert account["subscription"]["provider"] == "developer"

    restarted_app = create_app(settings, database)
    user = database.one("SELECT * FROM users WHERE email=?", (email,))
    account = endpoint(restarted_app, "/account", "GET")(user)

    assert account["subscription"]["provider"] == "developer"
    assert account["subscription"]["status"] == "active"
    assert account["subscription"]["plan"] == "platinum"
    bound = endpoint(restarted_app, "/licenses/bind", "POST")(
        LicenseBindRequest(
            mt5_account="12345678",
            device_id="developer-device-identifier-0123456789",
        ),
        user,
    )
    assert bound["valid"] is True


def test_regular_registration_needs_no_bootstrap_token_and_stays_view_only(tmp_path):
    app, database = service(tmp_path)
    register = endpoint(app, "/auth/register", "POST")
    email = "view-only@example.com"

    register(
        RegisterRequest(email=email, password="a-new-private-account-password"),
        admin_bootstrap_token=None,
    )
    user = database.one("SELECT * FROM users WHERE email=?", (email,))
    account = endpoint(app, "/account", "GET")(user)

    assert account["subscription"]["provider"] == "trial"
    assert account["phone_verified"] is False


def test_phone_verification_does_not_grant_free_bot_access(tmp_path):
    app, database, sms = phone_service(tmp_path)
    email = "owner-test@example.com"
    register = endpoint(app, "/auth/register", "POST")
    register(
        RegisterRequest(email=email, password="a-new-private-account-password"),
        admin_bootstrap_token=None,
    )
    user = database.one("SELECT * FROM users WHERE email=?", (email,))
    phone_request = PhoneCodeRequest(phone_number="0122222222", email=email)
    endpoint(app, "/account/phone/request", "POST")(phone_request, user)
    code = sms.messages[0][1].split("code is ", 1)[1].split(".", 1)[0]
    endpoint(app, "/account/phone/verify", "POST")(
        PhoneCodeVerifyRequest(
            phone_number="0122222222",
            email=email,
            code=code,
        ),
        user,
    )

    refreshed_user = database.one("SELECT * FROM users WHERE email=?", (email,))
    account = endpoint(app, "/account", "GET")(refreshed_user)
    assert account["phone_number"] == "+254122222222"
    assert account["phone_verified"] is True
    assert account["subscription"]["provider"] == "trial"


def test_license_cannot_be_bound_to_another_users_existing_device(tmp_path):
    app, database = service(tmp_path)
    register = endpoint(app, "/auth/register", "POST")
    bind = endpoint(app, "/licenses/bind", "POST")
    register(RegisterRequest(email="first@example.com", password="first-private-password"))
    register(RegisterRequest(email="second@example.com", password="second-private-password"))
    first = database.one("SELECT * FROM users WHERE email=?", ("first@example.com",))
    second = database.one("SELECT * FROM users WHERE email=?", ("second@example.com",))
    expiry = (backend_app.utc_now() + backend_app.timedelta(days=30)).isoformat()
    for user in (first, second):
        database.execute(
            """UPDATE subscriptions SET status='active',provider='mpesa_daraja',
               expires_at=? WHERE user_id=?""",
            (expiry, user["id"]),
        )
    request = LicenseBindRequest(
        mt5_account="12345678",
        device_id="device-identifier-0123456789",
    )
    bind(request, first)

    with pytest.raises(HTTPException) as conflict:
        bind(request, second)
    assert conflict.value.status_code == 409


def test_login_rejects_invalid_password(tmp_path):
    app, _ = service(tmp_path)
    register = endpoint(app, "/auth/register", "POST")
    login = endpoint(app, "/auth/login", "POST")
    register(RegisterRequest(email="trader@example.com", password="safe-test-password"))
    with pytest.raises(HTTPException) as failure:
        login(LoginRequest(email="trader@example.com", password="wrong-password-123"))
    assert failure.value.status_code == 401


def test_admin_registration_requires_bootstrap_token(tmp_path):
    app, _ = service(tmp_path)
    register = endpoint(app, "/auth/register", "POST")
    with pytest.raises(HTTPException) as failure:
        register(RegisterRequest(email="owner@example.com", password="P" * 24),
                 admin_bootstrap_token="incorrect-bootstrap-token")
    assert failure.value.status_code == 403


def test_production_refuses_manual_payments(monkeypatch):
    monkeypatch.setenv("MYTRADEBOT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/database")
    monkeypatch.setenv("JWT_SECRET", "j" * 32)
    monkeypatch.setenv("LICENSE_SIGNING_SECRET", "l" * 32)
    monkeypatch.setenv("ADMIN_BOOTSTRAP_TOKEN", "a" * 32)
    monkeypatch.setenv("PAYMENT_PROVIDER", "manual")
    with pytest.raises(RuntimeError, match="payment provider"):
        Settings()


def test_production_rejects_placeholder_secrets(monkeypatch):
    monkeypatch.setenv("MYTRADEBOT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/database")
    monkeypatch.setenv("JWT_SECRET", "replace-with-a-long-random-secret-min-32-chars")
    monkeypatch.setenv("LICENSE_SIGNING_SECRET", "l" * 40)
    monkeypatch.setenv("ADMIN_BOOTSTRAP_TOKEN", "a" * 40)
    monkeypatch.setenv("PAYMENT_PROVIDER", "stripe")

    with pytest.raises(RuntimeError, match="Production requires"):
        Settings()
