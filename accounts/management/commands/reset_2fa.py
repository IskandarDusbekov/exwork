"""Телефон йўқолганда: ``python manage.py reset_2fa email@…`` — икки босқичли кириш ўчади, кейинги киришда
(панелга кирадиган бўлса) қайтадан ўрнатилади. Журналга ёзилади."""
from django.core.management.base import BaseCommand, CommandError

from accounts.models import TwoFactor, User, normalize_email
from panel.models import Event


class Command(BaseCommand):
    help = "Фойдаланувчининг икки босқичли киришини ўчириш (телефон йўқолганда)"

    def add_arguments(self, parser):
        parser.add_argument("email")

    def handle(self, email, **opts):
        user = User.objects.filter(email=normalize_email(email)).first()
        if user is None:
            raise CommandError(f"{email}: бундай фойдаланувчи йўқ")
        n, _ = TwoFactor.objects.filter(user=user).delete()
        if not n:
            self.stdout.write(f"{user.email}: икки босқичли кириш ёқилмаган эди")
            return
        Event.objects.create(kind=Event.Kind.TWOFA_RESET, target=user, detail=f"{user.email}: manage.py reset_2fa")
        self.stdout.write(self.style.SUCCESS(f"{user.email}: икки босқичли кириш ўчирилди"))
