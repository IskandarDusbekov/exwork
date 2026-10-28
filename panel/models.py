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
        ADMIN_USER = "admin_user", "Админ: фойдаланувчи ўзгартирилди"
        ADMIN_STOP = "admin_stop", "Админ: жараён тўхтатилди"
        ADMIN_DOWNLOAD = "admin_download", "Админ: файл юклаб олинди"

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    kind = models.CharField(max_length=24, choices=Kind.choices, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")
    target = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="+")  # админ ҳаракатида — кимнинг устидан
    email = models.CharField(max_length=254, blank=True)  # хато киришда киритилган email
    ip = models.GenericIPAddressField(null=True, blank=True)
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
        return self.kind == self.Kind.LOGIN_FAILED
