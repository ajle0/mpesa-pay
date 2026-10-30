"""
Thin client for Safaricom's Daraja M-Pesa Express (STK Push) API.

This module only talks HTTP to Safaricom. It knows nothing about the
database; business logic lives in payments/services.py.
"""

import base64
import logging
import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

NAIROBI = ZoneInfo("Africa/Nairobi")

BASE_URLS = {
    "sandbox": "https://sandbox.safaricom.co.ke",
    "production": "https://api.safaricom.co.ke",
}

# Safaricom mobile prefixes: 07XX and 01XX, nine digits after the country code.
_PHONE_RE = re.compile(r"^(?:\+?254|0)?([71]\d{8})$")


class DarajaError(Exception):
    """Safaricom rejected a request or could not be reached."""


def normalize_phone(raw: str) -> str:
    """
    Turn any common Kenyan format into 2547XXXXXXXX / 2541XXXXXXXX.

    Accepts 0712345678, 712345678, 254712345678, +254 712 345 678, etc.
    Raises ValueError for anything else.
    """
    cleaned = re.sub(r"[\s\-()]", "", raw or "")
    match = _PHONE_RE.match(cleaned)
    if not match:
        raise ValueError("Enter a valid Safaricom number, e.g. 0712345678.")
    return "254" + match.group(1)


def make_timestamp(now: datetime | None = None) -> str:
    """Daraja wants YYYYMMDDHHmmss in Kenyan time."""
    now = now or datetime.now(tz=NAIROBI)
    return now.astimezone(NAIROBI).strftime("%Y%m%d%H%M%S")


def make_password(shortcode: str, passkey: str, timestamp: str) -> str:
    """base64(shortcode + passkey + timestamp)."""
    return base64.b64encode(f"{shortcode}{passkey}{timestamp}".encode()).decode()


class DarajaClient:
    """Real client for the sandbox and production environments."""

    def __init__(self):
        self.mode = settings.MPESA_MODE
        self.base_url = BASE_URLS[self.mode]
        self.timeout = settings.MPESA_TIMEOUT_SECONDS

    # -- auth ---------------------------------------------------------------
    def _token(self) -> str:
        cache_key = f"daraja-token:{self.mode}:{settings.MPESA_CONSUMER_KEY[-6:]}"
        token = cache.get(cache_key)
        if token:
            return token

        if not (settings.MPESA_CONSUMER_KEY and settings.MPESA_CONSUMER_SECRET):
            raise DarajaError("MPESA_CONSUMER_KEY and MPESA_CONSUMER_SECRET are not set.")

        try:
            resp = requests.get(
                f"{self.base_url}/oauth/v1/generate",
                params={"grant_type": "client_credentials"},
                auth=(settings.MPESA_CONSUMER_KEY, settings.MPESA_CONSUMER_SECRET),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise DarajaError(f"Could not reach Safaricom: {exc}") from exc

        if resp.status_code != 200:
            logger.error("Daraja auth failed: %s %s", resp.status_code, resp.text[:500])
            raise DarajaError("Safaricom rejected the API credentials.")

        data = resp.json()
        token = data["access_token"]
        # Tokens last ~3600s. Refresh a minute early to avoid using a dying token.
        ttl = max(int(data.get("expires_in", 3599)) - 60, 60)
        cache.set(cache_key, token, ttl)
        return token

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        safe_payload = {k: v for k, v in payload.items() if k != "Password"}
        logger.info("Daraja request %s %s", path, safe_payload)
        try:
            resp = requests.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self._token()}"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            logger.error("Daraja request to %s failed: %s", path, exc)
            raise DarajaError(f"Could not reach Safaricom: {exc}") from exc

        try:
            data = resp.json()
        except ValueError:
            data = {"errorMessage": resp.text[:500]}
        logger.info("Daraja response %s %s %s", path, resp.status_code, data)
        return data

    # -- common payload pieces ----------------------------------------------
    def _credentials(self) -> dict:
        timestamp = make_timestamp()
        return {
            "BusinessShortCode": settings.MPESA_SHORTCODE,
            "Password": make_password(settings.MPESA_SHORTCODE, settings.MPESA_PASSKEY, timestamp),
            "Timestamp": timestamp,
        }

    # -- API ----------------------------------------------------------------
    def stk_push(self, *, phone: str, amount: int, callback_url: str, reference: str) -> dict:
        """
        Send the PIN prompt. Returns Daraja's acknowledgement, which includes
        MerchantRequestID and CheckoutRequestID. Raises DarajaError if refused.
        """
        payload = {
            **self._credentials(),
            "TransactionType": settings.MPESA_TRANSACTION_TYPE,
            "Amount": int(amount),
            "PartyA": phone,
            "PartyB": settings.MPESA_PARTY_B,
            "PhoneNumber": phone,
            "CallBackURL": callback_url,
            "AccountReference": reference[:12],
            "TransactionDesc": settings.MPESA_TRANSACTION_DESC,
        }
        data = self._post("/mpesa/stkpush/v1/processrequest", payload)
        if str(data.get("ResponseCode")) != "0":
            raise DarajaError(data.get("errorMessage") or data.get("ResponseDescription") or "STK push rejected.")
        return data

    def stk_query(self, checkout_request_id: str) -> dict:
        """
        Ask Safaricom what happened to an STK push. While the customer has
        not answered yet, Daraja returns an errorCode instead of a ResultCode.
        """
        payload = {**self._credentials(), "CheckoutRequestID": checkout_request_id}
        return self._post("/mpesa/stkpushquery/v1/query", payload)


class SimulatedDarajaClient:
    """
    Stand-in used when MPESA_MODE=simulate. Makes no network calls.
    Payments stay pending until someone clicks a simulate button, which
    feeds a realistic callback into the real callback handler.
    """

    mode = "simulate"

    def stk_push(self, *, phone: str, amount: int, callback_url: str, reference: str) -> dict:
        logger.info("Simulated STK push to %s for KES %s", phone, amount)
        suffix = uuid.uuid4().hex[:12]
        return {
            "MerchantRequestID": f"SIM-{suffix}",
            "CheckoutRequestID": f"ws_CO_SIM_{suffix}",
            "ResponseCode": "0",
            "ResponseDescription": "Success. Request accepted for processing",
            "CustomerMessage": "Success. Request accepted for processing",
        }

    def stk_query(self, checkout_request_id: str) -> dict:
        return {"errorCode": "500.001.1001", "errorMessage": "The transaction is being processed"}


def get_client():
    if settings.MPESA_MODE == "simulate":
        return SimulatedDarajaClient()
    return DarajaClient()
