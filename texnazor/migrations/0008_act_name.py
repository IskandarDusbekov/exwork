from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('texnazor', '0007_acts'),
    ]

    operations = [
        migrations.AddField(
            model_name='act',
            name='name',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
    ]
