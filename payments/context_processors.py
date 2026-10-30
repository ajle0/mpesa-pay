from django.conf import settings

SIMULATE_CHOICES = [
    ("success", "Pays"),
    ("cancelled", "Cancels"),
    ("insufficient", "Has low balance"),
    ("timeout", "Ignores the prompt"),
]


def site(request):
    return {
        "SITE_NAME": settings.SITE_NAME,
        "MPESA_MODE": settings.MPESA_MODE,
        "simulate_choices": SIMULATE_CHOICES if settings.MPESA_MODE == "simulate" else [],
    }
