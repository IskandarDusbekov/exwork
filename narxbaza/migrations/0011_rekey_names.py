"""Ном калити қоидаси ўзгарди (нуқта, вергул, бўшлиқ, лотин/кирилл ҳарфлари фарқ қилмайди): ҳамма калитлар
қайта ҳисобланади, энди бир хил бўлиб қолган ресурслар бирлаштирилади (``services.rekey_all``)."""
from django.db import migrations


def forwards(apps, schema_editor):
    from narxbaza import services
    try:
        services.rekey_all()
    except Exception:  # noqa: BLE001 — кейинги версияда схема бошқача бўлса: «manage.py narx_rekey» билан қўлда
        import logging
        logging.getLogger(__name__).exception("narxbaza: калитларни янгилаб бўлмади — manage.py narx_rekey")


class Migration(migrations.Migration):
    dependencies = [("narxbaza", "0010_price_pin")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
