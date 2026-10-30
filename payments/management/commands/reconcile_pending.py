"""
Ask Safaricom about every payment still PENDING.

Safe to run as often as you like, e.g. from cron every minute:
    python manage.py reconcile_pending
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from payments import services
from payments.models import Transaction


class Command(BaseCommand):
    help = "Query M-Pesa for pending payments whose callback never arrived."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(seconds=services.QUERY_AFTER_SECONDS)
        pending = Transaction.objects.filter(status=Transaction.Status.PENDING, created_at__lte=cutoff)
        checked = changed = 0
        for txn in pending:
            checked += 1
            if services.refresh_status(txn, force=True).status != Transaction.Status.PENDING:
                changed += 1
        self.stdout.write(self.style.SUCCESS(f"Checked {checked} pending payment(s), {changed} updated."))
