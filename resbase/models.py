"""Ресурс базаси: narxlangan smetalardan yig'iladigan milliy klassifikator kodlari va viloyat × davr narxlari.

Kod — asosiy kalit (``000258`` va ``258`` bitta: oldidagi nollarsiz saqlanadi). Har bir smetadagi narx — alohida
kuzatuv (``Observation``); bazaviy narx — shu viloyat va davrdagi kuzatuvlar medianasi (``services.price_table``)."""
from django.conf import settings
from django.db import models

from .regions import REGIONS

QUARTERS = [(1, "1-чорак"), (2, "2-чорак"), (3, "3-чорак"), (4, "4-чорак")]
CATEGORIES = [("I", "Иш ҳақи"), ("J", "Машина"), ("K", "Материал"), ("L", "Кабель"), ("M", "Ускуна")]


class BaseConfig(models.Model):
    """Ягона ёзув (pk=1): базадан нархлаш ёқилганми."""
    enabled = models.BooleanField(default=False)
    # смета­чи ҳали танламаган бўлса — юклаш саҳифасидаги вилоят ва давр
    region = models.CharField(max_length=20, choices=REGIONS, blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    quarter = models.PositiveSmallIntegerField(choices=QUARTERS, null=True, blank=True)
    updated = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    @classmethod
    def get(cls) -> "BaseConfig":
        return cls.objects.get_or_create(pk=1)[0]

    @property
    def period(self) -> dict:
        return {"region": self.region, "year": self.year, "quarter": self.quarter}


class Resource(models.Model):
    code = models.CharField(max_length=12, unique=True)  # «258» (oldidagi nollarsiz)
    name = models.CharField(max_length=500)
    unit = models.CharField(max_length=40)  # ko'rsatish uchun («МАШ.-Ч»)
    unit_key = models.CharField(max_length=40, db_index=True)  # solishtirish uchun («машч»)
    category = models.CharField(max_length=1, choices=CATEGORIES, blank=True)
    edited = models.BooleanField(default=False)  # admin qo'lda tuzatgan — yig'ish nom/birlikni almashtirmaydi
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def code_int(self):
        return int(self.code) if self.code.isdigit() else 0


class ResourceName(models.Model):
    """Smetalarda uchragan nom variantlari (qidiruv va tekshirish uchun)."""
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name="names")
    name = models.CharField(max_length=500)
    unit = models.CharField(max_length=40, blank=True)
    seen = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["resource", "name", "unit"], name="resname_uniq")]
        ordering = ["-seen"]


class Source(models.Model):
    """Bazaga qo'shilgan ish (papka / obyekt / smeta) va uning narxlari qaysi viloyat va davrga tegishli."""

    class Kind(models.TextChoices):
        FOLDER = "folder", "Папка"
        OBJECT = "object", "Объект"
        SMETA = "smeta", "Смета"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    work_id = models.UUIDField()
    title = models.CharField(max_length=500)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")  # smeta egasi
    region = models.CharField(max_length=20, choices=REGIONS, blank=True)  # bo'sh — номаълум
    year = models.PositiveSmallIntegerField(null=True, blank=True)  # bo'sh — номаълум
    quarter = models.PositiveSmallIntegerField(choices=QUARTERS, null=True, blank=True)
    region_hint = models.CharField(max_length=80, blank=True)  # viloyat qaysi so'zdan taxmin qilingan
    stats = models.JSONField(default=dict, blank=True)  # {"rows": .., "taken": .., "skipped": {sabab: soni}}
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="+")
    created = models.DateTimeField(auto_now_add=True)
    harvested = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]
        constraints = [models.UniqueConstraint(fields=["kind", "work_id"], name="source_work_uniq")]

    def __str__(self):
        return self.title

    @property
    def period_known(self) -> bool:
        return bool(self.region and self.year and self.quarter)

    @property
    def period_label(self) -> str:
        return f"{self.year} · {self.quarter}-чорак" if self.year and self.quarter else "номаълум давр"


FLAG_LABELS = {"name": "бошқа ном", "unit": "бирлик бошқа", "price": "нарх кескин фарқ"}


class Observation(models.Model):
    """Bitta smetadagi bitta kod narxi (bir smeta ichida takrorlansa — bir marta).

    Shubhali kuzatuv (``flags``: boshqa nom, boshqa birlik, narx medianadan keskin farq) admin «қабул» qilmaguncha
    (``review=pending``) medianaga kirmaydi; «рад» — ``excluded`` bilan bir xil."""

    class Review(models.TextChoices):
        NONE = "", "—"
        PENDING = "pending", "текширувда"
        ACCEPTED = "accepted", "қабул қилинган"
        REJECTED = "rejected", "рад этилган"

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="observations")
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name="observations")
    job_id = models.UUIDField()
    row = models.PositiveIntegerField()  # lokal listdagi qator (havola uchun)
    name = models.CharField(max_length=500)  # smetada qanday yozilgan
    unit = models.CharField(max_length=40)
    price = models.DecimalField(max_digits=20, decimal_places=4)
    manual = models.BooleanField(default=False)  # smetachi qo'lda qo'ygan narx — medianaga kirmaydi
    excluded = models.BooleanField(default=False)  # admin istisno qilgan — medianaga kirmaydi
    flags = models.CharField(max_length=30, blank=True)  # «name,unit,price» — services.recheck
    review = models.CharField(max_length=10, choices=Review.choices, blank=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["resource", "source"])]

    @property
    def flag_list(self) -> list:
        return [(f, FLAG_LABELS.get(f, f)) for f in self.flags.split(",") if f]


def counted_q(prefix: str = "") -> models.Q:
    """Medianaga kiradigan kuzatuvlar: qo'lda emas, istisno emas, tekshiruvda emas. ``prefix`` — «observations__»."""
    return (models.Q(**{f"{prefix}manual": False, f"{prefix}excluded": False})
            & ~models.Q(**{f"{prefix}review": Observation.Review.PENDING}))
