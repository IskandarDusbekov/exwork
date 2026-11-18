"""Захира нусха: муаммоларни текшириш ва хабар бериш, тиклаш синови (cron ва deploy/backup.sh чақиради).

    manage.sh backup_check                  # муаммолар рўйхати; бўлса — суперфойдаланувчиларга хат (кунига бир марта)
    manage.sh backup_check --restore-test   # охирги нусхани алоҳида базага тиклаб текшириш (cron: ҳар якшанба)
    manage.sh backup_check --failed "сабаб" # backup.sh хатоси: панелда кўринади ва дарҳол хат кетади
"""
from django.core.management.base import BaseCommand, CommandError

from panel import backup


class Command(BaseCommand):
    help = "Захира нусха: текшириш, огоҳлантириш ва тиклаш синови"

    def add_arguments(self, parser):
        parser.add_argument("--restore-test", action="store_true", help="охирги нусхани синов базасига тиклаш")
        parser.add_argument("--file", help="синаладиган нусха номи (берилмаса — охиргиси)")
        parser.add_argument("--failed", metavar="САБАБ", help="backup.sh хато билан тугади")
        parser.add_argument("--no-mail", action="store_true", help="хат юборилмасин (фақат экранга)")

    def handle(self, *args, restore_test, file, failed, no_mail, **opts):
        if failed:
            backup.record_failure(failed)
        res = None
        if restore_test or file:
            res = backup.restore_test(file)
            if res["ok"]:
                self.stdout.write(f"Тиклаш синови ўтди: {res['file']} — {res['tables']} жадвал, {res['rows']} қатор, "
                                  f"{res['users']} фойдаланувчи ({res['seconds']} с)")
            else:
                self.stderr.write(f"Тиклаш синови ЎТМАДИ: {res['file']} — {res['error']}")
        probs = backup.problems()
        for _code, text in probs:
            self.stdout.write(f"! {text}")
        if not probs:
            self.stdout.write("Захира нусхада муаммо йўқ")
        if not no_mail and backup.alert(force=bool(failed)):
            self.stdout.write("Суперфойдаланувчиларга хат юборилди")
        if res is not None and not res["ok"]:
            raise CommandError("тиклаш синови ўтмади")
