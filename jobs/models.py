import uuid
from pathlib import Path

from django.conf import settings
from django.db import models


class Job(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Юкланди"
        DONE = "done", "Тайёр"
        FAILED = "failed", "Хато"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UPLOADED)

    # Asl nomlar (ko'rsatish uchun) va ish papkasiga nisbatan yo'llar (.xlsx ga o'girilgan)
    local_name = models.CharField(max_length=255)
    res_name = models.CharField(max_length=255)
    local_path = models.CharField(max_length=500)
    res_path = models.CharField(max_length=500)
    local_sheets = models.JSONField(default=list)
    res_sheets = models.JSONField(default=list)

    local_sheet = models.CharField(max_length=255, blank=True)
    res_sheet = models.CharField(max_length=255, blank=True)
    fuzzy = models.PositiveSmallIntegerField(default=95)

    output_path = models.CharField(max_length=500, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"{self.local_name} ({self.get_status_display()})"

    @property
    def dir(self) -> Path:
        return Path(settings.MEDIA_ROOT) / "jobs" / str(self.id)

    def abs(self, rel: str) -> Path:
        return self.dir / rel

    @property
    def download_name(self) -> str:
        stem = Path(self.local_name).stem
        ext = ".xlsm" if self.local_name.lower().endswith(".xlsm") else ".xlsx"
        return f"{stem}_narxlandi{ext}"
