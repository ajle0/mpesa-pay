import json
import logging

from django import forms
from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import services
from .models import Transaction

logger = logging.getLogger(__name__)


class PayForm(forms.Form):
    phone = forms.CharField(max_length=20)
    # Free text on purpose: services.parse_amount understands "1,500" and gives
    # friendlier errors than an IntegerField would.
    amount = forms.CharField(max_length=20)


def _client_ip(request) -> str | None:
    if settings.USE_X_FORWARDED_FOR:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _render_status(request, txn: Transaction):
    return render(request, "payments/_status.html", {"txn": txn})


# --------------------------------------------------------------------------
# Customer pages
# --------------------------------------------------------------------------
def _index_context(**extra):
    return {"min_amount": settings.PAY_MIN_AMOUNT, "max_amount": settings.PAY_MAX_AMOUNT, **extra}


@require_GET
def index(request):
    return render(request, "payments/index.html", _index_context())


@require_POST
def pay(request):
    form = PayForm(request.POST)
    error = None
    txn = None

    if not form.is_valid():
        error = "Enter your M-Pesa number and an amount."
    else:
        try:
            txn = services.initiate_payment(
                raw_amount=form.cleaned_data["amount"],
                raw_phone=form.cleaned_data["phone"],
                client_ip=_client_ip(request),
            )
        except services.PaymentError as exc:
            error = str(exc)

    if _is_htmx(request):
        if error:
            return render(request, "payments/_form_error.html", {"error": error})
        response = _render_status(request, txn)
        # Swap the whole payment card instead of just the error slot.
        response["HX-Retarget"] = "#payment-card"
        response["HX-Reswap"] = "innerHTML"
        # Put the payment's own URL in the address bar so a refresh keeps the status.
        response["HX-Push-Url"] = reverse("payments:payment_detail", args=[txn.pk])
        return response

    if error:
        context = _index_context(
            error=error, phone=request.POST.get("phone", ""), amount=request.POST.get("amount", "")
        )
        return render(request, "payments/index.html", context, status=400)
    return redirect("payments:payment_detail", pk=txn.pk)


@require_GET
def payment_detail(request, pk):
    """Full page for a payment, so a refresh or a shared link still works."""
    txn = get_object_or_404(Transaction, pk=pk)
    txn = services.refresh_status(txn)
    return render(request, "payments/detail.html", {"txn": txn})


@require_GET
def payment_status(request, pk):
    """Polled by HTMX every few seconds while a payment is pending."""
    txn = get_object_or_404(Transaction, pk=pk)
    txn = services.refresh_status(txn)
    return _render_status(request, txn)


@require_POST
def simulate(request, pk, outcome):
    """Simulate mode only: pretend Safaricom sent a callback."""
    if settings.MPESA_MODE != "simulate" or outcome not in services.SIMULATED_OUTCOMES:
        raise Http404
    txn = get_object_or_404(Transaction, pk=pk)
    services.process_callback(services.build_callback_payload(txn, outcome))
    txn.refresh_from_db()
    if _is_htmx(request):
        return _render_status(request, txn)
    return redirect("payments:payment_detail", pk=txn.pk)


# --------------------------------------------------------------------------
# Safaricom callback
# --------------------------------------------------------------------------
@csrf_exempt
@require_POST
def mpesa_callback(request, secret):
    # Unknown secret -> behave as if the URL doesn't exist.
    if not constant_time_compare(secret, settings.MPESA_CALLBACK_SECRET):
        raise Http404

    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        logger.warning("Callback with invalid JSON body")
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Invalid JSON"}, status=400)

    logger.info("M-Pesa callback received: %s", payload)
    services.process_callback(payload)
    # Always acknowledge, otherwise Safaricom keeps retrying.
    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})
