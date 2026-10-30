"""Starter packages so a fresh install has something to show. Edit them in the admin."""

from django.db import migrations

PACKAGES = [
    # (price, name, description, popular, order)
    (30, "Premium", "Everything included", True, 1),
    (20, "Standard", "Our most flexible plan", False, 2),
    (15, "Basic", "The essentials", False, 3),
    (10, "Starter", "Try it out", False, 4),
]


def seed(apps, schema_editor):
    Package = apps.get_model("payments", "Package")
    if Package.objects.exists():
        return
    for price, name, description, popular, order in PACKAGES:
        Package.objects.create(
            price=price, name=name, description=description, is_popular=popular, sort_order=order
        )


class Migration(migrations.Migration):
    dependencies = [("payments", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
