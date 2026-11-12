from datetime import datetime, timedelta, timezone

from core.database import Database
from core.licensing import LicenseManager


def test_valid_license_response_is_cached(tmp_path):
    db = Database(str(tmp_path / "license.db"))
    manager = LicenseManager(db, "https://licenses.example", request_json=lambda url, payload: {
        "valid": True, "license_token": "signed-token", "expires_at": "2030-01-01T00:00:00+00:00"
    })
    status = manager.validate(12345678)
    assert status.allowed
    assert db.get_setting("license_token") == "signed-token"


def test_offline_grace_expires(tmp_path):
    db = Database(str(tmp_path / "license.db"))
    now = datetime.now(timezone.utc)
    db.set_setting("license_last_validated_at", (now - timedelta(hours=73)).isoformat())
    db.set_setting("license_expires_at", (now + timedelta(days=30)).isoformat())
    manager = LicenseManager(
        db,
        "https://licenses.example",
        offline_grace_hours=72,
        request_json=lambda url, payload: (_ for _ in ()).throw(OSError()),
    )
    assert not manager.validate(12345678).allowed


def test_offline_grace_does_not_extend_expired_license(tmp_path):
    db = Database(str(tmp_path / "license.db"))
    now = datetime.now(timezone.utc)
    db.set_setting("license_last_validated_at", (now - timedelta(hours=1)).isoformat())
    db.set_setting("license_expires_at", (now - timedelta(minutes=1)).isoformat())
    manager = LicenseManager(
        db,
        "https://licenses.example",
        offline_grace_hours=72,
        request_json=lambda url, payload: (_ for _ in ()).throw(OSError()),
    )

    status = manager.validate(12345678)

    assert not status.allowed
    assert status.message == "Cached license has expired"


def test_unconfigured_license_fails_closed(tmp_path):
    status = LicenseManager(Database(str(tmp_path / "license.db"))).validate(12345678)
    assert not status.allowed
    assert "API URL" in status.message


def test_paid_license_fails_closed_on_network_error_by_default(tmp_path):
    db = Database(str(tmp_path / "license.db"))
    now = datetime.now(timezone.utc)
    db.set_setting("license_last_validated_at", now.isoformat())
    db.set_setting("license_expires_at", (now + timedelta(days=30)).isoformat())
    manager = LicenseManager(
        db,
        "https://licenses.example",
        request_json=lambda url, payload: (_ for _ in ()).throw(OSError()),
    )

    status = manager.validate(12345678)

    assert not status.allowed


def test_activate_logs_in_and_binds_paid_account_to_device(tmp_path):
    calls = []

    def request_json(url, payload):
        calls.append(("public", url, payload))
        return {"access_token": "temporary-access-token"}

    def request_authorized_json(url, payload, headers):
        calls.append(("authorized", url, payload, headers))
        return {"valid": True}

    manager = LicenseManager(
        Database(str(tmp_path / "license.db")),
        "https://licenses.example",
        request_json=request_json,
        request_authorized_json=request_authorized_json,
    )

    result = manager.activate(12345678, "trader@example.com", "user-password")

    assert result.allowed
    assert calls[0] == (
        "public",
        "https://licenses.example/auth/login",
        {"email": "trader@example.com", "password": "user-password"},
    )
    assert calls[1][0:3] == (
        "authorized",
        "https://licenses.example/licenses/bind",
        {"mt5_account": "12345678", "device_id": calls[1][2]["device_id"]},
    )
    assert len(calls[1][2]["device_id"]) == 64
    assert calls[1][3] == {"Authorization": "Bearer temporary-access-token"}
