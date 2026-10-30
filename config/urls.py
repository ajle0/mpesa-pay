from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path

admin.site.site_header = f"{settings.SITE_NAME} admin"
admin.site.site_title = f"{settings.SITE_NAME} admin"
admin.site.index_title = "Payments management"


def healthz(request):
    """Used by Docker and hosting platforms to check the app is up."""
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("dashboard/", include("dashboard.urls")),
    path("healthz", healthz, name="healthz"),
    path("", include("payments.urls")),
]
