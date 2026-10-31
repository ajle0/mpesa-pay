import csv

from django.contrib import admin, messages
from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import format_html

from . import services
from .models import Transaction

STATUS_COLOURS = {
    "SUCCESS": "#15803d",
    "PENDING": "#b45309",
    "FAILED": "#b91c1c",
    "CANCELLED": "#6b7280",
    "TIMEOUT": "#6b7280",
}


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "phone", "amount", "status_badge", "mpesa_receipt", "result_desc")
    list_filter = ("status", "created_at")
    search_fields = ("phone", "mpesa_receipt", "checkout_request_id", "merchant_request_id")
    date_hierarchy = "created_at"
    actions = ("recheck_status", "export_csv")
    readonly_fields = [f.name for f in Transaction._meta.fields]

    # Transactions are a financial record: no manual creating, editing or deleting.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return format_html(
            '<b style="color:{}">{}</b>', STATUS_COLOURS.get(obj.status, "#000"), obj.get_status_display()
        )

    @admin.action(description="Re-check selected pending payments with M-Pesa")
    def recheck_status(self, request, queryset):
        updated = 0
        for txn in queryset.filter(status=Transaction.Status.PENDING):
            before = txn.status
            if services.refresh_status(txn, force=True).status != before:
                updated += 1
        self.message_user(request, f"{updated} payment(s) updated.", messages.SUCCESS)

    @admin.action(description="Export selected to CSV")
    def export_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
        response["Content-Disposition"] = f'attachment; filename="transactions-{stamp}.csv"'
        writer = csv.writer(response)
        fields = ["created_at", "phone", "amount", "status", "mpesa_receipt", "result_desc"]
        writer.writerow(fields)
        for txn in queryset:
            writer.writerow([getattr(txn, f) for f in fields])
        return response
