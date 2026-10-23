import re
import uuid
from pathlib import Path

from django.conf import settings
from django.db import models


class Batch(models.Model):
    """Bir yoki bir nechta yuklangan fayl (obyekt): listlar tahlili va juftliklar.
    Tasdiqlangandan keyin har bir juftlik — alohida ``Job``."""

    class Status(models.TextChoices):
        REVIEW = "review", "Текширишда"
        DONE = "done", "Нархланди"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REVIEW)
    name = models.CharField(max_length=255)
    files = models.JSONField(default=list)   # [{"name": asl nom, "path": "conv/0.xlsx", "sheets": [...]}]
    sheets = models.JSONField(default=list)  # core.batch.SheetInfo.to_dict() ro'yxati
    pairs = models.JSONField(default=list)   # core.batch.Pair.to_dict() + {"include": bool}
    roles = models.JSONField(default=dict, blank=True)  # qo'lda list turi {"0:LRV": "local"}

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return self.name

    @property
    def dir(self) -> Path:
        return Path(settings.MEDIA_ROOT) / "batches" / str(self.id)

    def abs(self, rel: str) -> Path:
        return self.dir / rel

    def sheet(self, key: str):
        return next((s for s in self.sheets if s["key"] == key), None)


class Job(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Юкланди"
        DONE = "done", "Тайёр"
        FAILED = "failed", "Хато"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UPLOADED)

    # Obyekt (bir nechta juftlik) ichidagi qism bo'lsa
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.CASCADE, related_name="jobs")
    batch_label = models.CharField(max_length=255, blank=True)
    batch_file = models.PositiveSmallIntegerField(null=True, blank=True)  # lokal list qaysi yuklangan faylda
    batch_order = models.PositiveSmallIntegerField(default=0)

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
    mode = models.CharField(max_length=10, default="smart")  # smart | compat
    percents = models.JSONField(default=dict, blank=True)    # svodka foizlari (ulush); bo'sh = standart
    categories = models.JSONField(default=dict, blank=True)  # qo'lda kategoriya {"qator": "I".."M"}
    prices = models.JSONField(default=dict, blank=True)      # qo'lda narx / tasdiqlangan taklif {"qator": narx}

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
        if self.batch_label:
            stem = re.sub(r'[\\/:*?"<>|]+', " ", self.batch_label).strip()[:80] or "smeta"
            return f"{stem}_narxlandi.xlsx"
        stem = Path(self.local_name).stem
        ext = ".xlsm" if self.local_name.lower().endswith(".xlsm") else ".xlsx"
        return f"{stem}_narxlandi{ext}"
