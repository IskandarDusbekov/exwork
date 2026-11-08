"""Ускуна ва кабель базасини ҳамма нархланган сметалардан қайтадан йиғиш: ``manage.py lm_harvest``."""
from django.core.management.base import BaseCommand

from resbase.lm import harvest_all, stats


class Command(BaseCommand):
    help = "Ускуна ва кабель базасини ҳамма нархланган сметалардан қайтадан йиғади"

    def handle(self, *args, **opts):
        def progress(n, total):
            if n % 50 == 0 or n == total:
                self.stdout.write(f"{n} / {total}")
        out = harvest_all(progress)
        self.stdout.write(self.style.SUCCESS(f"{out['jobs']} та смета, {out['votes']} та далил · {stats()}"))
