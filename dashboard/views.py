"""
Staff-only dashboard: today's numbers, a 7-day revenue chart and a live
transactions table. Record browsing also lives in the Django admin.
"""

import csv
from datetime import timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from payments import services
from payments.models import Transaction

Status = Transaction.Status
TABLE_LIMIT = 50


def _filtered_transactions(request):
    qs = Transaction.objects.all()
    status = request.GET.get("status", "")
    q = request.GET.get("q", "").strip()
    if status in Status.values:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(phone__icontains=q.lstrip("0+")) | Q(mpesa_receipt__icontains=q))
    return qs, status, q


def _stats():
    now = timezone.localtime()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = Transaction.objects.filter(created_at__gte=today_start)

    agg = today.aggregate(
        revenue=Sum("amount", filter=Q(status=Status.SUCCESS)),
        total=Count("id"),
        success=Count("id", filter=Q(status=Status.SUCCESS)),
        failed=Count("id", filter=~Q(status__in=[Status.SUCCESS, Status.PENDING])),
    )
    finished = agg["success"] + agg["failed"]
    pending = Transaction.objects.filter(status=Status.PENDING).count()

    # Last 7 days of successful revenue, one bar per day (zero-filled).
    week_start = today_start - timedelta(days=6)
    rows = (
        Transaction.objects.filter(status=Status.SUCCESS, created_at__gte=week_start)
        .annotate(day=TruncDate("created_at", tzinfo=timezone.get_current_timezone()))
        .values("day")
        .annotate(revenue=Sum("amount"), count=Count("id"))
    )
    by_day = {r["day"]: r for r in rows}
    days = []
    for i in range(7):
        day = (week_start + timedelta(days=i)).date()
        r = by_day.get(day, {})
        days.append({"day": day, "revenue": r.get("revenue") or 0, "count": r.get("count") or 0})
    peak = max(d["revenue"] for d in days) or 1
    for d in days:
        d["pct"] = round(d["revenue"] / peak * 100, 1)
    week_total = sum(d["revenue"] for d in days)

    return {
        "revenue_today": agg["revenue"] or 0,
        "payments_today": agg["total"],
        "success_today": agg["success"],
        "success_rate": round(agg["success"] / finished * 100) if finished else None,
        "pending": pending,
        "days": days,
        "week_total": week_total,
    }


def _context(request):
    qs, status, q = _filtered_transactions(request)
    return {
        **_stats(),
        "transactions": qs[:TABLE_LIMIT],
        "status_filter": status,
        "q": q,
        "statuses": Status.choices,
        "updated_at": timezone.localtime(),
    }


@staff_member_required
def index(request):
    return render(request, "dashboard/index.html", _context(request))


def _reconcile_stale_pending(limit=5):
    """
    Payments whose customer closed the page never get polled, so the open
    dashboard nudges a few of them (refresh_status is throttled per payment).
    For guaranteed coverage, also run `manage.py reconcile_pending` on a schedule.
    """
    now = timezone.now()
    stale = Transaction.objects.filter(
        status=Status.PENDING,
        created_at__lte=now - timedelta(seconds=services.QUERY_AFTER_SECONDS),
        created_at__gte=now - timedelta(hours=1),
    ).order_by("created_at")[:limit]
    for txn in stale:
        services.refresh_status(txn)


@staff_member_required
def live(request):
    """Re-rendered every few seconds by HTMX."""
    _reconcile_stale_pending()
    return render(request, "dashboard/_live.html", _context(request))


@staff_member_required
def export_csv(request):
    qs, _, _ = _filtered_transactions(request)
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="payments-{stamp}.csv"'
    writer = csv.writer(response)
    writer.writerow(["created_at", "reference", "phone", "amount", "status", "mpesa_receipt", "result"])
    for txn in qs.iterator():
        writer.writerow(
            [
                timezone.localtime(txn.created_at).isoformat(timespec="seconds"),
                txn.reference,
                txn.phone,
                txn.amount,
                txn.status,
                txn.mpesa_receipt,
                txn.result_desc,
            ]
        )
    return response


@staff_member_required
@require_POST
def recheck(request, pk):
    """Manually ask Safaricom about a stuck pending payment."""
    txn = get_object_or_404(Transaction, pk=pk)
    services.refresh_status(txn, force=True)
    if request.headers.get("HX-Request") == "true":
        return live(request)
    return redirect("dashboard:index")
