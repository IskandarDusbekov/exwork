"""«Ёрдам хизмати»: ҳар бир фойдаланувчининг администратор билан битта суҳбати ва унинг хабарлари."""
import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils import timezone


class Conversation(models.Model):
    """Битта фойдаланувчи — битта суҳбат (тарих доим сақланади). «Ҳал қилинди» — ёпилган; фойдаланувчи яна
    ёзса, ўзи қайта очилади."""

    class Status(models.TextChoices):
        OPEN = "open", "Очиқ"
        CLOSED = "closed", "Ҳал қилинди"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_chat")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(default=timezone.now, db_index=True)  # охирги хабар вақти
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")

    class Meta:
        ordering = ["-updated"]
        verbose_name = "Ёрдам суҳбати"
        verbose_name_plural = "Ёрдам суҳбатлари"

    def __str__(self):
        return f"{self.user} ({self.get_status_display()})"


def _attach_path(instance, filename):
    return f"support/{instance.conversation_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    from_staff = models.BooleanField(default=False)  # администратор ёзган (муаллиф кейин ўчирилса ҳам аниқ)
    text = models.TextField(blank=True)
    attachment = models.FileField(upload_to=_attach_path, blank=True, max_length=200)
    attachment_name = models.CharField(max_length=150, blank=True)  # фойдаланувчи юклаган асл ном
    attachment_size = models.PositiveIntegerField(default=0)
    is_image = models.BooleanField(default=False)  # текширилган расм (PNG/JPEG/GIF/WEBP) — суҳбатда кўринади
    created = models.DateTimeField(default=timezone.now, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)  # иккинчи томон ўқиган вақт

    class Meta:
        ordering = ["created", "pk"]
        indexes = [models.Index(fields=["conversation", "from_staff", "read_at"])]

    def __str__(self):
        return f"{self.created:%d.%m.%Y %H:%M} {'admin' if self.from_staff else 'user'}: {self.text[:40]}"
