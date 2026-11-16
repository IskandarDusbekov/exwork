import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("panel", "0011_alter_event_kind"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Track",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("step", models.CharField(db_index=True, max_length=32)),
                ("work_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("detail", models.CharField(blank=True, max_length=200)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                           related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created"],
                "indexes": [models.Index(fields=["step", "created"], name="panel_track_step_cr_idx"),
                            models.Index(fields=["user", "created"], name="panel_track_user_cr_idx")],
            },
        ),
    ]
