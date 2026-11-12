import base64
import re

import pytest

from backend.mpesa import DarajaClient


def client():
    return DarajaClient(
        environment="sandbox",
        consumer_key="test-key",
        consumer_secret="test-secret",
        shortcode="123456",
        passkey="test-passkey",
        callback_url="https://example.test/payments/mpesa/" + "x" * 40,
        transaction_type="CustomerPayBillOnline",
        plan_prices_kes={"pro": 1000, "ultimate": 2000, "platinum": 3000},
    )


def test_stk_push_uses_configured_kes_amount_and_customer_phone(monkeypatch):
    daraja = client()
    calls = []

    monkeypatch.setattr(daraja, "_access_token", lambda: "test-access-token")

    def post_json(url, payload, headers):
        calls.append((url, payload, headers))
        return {
            "ResponseCode": "0",
            "MerchantRequestID": "merchant-1",
            "CheckoutRequestID": "checkout-1",
            "CustomerMessage": "Check your phone",
        }

    monkeypatch.setattr(daraja, "_post_json", post_json)
    result = daraja.start_checkout("+254712345678", "pro", "ACCOUNT123")

    url, payload, headers = calls[0]
    assert url == "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
    assert payload["Amount"] == 1000
    assert payload["PartyA"] == "254712345678"
    assert payload["PhoneNumber"] == "254712345678"
    assert payload["CallBackURL"] == daraja.callback_url
    assert re.fullmatch(r"\d{14}", payload["Timestamp"])
    assert payload["Password"] == base64.b64encode(
        f"123456test-passkey{payload['Timestamp']}".encode()
    ).decode()
    assert headers["Authorization"] == "Bearer test-access-token"
    assert result.checkout_request_id == "checkout-1"
    assert result.merchant_request_id == "merchant-1"
    assert result.customer_message == "Check your phone"


def test_stk_push_rejects_non_kenyan_number_before_request(monkeypatch):
    daraja = client()
    monkeypatch.setattr(
        daraja,
        "_access_token",
        lambda: pytest.fail("invalid phone number must not contact Daraja"),
    )

    with pytest.raises(ValueError, match="valid Kenyan mobile number"):
        daraja.start_checkout("+14155550123", "pro", "ACCOUNT123")


def test_stk_push_rejects_unaccepted_daraja_response(monkeypatch):
    daraja = client()
    monkeypatch.setattr(daraja, "_access_token", lambda: "test-access-token")
    monkeypatch.setattr(
        daraja,
        "_post_json",
        lambda *_args, **_kwargs: {"ResponseCode": "1"},
    )

    with pytest.raises(RuntimeError, match="did not accept"):
        daraja.start_checkout("+254712345678", "pro", "ACCOUNT123")


def test_daraja_configuration_requires_https_callback_and_valid_shortcode():
    with pytest.raises(ValueError, match="HTTPS"):
        DarajaClient(
            environment="sandbox",
            consumer_key="test-key",
            consumer_secret="test-secret",
            shortcode="123456",
            passkey="test-passkey",
            callback_url="http://example.test/callback",
            transaction_type="CustomerPayBillOnline",
            plan_prices_kes={"pro": 1000, "ultimate": 2000, "platinum": 3000},
        )

    with pytest.raises(ValueError, match="shortcode"):
        DarajaClient(
            environment="sandbox",
            consumer_key="test-key",
            consumer_secret="test-secret",
            shortcode="invalid",
            passkey="test-passkey",
            callback_url="https://example.test/callback",
            transaction_type="CustomerPayBillOnline",
            plan_prices_kes={"pro": 1000, "ultimate": 2000, "platinum": 3000},
        )
