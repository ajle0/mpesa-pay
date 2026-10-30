"""HTTP layer: customer pages, the Safaricom callback, simulate mode and the dashboard."""

import json

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from payments import services
from payments.models import Package, Transaction

from .test_services import callback

Status = Transaction.Status
SECRET = "test-callback-secret-0123456789"


@override_settings(MPESA_MODE="simulate", MPESA_CALLBACK_SECRET=SECRET)
class CustomerPageTests(TestCase):
    def setUp(self):
        Package.objects.all().delete()
        self.package = Package.objects.create(name="Daily", price=30, is_popular=True)

    def test_index_lists_packages(self):
        resp = self.client.get(reverse("payments:index"))
        self.assertContains(resp, "Ksh 30")
        self.assertContains(resp, "Daily")

    def test_htmx_pay_returns_status_and_pushes_url(self):
        resp = self.client.post(
            reverse("payments:pay"), {"phone": "0712345678", "package": self.package.pk}, HTTP_HX_REQUEST="true"
        )
        txn = Transaction.objects.get()
        self.assertContains(resp, "Check your phone")
        self.assertEqual(resp["HX-Push-Url"], reverse("payments:payment_detail", args=[txn.pk]))

    def test_plain_form_post_redirects(self):
        resp = self.client.post(reverse("payments:pay"), {"phone": "0712345678", "package": self.package.pk})
        self.assertRedirects(resp, reverse("payments:payment_detail", args=[Transaction.objects.get().pk]))

    def test_invalid_phone_shows_error(self):
        resp = self.client.post(
            reverse("payments:pay"), {"phone": "12", "package": self.package.pk}, HTTP_HX_REQUEST="true"
        )
        self.assertContains(resp, "valid Safaricom number")
        self.assertFalse(Transaction.objects.exists())

    def test_amount_cannot_be_set_by_browser(self):
        self.client.post(reverse("payments:pay"), {"phone": "0712345678", "package": self.package.pk, "amount": 1})
        self.assertEqual(Transaction.objects.get().amount, 30)

    def test_simulate_success(self):
        txn = services.initiate_payment(package=self.package, raw_phone="0712345678")
        resp = self.client.post(reverse("payments:simulate", args=[txn.pk, "success"]), HTTP_HX_REQUEST="true")
        self.assertContains(resp, "Payment received")

    @override_settings(MPESA_MODE="sandbox")
    def test_simulate_is_disabled_outside_simulate_mode(self):
        txn = Transaction.objects.create(package=self.package, phone="254712345678", amount=30, checkout_request_id="x")
        resp = self.client.post(reverse("payments:simulate", args=[txn.pk, "success"]))
        self.assertEqual(resp.status_code, 404)
        txn.refresh_from_db()
        self.assertEqual(txn.status, Status.PENDING)

    def test_status_partial_stops_polling_when_done(self):
        txn = services.initiate_payment(package=self.package, raw_phone="0712345678")
        url = reverse("payments:payment_status", args=[txn.pk])
        self.assertContains(self.client.get(url), 'hx-trigger="every 3s"')
        services.process_callback(callback(txn))
        self.assertNotContains(self.client.get(url), 'hx-trigger="every 3s"')


@override_settings(MPESA_MODE="simulate", MPESA_CALLBACK_SECRET=SECRET)
class CallbackEndpointTests(TestCase):
    def setUp(self):
        self.txn = services.initiate_payment(package=Package.objects.create(name="X", price=30), raw_phone="0712345678")

    def post(self, secret, body):
        return self.client.post(
            reverse("payments:mpesa_callback", args=[secret]), data=json.dumps(body), content_type="application/json"
        )

    def test_valid_callback_marks_paid_and_acknowledges(self):
        resp = self.post(SECRET, callback(self.txn))
        self.assertEqual(resp.json(), {"ResultCode": 0, "ResultDesc": "Accepted"})
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, Status.SUCCESS)

    def test_wrong_secret_is_404_and_changes_nothing(self):
        resp = self.post("guessed", callback(self.txn))
        self.assertEqual(resp.status_code, 404)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, Status.PENDING)

    def test_invalid_json(self):
        resp = self.client.post(
            reverse("payments:mpesa_callback", args=[SECRET]), data="not json", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)


@override_settings(MPESA_MODE="simulate")
class DashboardTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("staff", password="x", is_staff=True)
        package = Package.objects.create(name="X", price=30)
        txn = services.initiate_payment(package=package, raw_phone="0712345678")
        services.process_callback(callback(txn, receipt="TKRECEIPT1"))
        services.initiate_payment(package=package, raw_phone="0722000000")

    def test_requires_staff_login(self):
        for name in ["dashboard:index", "dashboard:live", "dashboard:export_csv"]:
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 302, name)
            self.assertIn(reverse("admin:login"), resp["Location"])

    def test_non_staff_user_is_refused(self):
        self.client.force_login(User.objects.create_user("customer", password="x"))
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 302)

    def test_dashboard_shows_stats_and_transactions(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("dashboard:index"))
        self.assertContains(resp, "Revenue today")
        self.assertContains(resp, "TKRECEIPT1")
        self.assertEqual(resp.context["revenue_today"], 30)
        self.assertEqual(resp.context["pending"], 1)
        self.assertEqual(resp.context["success_rate"], 100)

    def test_filters(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("dashboard:live"), {"status": "PENDING"})
        self.assertEqual([t.phone for t in resp.context["transactions"]], ["254722000000"])
        resp = self.client.get(reverse("dashboard:live"), {"q": "TKRECEIPT"})
        self.assertEqual(len(resp.context["transactions"]), 1)

    def test_csv_export(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("dashboard:export_csv"))
        self.assertEqual(resp["Content-Type"], "text/csv")
        body = resp.content.decode()
        self.assertIn("mpesa_receipt", body.splitlines()[0])
        self.assertIn("TKRECEIPT1", body)

    def test_healthz(self):
        self.assertEqual(self.client.get("/healthz").content, b"ok")
