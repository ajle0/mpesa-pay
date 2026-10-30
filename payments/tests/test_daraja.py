"""The Daraja client in isolation. No network: `requests` is mocked."""

import base64
from datetime import datetime
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from payments.daraja import (
    NAIROBI,
    DarajaClient,
    DarajaError,
    make_password,
    make_timestamp,
    normalize_phone,
)

SANDBOX = dict(
    MPESA_MODE="sandbox",
    MPESA_CONSUMER_KEY="key-123456",
    MPESA_CONSUMER_SECRET="secret",
    MPESA_SHORTCODE="174379",
    MPESA_PASSKEY="passkey",
    MPESA_PARTY_B="174379",
    MPESA_TRANSACTION_TYPE="CustomerPayBillOnline",
)


def fake_response(status=200, json=None):
    resp = mock.Mock(status_code=status, text=str(json))
    resp.json.return_value = json
    return resp


class PhoneTests(SimpleTestCase):
    def test_accepts_common_formats(self):
        for raw in ["0712345678", "712345678", "254712345678", "+254712345678", "+254 712 345 678", "0712-345-678"]:
            self.assertEqual(normalize_phone(raw), "254712345678", raw)

    def test_accepts_01_prefix(self):
        self.assertEqual(normalize_phone("0110123456"), "254110123456")

    def test_rejects_garbage(self):
        for raw in ["", "12345", "0812345678", "07123456789", "2557123456789", "abc", None]:
            with self.assertRaises(ValueError, msg=raw):
                normalize_phone(raw)


class CredentialTests(SimpleTestCase):
    def test_timestamp_is_kenyan_time(self):
        utc_noon = datetime(2026, 1, 15, 12, 0, 0, tzinfo=NAIROBI).astimezone(datetime.now().astimezone().tzinfo)
        self.assertEqual(make_timestamp(utc_noon), "20260115120000")

    def test_password_is_base64_of_shortcode_passkey_timestamp(self):
        pw = make_password("174379", "abc", "20260115120000")
        self.assertEqual(base64.b64decode(pw).decode(), "174379abc20260115120000")


@override_settings(**SANDBOX)
class ClientTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("payments.daraja.requests")
    def test_stk_push_sends_expected_payload(self, req):
        req.RequestException = Exception
        req.get.return_value = fake_response(json={"access_token": "tok", "expires_in": "3599"})
        req.post.return_value = fake_response(
            json={"MerchantRequestID": "m1", "CheckoutRequestID": "ws_CO_1", "ResponseCode": "0"}
        )

        ack = DarajaClient().stk_push(
            phone="254712345678", amount=30, callback_url="https://x.test/cb", reference="QuickPayRef12345"
        )

        self.assertEqual(ack["CheckoutRequestID"], "ws_CO_1")
        url = req.post.call_args.args[0]
        body = req.post.call_args.kwargs["json"]
        headers = req.post.call_args.kwargs["headers"]
        self.assertEqual(url, "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest")
        self.assertEqual(headers["Authorization"], "Bearer tok")
        self.assertEqual(body["PartyA"], "254712345678")
        self.assertEqual(body["PartyB"], "174379")
        self.assertEqual(body["Amount"], 30)
        self.assertEqual(body["AccountReference"], "QuickPayRef1")  # max 12 chars
        self.assertTrue(base64.b64decode(body["Password"]).decode().startswith("174379passkey"))

    @mock.patch("payments.daraja.requests")
    def test_token_is_cached(self, req):
        req.RequestException = Exception
        req.get.return_value = fake_response(json={"access_token": "tok", "expires_in": "3599"})
        req.post.return_value = fake_response(json={"ResponseCode": "0", "CheckoutRequestID": "a"})
        client = DarajaClient()
        client.stk_push(phone="254712345678", amount=1, callback_url="https://x", reference="r")
        client.stk_push(phone="254712345678", amount=1, callback_url="https://x", reference="r")
        self.assertEqual(req.get.call_count, 1)

    @mock.patch("payments.daraja.requests")
    def test_rejected_push_raises(self, req):
        req.RequestException = Exception
        req.get.return_value = fake_response(json={"access_token": "tok", "expires_in": "3599"})
        req.post.return_value = fake_response(
            status=400, json={"errorCode": "400.002.02", "errorMessage": "Bad Request - Invalid PhoneNumber"}
        )
        with self.assertRaisesMessage(DarajaError, "Invalid PhoneNumber"):
            DarajaClient().stk_push(phone="254712345678", amount=1, callback_url="https://x", reference="r")

    @mock.patch("payments.daraja.requests")
    def test_bad_credentials_raise(self, req):
        req.RequestException = Exception
        req.get.return_value = fake_response(status=400, json={})
        with self.assertRaises(DarajaError):
            DarajaClient().stk_query("ws_CO_1")
