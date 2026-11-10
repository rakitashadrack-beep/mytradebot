"""Backend business-flow tests without an external HTTP test-client dependency."""
import pytest
from fastapi import HTTPException

from backend.app import (Database, LicenseValidationRequest, LoginRequest,
                         RegisterRequest, RevokeRequest, Settings,
                         SubscriptionRequest, create_app)


def endpoint(app, path, method):
    route_endpoint = next(route.endpoint for route in app.routes if route.path == path and method in route.methods)
    if path == "/auth/register":
        def register(request, admin_bootstrap_token="test-admin-bootstrap-token"):
            return route_endpoint(request, admin_bootstrap_token=admin_bootstrap_token)
        return register
    return route_endpoint


def service(tmp_path):
    settings = Settings()
    settings.database_url = f"sqlite:///{tmp_path / 'licenses.db'}"
    settings.jwt_secret = "test-jwt-secret-" + "j" * 32
    settings.license_secret = "test-license-secret-" + "l" * 32
    settings.admin_email = "owner@example.com"
    settings.admin_bootstrap_token = "test-admin-bootstrap-token"
    database = Database(settings.database_url)
    return create_app(settings, database), database


def test_registration_subscription_validation_and_revocation(tmp_path):
    app, database = service(tmp_path)
    register = endpoint(app, "/auth/register", "POST")
    subscribe = endpoint(app, "/subscriptions", "POST")
    validate = endpoint(app, "/licenses/validate", "POST")
    revoke = endpoint(app, "/admin/licenses/{license_id}/revoke", "POST")
    register(RegisterRequest(email="trader@example.com", password="safe-test-password"))
    trader = database.one("SELECT * FROM users WHERE email=?", ("trader@example.com",))
    created = subscribe(SubscriptionRequest(plan="monthly", mt5_account="12345678", device_id="device-identifier-0123456789"), trader)
    assert validate(LicenseValidationRequest(mt5_account="12345678", device_id="device-identifier-0123456789"))["valid"] is True
    register(RegisterRequest(email="owner@example.com", password="safe-test-password"))
    owner = database.one("SELECT * FROM users WHERE email=?", ("owner@example.com",))
    assert revoke(created["license_id"], RevokeRequest(reason="customer request"), owner)["revoked"] is True
    with pytest.raises(HTTPException) as failure:
        validate(LicenseValidationRequest(mt5_account="12345678", device_id="device-identifier-0123456789"))
    assert failure.value.status_code == 403


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
