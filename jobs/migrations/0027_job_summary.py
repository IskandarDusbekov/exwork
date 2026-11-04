from django.db import migrations, models


def fill(apps, schema_editor):
    Job = apps.get_model("jobs", "Job")
    batch = []
    for j in Job.objects.only("id", "result").iterator(chunk_size=50):
        res = j.result or {}
        total = (res.get("totals") or {}).get("s_nds")
        j.sum_total = float(total) if isinstance(total, (int, float)) else None
        j.sum_not_priced = int((res.get("stats") or {}).get("NOT_PRICED") or 0)
        j.sum_checks = bool(res.get("checks"))
        batch.append(j)
        if len(batch) >= 200:
            Job.objects.bulk_update(batch, ["sum_total", "sum_not_priced", "sum_checks"])
            batch = []
    if batch:
        Job.objects.bulk_update(batch, ["sum_total", "sum_not_priced", "sum_checks"])


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0026_job_auto_cleared"),
    ]

    operations = [
        migrations.AddField(model_name="job", name="sum_total", field=models.FloatField(blank=True, null=True)),
        migrations.AddField(model_name="job", name="sum_not_priced", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="job", name="sum_checks", field=models.BooleanField(default=False)),
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
