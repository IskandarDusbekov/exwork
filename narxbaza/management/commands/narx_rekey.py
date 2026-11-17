from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Нарх базаси: ном калитларини қайта ҳисоблаш, бир хил бўлиб қолган ресурсларни бирлаштириш"

    def handle(self, *args, **opts):
        from narxbaza import services
        self.stdout.write(str(services.rekey_all()))
