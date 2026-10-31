"""
Payment business logic. Views call these functions; these functions call
the Daraja client. The database is the source of truth for every payment.
"""

import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction as db_transaction
from django.urls import reverse
from django.utils import timezone

from .daraja import NAIROBI, DarajaError, get_client, normalize_phone
from .models import Transaction

logger = logging.getLogger(__name__)

Status = Transaction.Status

# Daraja ResultCodes worth naming. Anything else non-zero is a plain failure.
RESULT_SUCCESS = 0
RESULT_INSUFFICIENT_FUNDS = 1
RESULT_CANCELLED = 1032
RESULT_UNREACHABLE = 1037  # phone off / prompt timed out on the handset
RESULT_WRONG_PIN = 2001

# Don't query Safaricom before the customer has had a chance to answer,
# and don't hammer the query endpoint while the status page polls.
QUERY_AFTER_SECONDS = 20
QUERY_MIN_INTERVAL_SECONDS = 10


class PaymentError(Exception):
    """A problem worth showing to the customer as-is."""


def status_for_result(code: int) -> str:
    if code == RESULT_SUCCESS:
        return Status.SUCCESS
    if code == RESULT_CANCELLED:
        return Status.CANCELLED
    if code == RESULT_UNREACHABLE:
        return Status.TIMEOUT
    return Status.FAILED


def callback_url() -> str:
    path = reverse("payments:mpesa_callback", args=[settings.MPESA_CALLBACK_SECRET])
    base = settings.MPESA_CALLBACK_BASE_URL
    if not base:
        if settings.MPESA_MODE != "simulate":
            raise PaymentError("Payments are not configured yet (missing MPESA_CALLBACK_BASE_URL).")
        base = "http://localhost"
    return base + path


# --------------------------------------------------------------------------
# Starting a payment
# --------------------------------------------------------------------------
def _check_rate_limits(phone: str, client_ip: str | None) -> None:
    since = timezone.now() - timedelta(minutes=settings.PAY_RATE_LIMIT_WINDOW_MINUTES)
    recent = Transaction.objects.filter(created_at__gte=since)
    if recent.filter(phone=phone).count() >= settings.PAY_RATE_LIMIT_PER_PHONE:
        raise PaymentError("Too many payment requests for this number. Please wait a few minutes.")
    if client_ip and recent.filter(client_ip=client_ip).count() >= settings.PAY_RATE_LIMIT_PER_IP:
        raise PaymentError("Too many payment requests. Please wait a few minutes.")


def parse_amount(raw) -> int:
    """
    Turn what the customer typed into whole shillings.

    Accepts "500", "1,500", "Ksh 1 500" and "250.00". M-Pesa only moves whole
    shillings, so cents are rejected rather than silently rounded.
    """
    cleaned = str(raw or "").strip().lower().replace(",", "").replace(" ", "")
    for prefix in ("kes", "ksh", "sh"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    if cleaned.endswith(".00"):
        cleaned = cleaned[:-3]
    elif cleaned.endswith(".0"):
        cleaned = cleaned[:-2]
    if not cleaned.isdigit():
        raise PaymentError("Enter an amount in whole shillings, e.g. 500.")

    amount = int(cleaned)
    lo, hi = settings.PAY_MIN_AMOUNT, settings.PAY_MAX_AMOUNT
    if amount < lo:
        raise PaymentError(f"The minimum amount is Ksh {lo:,}.")
    if amount > hi:
        raise PaymentError(f"The maximum amount is Ksh {hi:,}.")
    return amount


def initiate_payment(*, raw_amount, raw_phone: str, client_ip: str | None = None) -> Transaction:
    """
    Validate, record a PENDING transaction, then send the STK prompt.
    The amount is re-validated here on the server, whatever the browser sent.
    """
    amount = parse_amount(raw_amount)
    try:
        phone = normalize_phone(raw_phone)
    except ValueError as exc:
        raise PaymentError(str(exc)) from exc

    _check_rate_limits(phone, client_ip)
    url = callback_url()

    txn = Transaction.objects.create(phone=phone, amount=amount, client_ip=client_ip)
    try:
        ack = get_client().stk_push(
            phone=phone, amount=txn.amount, callback_url=url, reference=settings.MPESA_ACCOUNT_REFERENCE
        )
    except DarajaError as exc:
        txn.status = Status.FAILED
        txn.result_desc = str(exc)[:255]
        txn.save(update_fields=["status", "result_desc", "updated_at"])
        logger.warning("STK push failed for %s: %s", txn.id, exc)
        raise PaymentError("We couldn't send the M-Pesa prompt. Please try again.") from exc

    txn.merchant_request_id = ack.get("MerchantRequestID", "")
    txn.checkout_request_id = ack.get("CheckoutRequestID")
    txn.save(update_fields=["merchant_request_id", "checkout_request_id", "updated_at"])
    return txn


# --------------------------------------------------------------------------
# Callback from Safaricom
# --------------------------------------------------------------------------
def _metadata(callback: dict) -> dict:
    items = (callback.get("CallbackMetadata") or {}).get("Item") or []
    return {item.get("Name"): item.get("Value") for item in items if "Name" in item}


def _parse_mpesa_date(value) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%Y%m%d%H%M%S").replace(tzinfo=NAIROBI)
    except (TypeError, ValueError):
        return None


def process_callback(payload: dict) -> Transaction | None:
    """
    Apply Safaricom's result to the matching transaction.

    Idempotent: Safaricom may send the same callback more than once, and the
    row lock stops two callbacks (or a callback and a status query) racing.
    """
    try:
        callback = payload["Body"]["stkCallback"]
        checkout_id = callback["CheckoutRequestID"]
        result_code = int(callback["ResultCode"])
    except (KeyError, TypeError, ValueError):
        logger.warning("Malformed M-Pesa callback: %s", payload)
        return None

    with db_transaction.atomic():
        txn = Transaction.objects.select_for_update().filter(checkout_request_id=checkout_id).first()
        if txn is None:
            logger.warning("Callback for unknown CheckoutRequestID %s", checkout_id)
            return None
        if txn.status in Transaction.FINAL_STATUSES:
            # A status query may have confirmed success first; the query has no
            # receipt number, so let the later callback fill it in.
            if txn.status == Status.SUCCESS and not txn.mpesa_receipt and result_code == RESULT_SUCCESS:
                meta = _metadata(callback)
                txn.mpesa_receipt = str(meta.get("MpesaReceiptNumber", ""))[:20]
                txn.mpesa_transaction_date = _parse_mpesa_date(meta.get("TransactionDate"))
                txn.raw_callback = payload
                txn.save()
                logger.info("Receipt %s attached to %s", txn.mpesa_receipt, txn.id)
            else:
                logger.info("Duplicate callback for %s ignored (already %s)", txn.id, txn.status)
            return txn

        txn.raw_callback = payload
        txn.result_code = str(result_code)
        txn.result_desc = str(callback.get("ResultDesc", ""))[:255]
        txn.status = status_for_result(result_code)

        if txn.status == Status.SUCCESS:
            meta = _metadata(callback)
            paid = meta.get("Amount")
            if paid is None or int(float(paid)) != txn.amount:
                # Never mark something paid if the money doesn't match.
                logger.error("Amount mismatch on %s: expected %s, got %s", txn.id, txn.amount, paid)
                txn.status = Status.FAILED
                txn.result_desc = f"Amount mismatch: expected {txn.amount}, got {paid}"
            else:
                txn.mpesa_receipt = str(meta.get("MpesaReceiptNumber", ""))[:20]
                txn.mpesa_transaction_date = _parse_mpesa_date(meta.get("TransactionDate"))

        txn.save()
    logger.info("Transaction %s -> %s (%s)", txn.id, txn.status, txn.result_desc)
    return txn


# --------------------------------------------------------------------------
# Fallback when no callback arrives
# --------------------------------------------------------------------------
def refresh_status(txn: Transaction, *, force: bool = False) -> Transaction:
    """
    For a pending payment, ask Safaricom directly (STK Query) and, if it is
    still unanswered after PAY_PENDING_EXPIRY_SECONDS, mark it TIMEOUT.
    Throttled so a polling status page doesn't spam Safaricom.
    """
    if not txn.is_pending or not txn.checkout_request_id:
        return txn

    now = timezone.now()
    age = (now - txn.created_at).total_seconds()
    recently_queried = txn.last_queried_at and (now - txn.last_queried_at).total_seconds() < QUERY_MIN_INTERVAL_SECONDS
    if not force and (age < QUERY_AFTER_SECONDS or recently_queried):
        return txn

    try:
        data = get_client().stk_query(txn.checkout_request_id)
    except DarajaError as exc:
        logger.warning("STK query failed for %s: %s", txn.id, exc)
        data = {}

    with db_transaction.atomic():
        txn = Transaction.objects.select_for_update().get(pk=txn.pk)
        txn.last_queried_at = now
        if txn.is_pending and "ResultCode" in data:
            code = int(data["ResultCode"])
            txn.status = status_for_result(code)
            txn.result_code = str(code)
            txn.result_desc = str(data.get("ResultDesc", ""))[:255]
            # The query response has no receipt number; process_callback
            # fills it in if the callback arrives later.
        elif txn.is_pending and age >= settings.PAY_PENDING_EXPIRY_SECONDS:
            txn.status = Status.TIMEOUT
            txn.result_desc = "No response from M-Pesa in time."
        txn.save()
    return txn


# --------------------------------------------------------------------------
# Simulate mode helpers
# --------------------------------------------------------------------------
SIMULATED_OUTCOMES = {
    "success": (RESULT_SUCCESS, "The service request is processed successfully."),
    "cancelled": (RESULT_CANCELLED, "Request cancelled by user"),
    "insufficient": (RESULT_INSUFFICIENT_FUNDS, "The balance is insufficient for the transaction."),
    "timeout": (RESULT_UNREACHABLE, "DS timeout user cannot be reached"),
}


def build_callback_payload(txn: Transaction, outcome: str) -> dict:
    """A callback body shaped exactly like Safaricom's, for simulate mode and tests."""
    code, desc = SIMULATED_OUTCOMES[outcome]
    callback = {
        "MerchantRequestID": txn.merchant_request_id,
        "CheckoutRequestID": txn.checkout_request_id,
        "ResultCode": code,
        "ResultDesc": desc,
    }
    if code == RESULT_SUCCESS:
        callback["CallbackMetadata"] = {
            "Item": [
                {"Name": "Amount", "Value": float(txn.amount)},
                {"Name": "MpesaReceiptNumber", "Value": f"SIM{txn.id.hex[:7].upper()}"},
                {"Name": "TransactionDate", "Value": int(timezone.localtime().strftime("%Y%m%d%H%M%S"))},
                {"Name": "PhoneNumber", "Value": int(txn.phone)},
            ]
        }
    return {"Body": {"stkCallback": callback}}
