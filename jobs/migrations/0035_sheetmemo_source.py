from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0034_sheet_memo"),
    ]

    operations = [
        migrations.AddField(
            model_name="sheetmemo",
            name="batch_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sheetmemo",
            name="sheet_key",
            field=models.CharField(blank=True, max_length=300),
        ),
    ]
