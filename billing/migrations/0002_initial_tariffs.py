# i18n: ok-file — бошланғич маълумот (панелда ўзгартирилади)
from django.db import migrations

TARIFFS = [
    ("Бепул", 0, 2000, 1, "Синаб кўриш учун: 3–5 та смета", 0),
    ("Старт", 100000, 30000, 5, "", 1),
    ("Про", 250000, 100000, 20, "", 2),
    ("Компания", 600000, 300000, 60, "", 3),
]


def add(apps, schema_editor):
    Tariff = apps.get_model("billing", "Tariff")
    if Tariff.objects.exists():
        return
    for name, price, rows, objs, note, order in TARIFFS:
        Tariff.objects.create(name=name, price=price, rows=rows, objects_limit=objs, note=note, order=order)


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial")]
    operations = [migrations.RunPython(add, migrations.RunPython.noop)]
