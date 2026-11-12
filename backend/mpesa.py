"""Safaricom Daraja STK Push client; credentials and KES prices come from deployment."""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class MpesaCheckout:
    merchant_request_id: str
    checkout_request_id: str
    customer_message: str


class DarajaClient:
    _HOSTS = {
        "sandbox": "https://sandbox.safaricom.co.ke",
        "production": "https://api.safaricom.co.ke",
    }

    def __init__(
        self,
        *,
        environment: str,
        consumer_key: str,
        consumer_secret: str,
        shortcode: str,
        passkey: str,
        callback_url: str,
        transaction_type: str,
        plan_prices_kes: dict[str, int],
    ) -> None:
        if environment not in self._HOSTS:
            raise ValueError("M-Pesa environment must be sandbox or production")
        if transaction_type not in {"CustomerPayBillOnline", "CustomerBuyGoodsOnline"}:
            raise ValueError("Unsupported M-Pesa transaction type")
        if not callback_url.startswith("https://"):
            raise ValueError("M-Pesa callback URL must use HTTPS")
        if not re.fullmatch(r"\d{5,7}", shortcode):
            raise ValueError("M-Pesa shortcode must contain 5-7 digits")
        if set(plan_prices_kes) != {"pro", "ultimate", "platinum"}:
            raise ValueError("KES prices are required for all three subscription plans")
        if any(not isinstance(price, int) or price < 1 for price in plan_prices_kes.values()):
            raise ValueError("M-Pesa plan prices must be positive whole KES amounts")
        self.environment = environment
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.shortcode = shortcode
        self.passkey = passkey
        self.callback_url = callback_url
        self.transaction_type = transaction_type
        self.plan_prices_kes = dict(plan_prices_kes)

    def start_checkout(self, phone_number: str, plan: str, account_reference: str) -> MpesaCheckout:
        if plan not in self.plan_prices_kes:
            raise ValueError("Unknown M-Pesa subscription plan")
        if not re.fullmatch(r"\+254[17]\d{8}", phone_number):
            raise ValueError("M-Pesa checkout requires a valid Kenyan mobile number in +254 format")
        phone = phone_number[1:]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        password = base64.b64encode(
            f"{self.shortcode}{self.passkey}{timestamp}".encode()
        ).decode()
        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": self.transaction_type,
            "Amount": self.plan_prices_kes[plan],
            "PartyA": phone,
            "PartyB": self.shortcode,
            "PhoneNumber": phone,
            "CallBackURL": self.callback_url,
            "AccountReference": account_reference[:12],
            "TransactionDesc": f"MyTradeBot {plan.title()}",
        }
        token = self._access_token()
        response = self._post_json(
            f"{self._HOSTS[self.environment]}/mpesa/stkpush/v1/processrequest",
            payload,
            {"Authorization": f"Bearer {token}"},
        )
        if response.get("ResponseCode") != "0":
            raise RuntimeError("Safaricom did not accept the M-Pesa checkout request")
        checkout_id = response.get("CheckoutRequestID")
        merchant_id = response.get("MerchantRequestID")
        if not isinstance(checkout_id, str) or not checkout_id:
            raise RuntimeError("Safaricom response did not include a checkout ID")
        if not isinstance(merchant_id, str) or not merchant_id:
            raise RuntimeError("Safaricom response did not include a merchant request ID")
        return MpesaCheckout(
            merchant_request_id=merchant_id,
            checkout_request_id=checkout_id,
            customer_message=str(response.get("CustomerMessage", "Approve the payment prompt on your phone.")),
        )

    def _access_token(self) -> str:
        credentials = base64.b64encode(
            f"{self.consumer_key}:{self.consumer_secret}".encode()
        ).decode()
        url = f"{self._HOSTS[self.environment]}/oauth/v1/generate?{urlencode({'grant_type': 'client_credentials'})}"
        response = self._get_json(url, {"Authorization": f"Basic {credentials}"})
        token = response.get("access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Safaricom did not return an access token")
        return token

    @staticmethod
    def _get_json(url: str, headers: dict[str, str]) -> dict:
        request = Request(url, headers={"Accept": "application/json", **headers})
        try:
            with urlopen(request, timeout=15) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("Safaricom API request failed") from exc
        if not isinstance(body, dict):
            raise RuntimeError("Safaricom returned an invalid response")
        return body

    @staticmethod
    def _post_json(url: str, payload: dict, headers: dict[str, str]) -> dict:
        request = Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                **headers,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("Safaricom API request failed") from exc
        if not isinstance(body, dict):
            raise RuntimeError("Safaricom returned an invalid response")
        return body
