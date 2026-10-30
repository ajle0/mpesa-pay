from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.index, name="index"),
    path("live/", views.live, name="live"),
    path("export.csv", views.export_csv, name="export_csv"),
    path("payments/<uuid:pk>/recheck/", views.recheck, name="recheck"),
]
