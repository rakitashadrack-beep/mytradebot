"""Desktop license validation; automated trading fails closed without paid access."""
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
    """Checks a configured licensing API and fails closed when it is unavailable."""
    def __init__(self, database, api_url: str = "", offline_grace_hours: int = 0,
                 request_json=None, request_authorized_json=None) -> None:
        self.database = database
        self.api_url = api_url.rstrip("/")
        self.offline_grace = timedelta(hours=offline_grace_hours)
        self.request_json = request_json or self._request_json
        self.request_authorized_json = request_authorized_json or self._request_authorized_json

    @property
    def enabled(self) -> bool:
        return bool(self.api_url)

    def validate(self, mt5_account: int | str) -> LicenseStatus:
        if not self.enabled:
            return LicenseStatus(False, "Set the paid subscription API URL in Settings before starting the bot")
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
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403, 404}:
                return LicenseStatus(False, "Active paid or developer access is required to run the bot")
            return self._offline_status(now)
        except (OSError, ValueError, urllib.error.URLError):
            return self._offline_status(now)

    def activate(self, mt5_account: int | str, email: str, password: str) -> LicenseStatus:
        if not self.enabled:
            return LicenseStatus(False, "Set the paid subscription API URL in Settings before signing in")
        try:
            login = self.request_json(f"{self.api_url}/auth/login", {
                "email": email,
                "password": password,
            })
            token = login.get("access_token")
            if not isinstance(token, str) or not token:
                return LicenseStatus(False, "The account service returned an invalid sign-in response")
            result = self.request_authorized_json(
                f"{self.api_url}/licenses/bind",
                {"mt5_account": str(mt5_account), "device_id": device_id()},
                {"Authorization": f"Bearer {token}"},
            )
            if result.get("valid") is True:
                return LicenseStatus(True, "Bot access activated for this MT5 account and device")
            return LicenseStatus(False, "The account service did not activate this device")
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return LicenseStatus(False, "Email or password is incorrect")
            if exc.code == 403:
                return LicenseStatus(False, "Active paid or developer access is required to run the bot")
            if exc.code == 409:
                return LicenseStatus(False, "This MT5 account and device are already linked to another account")
            return LicenseStatus(False, f"Subscription service returned HTTP {exc.code}")
        except (OSError, ValueError, urllib.error.URLError):
            return LicenseStatus(False, "Could not reach the subscription service")

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
        return LicenseManager._request_with_headers(url, payload, {})

    @staticmethod
    def _request_authorized_json(url: str, payload: dict, headers: dict[str, str]) -> dict:
        return LicenseManager._request_with_headers(url, payload, headers)

    @staticmethod
    def _request_with_headers(url: str, payload: dict, headers: dict[str, str]) -> dict:
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json", **headers}, method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status != 200:
                raise OSError(f"License service returned HTTP {response.status}")
            return json.loads(response.read().decode("utf-8"))
