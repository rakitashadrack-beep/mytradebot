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
    manager = LicenseManager(db, "https://licenses.example", request_json=lambda url, payload: (_ for _ in ()).throw(OSError()))
    assert not manager.validate(12345678).allowed


def test_offline_grace_does_not_extend_expired_license(tmp_path):
    db = Database(str(tmp_path / "license.db"))
    now = datetime.now(timezone.utc)
    db.set_setting("license_last_validated_at", (now - timedelta(hours=1)).isoformat())
    db.set_setting("license_expires_at", (now - timedelta(minutes=1)).isoformat())
    manager = LicenseManager(db, "https://licenses.example", request_json=lambda url, payload: (_ for _ in ()).throw(OSError()))

    status = manager.validate(12345678)

    assert not status.allowed
    assert status.message == "Cached license has expired"


def test_unconfigured_development_license_does_not_block(tmp_path):
    status = LicenseManager(Database(str(tmp_path / "license.db"))).validate(12345678)
    assert status.allowed
