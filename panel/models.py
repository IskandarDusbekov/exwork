from django.conf import settings
from django.db import models


class Event(models.Model):
    """Ҳаракатлар журнали: ким, қачон, қаердан (IP), нима қилди. Ўзгармайди — фақат қўшилади."""

    class Kind(models.TextChoices):
        REGISTER = "register", "Рўйхатдан ўтди"
        LOGIN = "login", "Кирди"
        LOGOUT = "logout", "Чиқди"
        LOGIN_FAILED = "login_failed", "Хато кириш"
        UPLOAD = "upload", "Юклади"
        DELETE = "delete", "Ўчирди"
        STOP = "stop", "Тўхтатди"
        RENAME = "rename", "Номини ўзгартирди"
        PASSWORD_RESET = "password_reset", "Паролни тиклади"
        ADMIN_USER = "admin_user", "Админ: фойдаланувчи ўзгартирилди"
        ADMIN_STOP = "admin_stop", "Админ: жараён тўхтатилди"
        ADMIN_DOWNLOAD = "admin_download", "Админ: файл юклаб олинди"
        ADMIN_VIEW = "admin_view", "Админ: иш кўрилди"
        ADMIN_SESSION = "admin_session", "Админ: сессия тугатилди"
        ADMIN_IP = "admin_ip", "Админ: IP блоки"
        BLOCKED_IP = "blocked_ip", "Блокланган IP'дан сўров"

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    kind = models.CharField(max_length=24, choices=Kind.choices, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")
    target = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="+")  # админ ҳаракатида — кимнинг устидан
    email = models.CharField(max_length=254, blank=True)  # хато киришда киритилган email
    ip = models.GenericIPAddressField(null=True, blank=True)
    device = models.CharField(max_length=120, blank=True)  # «Chrome · Windows · компьютер»
    work_kind = models.CharField(max_length=10, blank=True)  # folder | object | smeta
    work_id = models.UUIDField(null=True, blank=True)
    detail = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created"]
        indexes = [models.Index(fields=["actor", "-created"]), models.Index(fields=["target", "-created"])]

    def __str__(self):
        return f"{self.created:%d.%m.%Y %H:%M} {self.get_kind_display()}"

    @property
    def is_admin(self):
        return self.kind.startswith("admin_")

    @property
    def is_alarm(self):
        return self.kind in (self.Kind.LOGIN_FAILED, self.Kind.BLOCKED_IP)


class LoginSession(models.Model):
    """Ҳар бир кириш (сессия): қайси IP ва қурилмадан, қачон охирги марта фаол бўлган.
    Админ уни тугатса — фойдаланувчи дарҳол тизимдан чиқади."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="login_sessions")
    session_key = models.CharField(max_length=40, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    browser = models.CharField(max_length=60, blank=True)
    os = models.CharField(max_length=60, blank=True)
    device = models.CharField(max_length=20, blank=True)  # computer | phone | tablet | bot
    created = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(db_index=True)
    ended = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=20, blank=True)  # logout | revoked | blocked

    class Meta:
        ordering = ["-last_seen"]
        indexes = [models.Index(fields=["user", "-last_seen"])]

    @property
    def device_label(self):
        return {"phone": "Телефон", "tablet": "Планшет", "bot": "Бот"}.get(self.device, "Компьютер")


class BlockedIP(models.Model):
    """Блокланган манзил: ундан ҳеч қандай саҳифа очилмайди (кириш саҳифаси ҳам)."""
    ip = models.GenericIPAddressField(unique=True)
    reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return self.ip
