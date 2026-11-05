"""Давр энди вилоят ва йил (чорак ишлатилмайди): bp↔bv жуфтлик калити чораксиз қайта ҳисобланади,
акс ҳолда эски файллар янги юкланганлари билан жуфтланмай қоларди."""
import re
from pathlib import Path

from django.db import migrations

_PAIR = re.compile(r"^b[pv](\d+)$", re.I)


def forward(apps, schema_editor):
    Source = apps.get_model("resbase", "Source")
    for s in Source.objects.filter(kind="file").only("id", "title", "region", "year", "pair_key"):
        m = _PAIR.match(Path(str(s.title)).stem.strip())
        key = f"{s.region}:{s.year}:{m.group(1)}" if m and s.region and s.year else ""
        if key != s.pair_key:
            Source.objects.filter(pk=s.pk).update(pair_key=key)


class Migration(migrations.Migration):
    dependencies = [("resbase", "0007_book_names")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
