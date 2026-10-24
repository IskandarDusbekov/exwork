from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0006_batch"),
    ]

    operations = [
        migrations.AddField(
            model_name="job",
            name="column_names",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
