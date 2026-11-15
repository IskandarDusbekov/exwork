"""Ҳамма ресурс × вилоят × йил учун паст / ўрта / юқори ва «четда» белгисини қайта ҳисоблайди
(ҳисоблаш қоидаси ўзгарганда — масалан, деплойдан кейин бир марта)."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Нарх базаси: паст / ўрта / юқори нархларни қайта ҳисоблаш"

    def handle(self, *args, **opts):
        from django.core.cache import cache

        from narxbaza import services
        from narxbaza.models import Stat

        services.recalc_all()
        cache.clear()
        self.stdout.write(self.style.SUCCESS(f"Тайёр: {Stat.objects.count()} та нарх гуруҳи қайта ҳисобланди."))
