import hashlib
import hmac
import json
import time

from backend.payments import MobileMoneyCallbackProvider, StripeCheckoutProvider


def stripe_provider():
    return StripeCheckoutProvider(
        "stripe-secret",
        "monthly-price",
        "annual-price",
        "https://example.com/success",
        "https://example.com/cancel",
        "webhook-secret",
    )


def stripe_signature(payload, timestamp):
    signed = str(timestamp).encode() + b"." + payload
    digest = hmac.new(b"webhook-secret", signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_stripe_webhook_requires_recent_paid_checkout():
    timestamp = int(time.time())
    payload = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {
            "payment_status": "paid",
            "metadata": {"license_id": "license-123"},
        }},
    }).encode()

    assert stripe_provider().verify_callback(payload, stripe_signature(payload, timestamp)) == "license-123"
    assert stripe_provider().verify_callback(payload, stripe_signature(payload, timestamp - 301)) is None


def test_stripe_webhook_rejects_unpaid_or_malformed_events():
    timestamp = int(time.time())
    payload = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {
            "payment_status": "unpaid",
            "metadata": {"license_id": "license-123"},
        }},
    }).encode()
    signature = stripe_signature(payload, timestamp)

    assert stripe_provider().verify_callback(payload, signature) is None
    malformed = b"{"
    assert stripe_provider().verify_callback(malformed, stripe_signature(malformed, timestamp)) is None


def test_mobile_money_callback_rejects_malformed_payload():
    provider = MobileMoneyCallbackProvider("https://checkout.example", "callback-secret")
    payload = b"{"
    signature = hmac.new(b"callback-secret", payload, hashlib.sha256).hexdigest()

    assert provider.verify_callback(payload, signature) is None
