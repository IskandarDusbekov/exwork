from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('narxbaza', '0011_rekey_names'),
    ]

    operations = [
        migrations.AddField(
            model_name='intake',
            name='worker',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='intake',
            name='heartbeat',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
