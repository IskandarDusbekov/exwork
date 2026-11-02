"""``manage.py tn_check <papka> [--ignore Обмер,Ф-2]`` — papkadagi hamma ofyortani tizimdagidek o'qib tekshiradi:
hajmsiz dalolatnoma ≈ 0, yarim hajm ≈ 50%, to'liq hajm ≈ fayldagi jami. Yiqilgan bo'lsa — chiqish kodi 1."""
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from texnazor.corpus import check_file, files


def _m(x):
    return "—" if x is None else f"{x:,.0f}".replace(",", " ")


class Command(BaseCommand):
    help = "Офёрталар тўпламини текшириш (бўш ≈ 0, ярим ≈ 50%, тўлиқ ≈ файлдаги жами)"

    def add_arguments(self, parser):
        parser.add_argument("folder")
        parser.add_argument("--ignore", default="", help="номида шу сўзлар бўлган файллар ташланади (вергул билан)")

    def handle(self, folder, ignore, **opts):
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")  # Windows konsolida kirill / o'zbek harflari
        paths = files(Path(folder), ignore.split(","))
        if not paths:
            raise CommandError(f"{folder}: Excel файл йўқ")
        bad = 0
        for p in paths:
            r = check_file(p)
            mark = "OK  " if r["ok"] else "FAIL"
            bad += not r["ok"]
            if r["error"]:
                self.stdout.write(f"{mark} {r['file'][:60]:60}  {r['error'][:120]}")
                continue
            half = f"{100 * r['half'] / r['full']:.1f}%" if r.get("full") else "—"
            self.stdout.write(f"{mark} {r['file'][:60]:60}  жами {_m(r['full']):>18}  бўш {_m(r['empty']):>14}  "
                              f"ярим {half:>6}  файлда {_m(r['file_total']):>18}")
            for w in r["fails"]:
                self.stdout.write(f"       · {w['title']}")
        n = len(paths)
        self.stdout.write(f"\n{n - bad} / {n} ўтди ({100 * (n - bad) / n:.0f}%)")
        if bad:
            sys.exit(1)
