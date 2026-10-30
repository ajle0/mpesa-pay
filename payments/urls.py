from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.index, name="index"),
    path("pay/", views.pay, name="pay"),
    path("pay/<uuid:pk>/", views.payment_detail, name="payment_detail"),
    path("pay/<uuid:pk>/status/", views.payment_status, name="payment_status"),
    path("pay/<uuid:pk>/simulate/<str:outcome>/", views.simulate, name="simulate"),
    path("callbacks/mpesa/<str:secret>/", views.mpesa_callback, name="mpesa_callback"),
]
