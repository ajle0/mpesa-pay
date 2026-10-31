import uuid

from django.db import models


class Transaction(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"
        TIMEOUT = "TIMEOUT", "Timed out"

    # Statuses Safaricom has given a definitive answer for. TIMEOUT is not here:
    # it is our own guess, so a late callback is still allowed to overwrite it.
    FINAL_STATUSES = {Status.SUCCESS, Status.FAILED, Status.CANCELLED}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone = models.CharField(max_length=12, db_index=True, help_text="Format 2547XXXXXXXX / 2541XXXXXXXX")
    amount = models.PositiveIntegerField(help_text="Whole Kenyan shillings, as entered by the customer.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)

    merchant_request_id = models.CharField(max_length=64, blank=True)
    checkout_request_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    result_code = models.CharField(max_length=20, blank=True)
    result_desc = models.CharField(max_length=255, blank=True)
    mpesa_receipt = models.CharField(max_length=20, blank=True, db_index=True)
    mpesa_transaction_date = models.DateTimeField(null=True, blank=True)

    client_ip = models.GenericIPAddressField(null=True, blank=True)
    raw_callback = models.JSONField(null=True, blank=True)
    last_queried_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.phone} KES {self.amount} [{self.status}]"

    @property
    def is_pending(self):
        return self.status == self.Status.PENDING

    @property
    def masked_phone(self):
        """2547XXXXX123 -> 0712•••123, for showing on public pages."""
        local = "0" + self.phone[3:] if self.phone.startswith("254") else self.phone
        return f"{local[:4]}•••{local[-3:]}"

    @property
    def reference(self):
        return self.id.hex[:8].upper()

    # Plain-language explanations for the customer, keyed by Daraja ResultCode.
    FRIENDLY_RESULTS = {
        "1": "Your M-Pesa balance was too low for this payment.",
        "1032": "You cancelled the M-Pesa prompt.",
        "1037": "We couldn't reach your phone, or the prompt expired before you entered your PIN.",
        "2001": "The M-Pesa PIN entered was incorrect.",
    }

    @property
    def customer_message(self):
        if self.result_code in self.FRIENDLY_RESULTS:
            return self.FRIENDLY_RESULTS[self.result_code]
        if self.status == self.Status.TIMEOUT:
            return "The prompt expired before a PIN was entered."
        return "M-Pesa could not complete the payment. No money was taken."
