"""Desktop license validation with an intentionally short offline grace period."""
from __future__ import annotations

import hashlib
import json
import platform
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


def device_id() -> str:
    """Return a stable pseudonymous device identifier; never sends personal details."""
    source = f"{uuid.getnode()}:{platform.node()}:{platform.system()}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LicenseStatus:
    allowed: bool
    message: str
    checked_at: datetime | None = None


class LicenseManager:
    """Checks a configured licensing API every few hours, with a 72-hour grace cache."""
    def __init__(self, database, api_url: str = "", offline_grace_hours: int = 72,
                 request_json=None) -> None:
        self.database = database
        self.api_url = api_url.rstrip("/")
        self.offline_grace = timedelta(hours=offline_grace_hours)
        self.request_json = request_json or self._request_json

    @property
    def enabled(self) -> bool:
        return bool(self.api_url)

    def validate(self, mt5_account: int | str) -> LicenseStatus:
        if not self.enabled:
            return LicenseStatus(True, "License checks are not configured for this development installation")
        now = datetime.now(timezone.utc)
        try:
            response = self.request_json(f"{self.api_url}/licenses/validate", {
                "mt5_account": str(mt5_account), "device_id": device_id(),
            })
            if response.get("valid") is True:
                self.database.set_setting("license_last_validated_at", now.isoformat())
                self.database.set_setting("license_token", response.get("license_token", ""))
                self.database.set_setting("license_expires_at", response.get("expires_at", ""))
                return LicenseStatus(True, "License validated", now)
            return LicenseStatus(False, "License validation was refused")
        except (OSError, ValueError, urllib.error.URLError):
            return self._offline_status(now)

    def _offline_status(self, now: datetime) -> LicenseStatus:
        stored = self.database.get_setting("license_last_validated_at", "")
        try:
            checked = datetime.fromisoformat(stored)
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return LicenseStatus(False, "License server unavailable and no prior validation exists")
        expires_at = self.database.get_setting("license_expires_at", "")
        try:
            expires = datetime.fromisoformat(expires_at)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return LicenseStatus(False, "License server unavailable and cached license expiry is invalid")
        if now >= expires:
            return LicenseStatus(False, "Cached license has expired")
        if now - checked <= self.offline_grace:
            return LicenseStatus(True, "License server unavailable; using offline grace period", checked)
        return LicenseStatus(False, "License server unavailable and offline grace period expired", checked)

    @staticmethod
    def _request_json(url: str, payload: dict) -> dict:
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status != 200:
                raise OSError(f"License service returned HTTP {response.status}")
            return json.loads(response.read().decode("utf-8"))
