"""Payment-provider boundary: Stripe Checkout plus a signed mobile-money adapter."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Checkout:
    provider: str
    reference: str
    checkout_url: str | None
    paid: bool = False


class PaymentProvider(Protocol):
    name: str
    def create_checkout(self, reference: str, plan: str, customer_email: str) -> Checkout: ...
    def verify_callback(self, payload: bytes, signature: str | None) -> str | None: ...


class ManualProvider:
    """Local-development provider that activates only when an admin provisions it."""
    name = "manual"

    def create_checkout(self, reference: str, plan: str, customer_email: str) -> Checkout:
        return Checkout(self.name, reference, None, False)

    def verify_callback(self, payload: bytes, signature: str | None) -> str | None:
        return None


class StripeCheckoutProvider:
    name = "stripe"

    def __init__(self, secret_key: str, monthly_price_id: str, annual_price_id: str, success_url: str, cancel_url: str,
                 webhook_secret: str) -> None:
        self.secret_key, self.prices = secret_key, {"monthly": monthly_price_id, "annual": annual_price_id}
        self.success_url, self.cancel_url, self.webhook_secret = success_url, cancel_url, webhook_secret

    def create_checkout(self, reference: str, plan: str, customer_email: str) -> Checkout:
        body = urllib.parse.urlencode({"mode": "subscription", "customer_email": customer_email,
                                       "line_items[0][price]": self.prices[plan], "line_items[0][quantity]": "1",
                                       "success_url": self.success_url, "cancel_url": self.cancel_url,
                                       "metadata[license_id]": reference}).encode()
        request = urllib.request.Request("https://api.stripe.com/v1/checkout/sessions", data=body,
            headers={"Authorization": f"Bearer {self.secret_key}", "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode())
        return Checkout(self.name, result["id"], result["url"])

    def verify_callback(self, payload: bytes, signature: str | None) -> str | None:
        # Stripe signs the raw body with a timestamp to limit replay attacks.
        if not signature: return None
        fields: dict[str, list[str]] = {}
        for item in signature.split(","):
            key, separator, value = item.partition("=")
            if separator:
                fields.setdefault(key, []).append(value)
        timestamps = fields.get("t", [])
        if not timestamps:
            return None
        try:
            timestamp = int(timestamps[0])
        except ValueError:
            return None
        if abs(time.time() - timestamp) > 300:
            return None
        signed_payload = str(timestamp).encode() + b"." + payload
        expected = hmac.new(self.webhook_secret.encode(), signed_payload, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, value) for value in fields.get("v1", [])):
            return None
        try:
            event = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if event.get("type") != "checkout.session.completed": return None
        session = event.get("data", {}).get("object", {})
        if session.get("payment_status") != "paid":
            return None
        license_id = session.get("metadata", {}).get("license_id")
        return license_id if isinstance(license_id, str) and license_id else None


class MobileMoneyCallbackProvider:
    """Adapter for a provider that redirects customers then sends HMAC-signed callbacks."""
    name = "mobile_money"

    def __init__(self, checkout_base_url: str, callback_secret: str) -> None:
        self.checkout_base_url, self.callback_secret = checkout_base_url.rstrip("/"), callback_secret

    def create_checkout(self, reference: str, plan: str, customer_email: str) -> Checkout:
        query = urllib.parse.urlencode({"reference": reference, "plan": plan, "email": customer_email})
        return Checkout(self.name, reference, f"{self.checkout_base_url}?{query}")

    def verify_callback(self, payload: bytes, signature: str | None) -> str | None:
        expected = hmac.new(self.callback_secret.encode(), payload, hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature): return None
        try:
            body = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        reference = body.get("reference")
        return reference if body.get("status") == "paid" and isinstance(reference, str) and reference else None
