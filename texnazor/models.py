"""Texnazor: obyektlar, ularning ofyortasi (bazada — Excel har safar ochilmaydi), oylar va bajarilgan hajmlar.

Obyekt -> Bo'lim -> Qator (ish / mustaqil resurs / qo'shimcha; resurslar ishga bog'langan)
Obyekt -> Oy -> Bajarilish (qator, shu oyda bajarilgan hajm)

Pul: qatorning ``cost_*`` maydonlari — 1 birlik hajmning narxi turlar bo'yicha (J..O, X). Oyning summasi =
Σ hajm × cost — bitta SQL bilan; keyin svodka zanjiri (``calc.chain``) ofyortaning foizlari bilan.
"""
import uuid
from pathlib import Path

from django.conf import settings
from django.db import models

COST_FIELDS = {"J": "cost_j", "K": "cost_k", "L": "cost_l", "M": "cost_m", "N": "cost_n", "O": "cost_o", "X": "cost_x"}


class SiteObject(models.Model):
    class Status(models.TextChoices):
        PARSING = "parsing", "Офёрта ўқиляпти"
        READY = "ready", "Тайёр"
        FAILED = "failed", "Хато"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=300)
    address = models.CharField(max_length=300, blank=True)
    customer = models.CharField(max_length=300, blank=True)    # буюртмачи
    contractor = models.CharField(max_length=300, blank=True)  # пудратчи
    contract = models.CharField(max_length=200, blank=True)    # шартнома рақами ва санаси
    object_type = models.CharField(max_length=200, blank=True)

    source_name = models.CharField(max_length=255)  # юкланган файл номи
    source_path = models.CharField(max_length=500)  # ish papkasiga nisbatan (.xlsx ga o'girilgan)
    sheet = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PARSING)
    error = models.TextField(blank=True)
    progress = models.JSONField(default=dict, blank=True)  # o'qish jarayoni: {"step", "done", "total"}
    rates = models.JSONField(default=dict, blank=True)     # svodka foizlari (ofyortadan)
    file_totals = models.JSONField(default=dict, blank=True)  # ofyortadagi jami summalar (taqqoslash)
    warnings = models.JSONField(default=list, blank=True)  # ofyortadagi xatolar
    contract_totals = models.JSONField(default=dict, blank=True)  # biz hisoblagan shartnoma zanjiri (calc.chain)
    worker = models.CharField(max_length=100, blank=True)  # o'qishni egallagan worker (lease)
    heartbeat = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return self.name

    @property
    def dir(self) -> Path:
        return Path(settings.MEDIA_ROOT) / "texnazor" / str(self.id)

    def abs(self, rel: str) -> Path:
        return self.dir / rel


class Section(models.Model):
    obj = models.ForeignKey(SiteObject, on_delete=models.CASCADE, related_name="sections")
    order = models.PositiveIntegerField()
    title = models.CharField(max_length=500)
    items_count = models.PositiveIntegerField(default=0)  # ichidagi ish/mustaqil resurslar (bo'sh — guruh sarlavhasi)

    class Meta:
        ordering = ["order"]


class Item(models.Model):
    class Kind(models.TextChoices):
        WORK = "work", "Иш"
        FREE = "free", "Мустақил ресурс"
        EXTRA = "extra", "Қўшимча"
        RES = "res", "Ресурс"

    obj = models.ForeignKey(SiteObject, on_delete=models.CASCADE, related_name="items")
    section = models.ForeignKey(Section, null=True, blank=True, on_delete=models.CASCADE, related_name="items")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="resources")
    order = models.PositiveIntegerField()
    excel_row = models.PositiveIntegerField(default=0)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    num = models.CharField(max_length=20, blank=True)
    code = models.CharField(max_length=80, blank=True)
    name = models.TextField()
    search = models.TextField(blank=True)  # «№ ШИФР НОМИ» katta harfda — qidiruv (SQLite kirillni katta/kichik farqlaydi)
    unit = models.CharField(max_length=80, blank=True)
    qty = models.FloatField(default=0)        # ish/mustaqil: shartnoma hajmi; resurs: me'yor (1 birlik ishga)
    total_qty = models.FloatField(default=0)  # resurs: jami (F)
    price = models.FloatField(default=0)      # G (yoki H — buyurtmachi uskunasi)
    amount = models.FloatField(default=0)     # shartnoma bo'yicha to'g'ridan-to'g'ri summa
    category = models.CharField(max_length=1, blank=True)  # J..O (resurs/mustaqil)
    cost_j = models.FloatField(default=0)
    cost_k = models.FloatField(default=0)
    cost_l = models.FloatField(default=0)
    cost_m = models.FloatField(default=0)
    cost_n = models.FloatField(default=0)
    cost_o = models.FloatField(default=0)
    cost_x = models.FloatField(default=0)

    class Meta:
        ordering = ["order"]
        indexes = [models.Index(fields=["obj", "kind"]), models.Index(fields=["section", "order"])]

    @property
    def unit_cost(self) -> float:
        return sum(getattr(self, f) for f in COST_FIELDS.values())


class Period(models.Model):
    """Hisobot oyi. Yopilgan oy o'zgarmaydi (u bo'yicha to'lov qilingan)."""
    obj = models.ForeignKey(SiteObject, on_delete=models.CASCADE, related_name="periods")
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    closed = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["year", "month"]
        constraints = [models.UniqueConstraint(fields=["obj", "year", "month"], name="tn_period_unique")]

    MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
              "Ноябрь", "Декабрь"]

    @property
    def title(self) -> str:
        return f"{self.MONTHS[self.month - 1]} {self.year}"

    @property
    def key(self) -> int:
        return self.year * 100 + self.month


class Done(models.Model):
    period = models.ForeignKey(Period, on_delete=models.CASCADE, related_name="done")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="done")
    qty = models.FloatField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["period", "item"], name="tn_done_unique")]
