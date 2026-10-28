"""SQLite'даги барча маълумотни PostgreSQL'га кўчириш (бир марта, ўтишда).

    set DATABASE_URL=postgres://narxlash:parol@127.0.0.1:5432/narxlash
    python manage.py migrate                 # PostgreSQL'да жадваллар
    python manage.py copy_to_postgres        # db.sqlite3 → PostgreSQL

Фойдаланувчилар (пароль хешлари билан), ишлар, журнал, сессиялар — ҳаммаси. ``media/`` папкаси жойида қолади.
Мақсад база бўш бўлиши керак (тасодифан устидан ёзилмасин); ``--force`` — барибир."""
from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

SKIP = {"contenttypes.contenttype", "auth.permission", "admin.logentry"}  # migrate ўзи яратади / керак эмас


class Command(BaseCommand):
    help = "db.sqlite3 даги маълумотни PostgreSQL'га (DATABASE_URL) кўчиради"

    def add_arguments(self, parser):
        parser.add_argument("--sqlite", default=str(settings.BASE_DIR / "db.sqlite3"))
        parser.add_argument("--force", action="store_true", help="мақсад базада маълумот бўлса ҳам")

    def handle(self, *args, sqlite, force, **opts):
        if connections["default"].vendor != "postgresql":
            raise CommandError("DATABASE_URL=postgres://… ўрнатилмаган — мақсад база PostgreSQL бўлиши керак")
        connections.databases["sqlite_src"] = {
            **connections["default"].settings_dict, "ENGINE": "django.db.backends.sqlite3", "NAME": sqlite,
            "OPTIONS": {}, "HOST": "", "PORT": "", "USER": "", "PASSWORD": "",
        }
        models = [m for m in apps.get_models() if m._meta.label_lower not in SKIP and not m._meta.proxy]
        user_model = apps.get_model(settings.AUTH_USER_MODEL)
        if user_model.objects.using("default").exists() and not force:
            raise CommandError("PostgreSQL'да аллақачон фойдаланувчилар бор. Устидан ёзиш учун: --force")

        # боғланишлар тартиби: аввал улар ишора қиладиган жадваллар
        order, seen = [], set()

        def visit(m):
            if m in seen:
                return
            seen.add(m)
            for f in m._meta.concrete_fields:
                if f.is_relation and f.related_model and f.related_model is not m and f.related_model in models:
                    visit(f.related_model)
            order.append(m)

        for m in models:
            visit(m)

        with transaction.atomic(using="default"):
            for m in order:
                rows = list(m._base_manager.using("sqlite_src").all())
                m._base_manager.using("default").bulk_create(rows, batch_size=500)
                for f in m._meta.many_to_many:  # гуруҳлар/ҳуқуқлар
                    through = f.remote_field.through
                    if through._meta.auto_created:
                        links = list(through._base_manager.using("sqlite_src").all())
                        through._base_manager.using("default").bulk_create(links, batch_size=500,
                                                                           ignore_conflicts=True)
                self.stdout.write(f"{m._meta.label}: {len(rows)}")
        # автоинкремент кетма-кетликлари (кейинги id тўқнашмасин)
        from django.core.management.color import no_style
        sql = connections["default"].ops.sequence_reset_sql(no_style(), order)
        with connections["default"].cursor() as cur:
            for stmt in sql:
                cur.execute(stmt)
        self.stdout.write(self.style.SUCCESS("Тайёр: барча маълумот PostgreSQL'да"))
