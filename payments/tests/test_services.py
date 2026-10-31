"""Payment lifecycle: starting payments, callbacks, status queries and timeouts."""

from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from payments import services
from payments.daraja import DarajaError
from payments.models import Transaction

Status = Transaction.Status


def pay(phone="0712345678", amount="30", **kw):
    return services.initiate_payment(raw_amount=amount, raw_phone=phone, **kw)


def callback(txn, code=0, amount=None, receipt="TKA1B2C3D4"):
    cb = {
        "MerchantRequestID": txn.merchant_request_id,
        "CheckoutRequestID": txn.checkout_request_id,
        "ResultCode": code,
        "ResultDesc": "desc",
    }
    if code == 0:
        cb["CallbackMetadata"] = {
            "Item": [
                {"Name": "Amount", "Value": float(txn.amount if amount is None else amount)},
                {"Name": "MpesaReceiptNumber", "Value": receipt},
                {"Name": "Balance"},
                {"Name": "TransactionDate", "Value": 20261030105600},
                {"Name": "PhoneNumber", "Value": int(txn.phone)},
            ]
        }
    return {"Body": {"stkCallback": cb}}


class ParseAmountTests(TestCase):
    def test_accepts_common_formats(self):
        for raw, expected in [
            ("500", 500),
            ("1,500", 1500),
            ("Ksh 1 500", 1500),
            ("KES2500", 2500),
            ("250.00", 250),
            (" 7 ", 7),
        ]:
            self.assertEqual(services.parse_amount(raw), expected, raw)

    def test_rejects_non_amounts(self):
        for raw in ["", "abc", "-5", "10.50", "1e3", None]:
            with self.assertRaises(services.PaymentError, msg=raw):
                services.parse_amount(raw)

    @override_settings(PAY_MIN_AMOUNT=10, PAY_MAX_AMOUNT=1000)
    def test_limits(self):
        self.assertEqual(services.parse_amount("10"), 10)
        self.assertEqual(services.parse_amount("1000"), 1000)
        with self.assertRaisesMessage(services.PaymentError, "minimum amount is Ksh 10"):
            services.parse_amount("9")
        with self.assertRaisesMessage(services.PaymentError, "maximum amount is Ksh 1,000"):
            services.parse_amount("1001")
        with self.assertRaises(services.PaymentError):
            services.parse_amount("0")


@override_settings(MPESA_MODE="simulate")
class InitiatePaymentTests(TestCase):
    def test_creates_pending_transaction(self):
        txn = pay(amount="1,250", client_ip="1.2.3.4")
        self.assertEqual(txn.status, Status.PENDING)
        self.assertEqual(txn.amount, 1250)
        self.assertEqual(txn.phone, "254712345678")
        self.assertTrue(txn.checkout_request_id.startswith("ws_CO_SIM_"))

    def test_invalid_phone(self):
        with self.assertRaises(services.PaymentError):
            pay(phone="123")
        self.assertFalse(Transaction.objects.exists())

    def test_invalid_amount_creates_nothing(self):
        with self.assertRaises(services.PaymentError):
            pay(amount="0")
        self.assertFalse(Transaction.objects.exists())

    def test_amount_sent_to_mpesa_is_the_validated_one(self):
        client = mock.Mock()
        client.stk_push.return_value = {"MerchantRequestID": "m", "CheckoutRequestID": "c", "ResponseCode": "0"}
        with mock.patch.object(services, "get_client", return_value=client):
            pay(amount="Ksh 2,000")
        self.assertEqual(client.stk_push.call_args.kwargs["amount"], 2000)

    @override_settings(PAY_RATE_LIMIT_PER_PHONE=2)
    def test_rate_limit_per_phone(self):
        for _ in range(2):
            pay()
        with self.assertRaisesMessage(services.PaymentError, "Too many"):
            pay()
        # A different number is unaffected.
        pay(phone="0722000000")

    @override_settings(PAY_RATE_LIMIT_PER_IP=2)
    def test_rate_limit_per_ip(self):
        pay(phone="0711111111", client_ip="9.9.9.9")
        pay(phone="0722222222", client_ip="9.9.9.9")
        with self.assertRaises(services.PaymentError):
            pay(phone="0733333333", client_ip="9.9.9.9")

    def test_daraja_refusal_marks_failed(self):
        client = mock.Mock()
        client.stk_push.side_effect = DarajaError("boom")
        with mock.patch.object(services, "get_client", return_value=client):
            with self.assertRaises(services.PaymentError):
                pay()
        txn = Transaction.objects.get()
        self.assertEqual(txn.status, Status.FAILED)
        self.assertEqual(txn.result_desc, "boom")


@override_settings(MPESA_MODE="simulate")
class CallbackTests(TestCase):
    def setUp(self):
        self.txn = pay()

    def test_success(self):
        services.process_callback(callback(self.txn))
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, Status.SUCCESS)
        self.assertEqual(self.txn.mpesa_receipt, "TKA1B2C3D4")
        self.assertEqual(self.txn.mpesa_transaction_date.year, 2026)

    def test_cancelled(self):
        services.process_callback(callback(self.txn, code=1032))
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, Status.CANCELLED)
        self.assertEqual(self.txn.customer_message, "You cancelled the M-Pesa prompt.")

    def test_insufficient_funds_is_failed(self):
        services.process_callback(callback(self.txn, code=1))
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, Status.FAILED)

    def test_amount_mismatch_is_never_success(self):
        services.process_callback(callback(self.txn, amount=1))
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, Status.FAILED)
        self.assertIn("mismatch", self.txn.result_desc)

    def test_duplicate_callback_is_ignored(self):
        services.process_callback(callback(self.txn))
        services.process_callback(callback(self.txn, code=1032))
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, Status.SUCCESS)

    def test_unknown_and_malformed_callbacks(self):
        self.assertIsNone(
            services.process_callback({"Body": {"stkCallback": {"CheckoutRequestID": "nope", "ResultCode": 0}}})
        )
        self.assertIsNone(services.process_callback({"hello": "world"}))

    def test_late_callback_overrides_our_own_timeout(self):
        Transaction.objects.filter(pk=self.txn.pk).update(status=Status.TIMEOUT)
        services.process_callback(callback(self.txn))
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, Status.SUCCESS)


@override_settings(MPESA_MODE="simulate", PAY_PENDING_EXPIRY_SECONDS=180)
class RefreshStatusTests(TestCase):
    def setUp(self):
        self.txn = pay()

    def age(self, seconds):
        Transaction.objects.filter(pk=self.txn.pk).update(created_at=timezone.now() - timedelta(seconds=seconds))
        self.txn.refresh_from_db()

    def query_returns(self, data):
        client = mock.Mock()
        client.stk_query.return_value = data
        return mock.patch.object(services, "get_client", return_value=client)

    def test_not_queried_while_fresh(self):
        with self.query_returns({"ResultCode": "0"}) as get_client:
            services.refresh_status(self.txn)
        get_client.assert_not_called()

    def test_query_success_then_callback_adds_receipt(self):
        self.age(30)
        with self.query_returns({"ResultCode": "0", "ResultDesc": "ok"}):
            txn = services.refresh_status(self.txn)
        self.assertEqual(txn.status, Status.SUCCESS)
        self.assertEqual(txn.mpesa_receipt, "")

        services.process_callback(callback(txn, receipt="TKLATE1234"))
        txn.refresh_from_db()
        self.assertEqual(txn.mpesa_receipt, "TKLATE1234")

    def test_still_processing_stays_pending(self):
        self.age(30)
        with self.query_returns({"errorCode": "500.001.1001", "errorMessage": "The transaction is being processed"}):
            txn = services.refresh_status(self.txn)
        self.assertEqual(txn.status, Status.PENDING)
        self.assertIsNotNone(txn.last_queried_at)

    def test_queries_are_throttled(self):
        self.age(30)
        with self.query_returns({"errorCode": "500.001.1001"}) as get_client:
            txn = services.refresh_status(self.txn)
            services.refresh_status(txn)
        self.assertEqual(get_client.call_count, 1)

    def test_expires_after_timeout(self):
        self.age(200)
        with self.query_returns({"errorCode": "500.001.1001"}):
            txn = services.refresh_status(self.txn)
        self.assertEqual(txn.status, Status.TIMEOUT)
