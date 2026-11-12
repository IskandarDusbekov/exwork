"""Янги нарх базаси: миллий кодсиз — ресурс номи + бирлиги бўйича.

* ``Item`` — ресурс картаси (номи, бирлиги, тоифаси).
* ``Price`` — битта нарх кузатуви: қаердан келди (смета файли ёки қўлда), ким киритди, вилоят, аниқ сана, сони.
  Ҳар ресурснинг «паспорти» — шу ёзувлар тарихи. Ҳеч нарса ўчирилмайди — истисно қилинади.
* ``Batch`` — бир марта тасдиқлаб киритилган тўплам (бир смета/папкадан ёки қўлда).
* ``Stat`` — ресурс × вилоят × йил: паст / ўрта / юқори нарх (``services.recalc``)."""
from django.conf import settings
from django.db import models

from resbase.regions import REGIONS

CATEGORIES = [("I", "Иш ҳақи"), ("J", "Машина ва механизмлар"), ("K", "Материал"), ("M", "Ускуна"),
              ("L", "Кабель"), ("O", "Бошқа")]
CATEGORY_LABELS = dict(CATEGORIES)


class Item(models.Model):
    key = models.CharField(max_length=560, unique=True)  # services.item_key(name, unit)
    name = models.CharField(max_length=500)
    unit = models.CharField(max_length=40)
    category = models.CharField(max_length=1, choices=CATEGORIES, default="O")
    edited = models.BooleanField(default=False)  # админ тоифа/номни қўлда тузатган — йиғиш ўзгартирмайди
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.unit})"


class Batch(models.Model):
    """Бир марта киритилган нархлар тўплами — паспортдаги «қаердан» шу ерга ишора қилади."""

    class Kind(models.TextChoices):
        FOLDER = "folder", "Папка"
        OBJECT = "object", "Объект"
        SMETA = "smeta", "Смета"
        FILE = "file", "Юкланган файл"
        MANUAL = "manual", "Қўлда киритилган"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    work_id = models.UUIDField(null=True, blank=True, db_index=True)
    title = models.CharField(max_length=500)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")  # смета эгаси (смета­чи)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="+")  # тасдиқлаган админ
    region = models.CharField(max_length=20, choices=REGIONS)
    date = models.DateField()
    note = models.CharField(max_length=300, blank=True)
    file_path = models.CharField(max_length=300, blank=True)  # юкланган файл нусхаси (MEDIA_ROOT ичида)
    stats = models.JSONField(default=dict, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return self.title


class Price(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="prices")
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="prices")
    price = models.DecimalField(max_digits=20, decimal_places=4)  # бирлик нархи
    qty = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)  # фақат маълумот учун
    region = models.CharField(max_length=20, choices=REGIONS, db_index=True)
    date = models.DateField()
    year = models.PositiveSmallIntegerField(db_index=True)  # нархлашда шу ишлатилади
    name = models.CharField(max_length=500)  # файлда қандай ёзилган
    unit = models.CharField(max_length=40)
    # қаердан: [{"job": uuid, "file": "смета номи", "row": 12}, …] — бир тўпламда шу нарх неча файлда учраган
    places = models.JSONField(default=list, blank=True)
    seen = models.PositiveIntegerField(default=1)  # шу тўпламда шу нарх неча қаторда учраган (2 та нархда — ўрта шу бўйича)
    excluded = models.BooleanField(default=False)  # админ истисно қилган — ҳисобга кирмайди
    outlier = models.BooleanField(default=False)  # бошқалардан 3 баробар фарқ — ҳисобга кирмайди (recalc)
    note = models.CharField(max_length=300, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["item", "region", "year"])]


class Stat(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="stats")
    region = models.CharField(max_length=20, choices=REGIONS)
    year = models.PositiveSmallIntegerField()
    low = models.DecimalField(max_digits=20, decimal_places=2)
    mid = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)  # 2 та нархда — йўқ
    high = models.DecimalField(max_digits=20, decimal_places=2)
    n = models.PositiveIntegerField(default=0)  # ҳисобга кирган нархлар
    last_date = models.DateField(null=True, blank=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("item", "region", "year")]
        ordering = ["-year", "region"]

    @property
    def single(self):
        return self.low == self.high


LEVELS = [("low", "Паст нарх"), ("mid", "Ўрта нарх"), ("high", "Юқори нарх")]
LEVEL_LABELS = dict(LEVELS)


class Config(models.Model):
    """Ягона ёзув (pk=1): янги база нархлашда ишлатиладими. Ёқилса — эски «Ресурс базаси» ўрнига шу ишлайди."""
    enabled = models.BooleanField(default=False)
    level = models.CharField(max_length=4, choices=LEVELS, default="mid")  # смета­чи танламаса — шу
    updated = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    @classmethod
    def get(cls) -> "Config":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
