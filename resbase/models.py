"""Ресурс базаси: narxlangan smetalardan yig'iladigan milliy klassifikator kodlari va viloyat × davr narxlari.

Kod — asosiy kalit (``000258`` va ``258`` bitta: oldidagi nollarsiz saqlanadi). Har bir smetadagi narx — alohida
kuzatuv (``Observation``); bazaviy narx — shu viloyat va davrdagi kuzatuvlar medianasi (``services.price_table``)."""
from django.conf import settings
from django.db import models

from .regions import REGIONS

QUARTERS = [(1, "1-чорак"), (2, "2-чорак"), (3, "3-чорак"), (4, "4-чорак")]
CATEGORIES = [("I", "Меҳнат ресурслари (иш ҳақи)"), ("J", "Машина ва механизмлар"), ("K", "Материаллар ва конструкциялар"),
              ("L", "Кабель маҳсулотлари"), ("M", "Ускуналар")]


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
    """Bazaga qo'shilgan ish (papka / obyekt / smeta) yoki to'g'ridan-to'g'ri yuklangan RES fayl va uning narxlari
    qaysi viloyat va davrga tegishli. Fayl uchun ``work_id`` — o'zi yaratilgan UUID (ishga bog'lanmagan)."""

    class Kind(models.TextChoices):
        FOLDER = "folder", "Папка"
        OBJECT = "object", "Объект"
        SMETA = "smeta", "Смета"
        FILE = "file", "Файл"

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
    # faqat «Файл»: saqlangan nusxa (MEDIA_ROOT ga nisbatan), bayt xeshi va mazmun izi (resfile.fingerprint) — takror
    file_path = models.CharField(max_length=300, blank=True)
    # «bp1010» (narxlar) va «bv1010» (ishlar) — bitta smetaning ikki fayli: shu kalit bir xil (davr + raqam)
    pair_key = models.CharField(max_length=60, blank=True, db_index=True)
    file_hash = models.CharField(max_length=64, blank=True, db_index=True)
    fingerprint = models.CharField(max_length=64, blank=True, db_index=True)
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
    def is_file(self) -> bool:
        return self.kind == self.Kind.FILE

    @property
    def period_known(self) -> bool:
        return bool(self.region and self.year)  # давр — вилоят ва йил (чорак ишлатилмайди)

    @property
    def period_label(self) -> str:
        return f"{self.year}-йил" if self.year else "номаълум давр"


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
    # иш турлари: шу файл (ёки жуфти — bp↔bv) ЛРВ'сида бу код қайси тўпламлар ишида ишлатилган — «,Е01,Е27,»
    books = models.CharField(max_length=300, blank=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["resource", "source"])]

    @property
    def book_list(self) -> list:
        return [b for b in self.books.split(",") if b]

    @property
    def flag_list(self) -> list:
        return [(f, FLAG_LABELS.get(f, f)) for f in self.flags.split(",") if f]


class BookName(models.Model):
    """Тўплам (иш тури) номи — админ ёзган («Е01», «Ц38», «ТР»). Ёзилмаган бўлса — стандарт ном (normbook)."""
    key = models.CharField(max_length=6, unique=True)
    name = models.CharField(max_length=200)
    updated = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        ordering = ["key"]


class WorkItem(models.Model):
    """ЛРВ: манбадаги битта иш — норма шифри («Е0101-197-02»), тўплами («Е01» — иш тури), номи, бирлиги, ҳажми.
    Бир смета ичида бир шифр бир неча марта учраши мумкин (ҳар бири — ўз ҳажми билан алоҳида ёзилади)."""
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="works")
    sheet = models.CharField(max_length=60, blank=True)
    row = models.PositiveIntegerField()
    shifr = models.CharField(max_length=40, db_index=True)
    raw = models.CharField(max_length=120, blank=True)  # файлда қандай ёзилган («… ДОП. 11 ГОСАРХИТЕКТСТРОЙ …»)
    book = models.CharField(max_length=6, db_index=True)
    name = models.CharField(max_length=500)
    unit = models.CharField(max_length=60, blank=True)
    qty = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    section = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["source", "row"]


class WorkResource(models.Model):
    """Иш ичидаги ресурс: ``norma`` — 1 бирлик ишга, ``qty`` — жами. ``extra`` — нормада йўқ, иш ортидан алоҳида
    ёзилган материал (нормаси йўқ). ``code`` бўш — кодсиз."""
    work = models.ForeignKey(WorkItem, on_delete=models.CASCADE, related_name="resources")
    row = models.PositiveIntegerField()
    code = models.CharField(max_length=12, blank=True, db_index=True)
    raw_code = models.CharField(max_length=40, blank=True)
    name = models.CharField(max_length=500)
    unit = models.CharField(max_length=40, blank=True)
    norma = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    qty = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    extra = models.BooleanField(default=False)

    class Meta:
        ordering = ["work", "row"]


def counted_q(prefix: str = "") -> models.Q:
    """Medianaga kiradigan kuzatuvlar: qo'lda emas, istisno emas, tekshiruvda emas. ``prefix`` — «observations__»."""
    return (models.Q(**{f"{prefix}manual": False, f"{prefix}excluded": False})
            & ~models.Q(**{f"{prefix}review": Observation.Review.PENDING}))


# ===================== ускуна ва кабель базаси =====================
LM_CATEGORIES = [("M", "Ускуна"), ("L", "Кабель"), ("K", "Ускуна эмас (материал)")]


class LMEntry(models.Model):
    """Ускуна ва кабель базасидаги битта ресурс (номи бўйича; коди бўлса — код ҳам). Тури икки йўлдан:

    * ўзи ўрганади — ``LMVote``: ҳар нархланган сметадан (RES'да «ОБОРУДОВАНИЕ»/«КАБЕЛЬ» бўлимида турган
      ресурслар, смета­чининг L/M га кўчиргани ёки қайтаргани). ``category`` — овозлар кўпи;
    * админ ўргатади (бирма-бир ёки Excel) — ``locked``: овозлардан қатъи назар шу тур.

    Нархлашда фақат таклиф (core.lmbase); «ускуна эмас» (K) — сўзга қараб нотўғри таклиф қилинмасин."""

    class Origin(models.TextChoices):
        AUTO = "auto", "Ўзи ўрганди"
        ADMIN = "admin", "Қўлда ўргатилди"
        EXCEL = "excel", "Excel'дан ўргатилди"

    key = models.CharField(max_length=400, unique=True)  # core.lmbase.lm_key(name)
    name = models.CharField(max_length=500)
    unit = models.CharField(max_length=40, blank=True)
    code = models.CharField(max_length=12, blank=True, db_index=True)  # миллий код (олдидаги нолларсиз)
    category = models.CharField(max_length=1, choices=LM_CATEGORIES, blank=True)  # бўш — овоз етарли эмас
    locked = models.BooleanField(default=False)  # админ ўргатган — овозлар ўзгартирмайди
    origin = models.CharField(max_length=8, choices=Origin.choices, default=Origin.AUTO)
    votes = models.JSONField(default=dict, blank=True)  # {"M": 12, "K": 1, "user_M": 2} — LMVote'дан ҳисобланади
    note = models.CharField(max_length=200, blank=True)  # админ изоҳи ёки Excel файл номи
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} → {self.category or '?'}"


class LMVote(models.Model):
    """Битта сметадан битта далил: шу ресурс шу сметада қайси турда. Смета қайта ҳисобланса — алмаштирилади."""

    class Kind(models.TextChoices):
        RES = "res", "RES бўлими"
        USER = "user", "Смета­чи танлови"

    entry = models.ForeignKey(LMEntry, on_delete=models.CASCADE, related_name="lm_votes")
    job_id = models.UUIDField(db_index=True)
    owner_id = models.IntegerField(null=True, blank=True)  # смета эгаси — смета­чи овози фойдаланувчилар бўйича санади
    src = models.CharField(max_length=40, blank=True)  # RES файлнинг изи — битта файл 20 сметада бўлса ҳам битта овоз
    category = models.CharField(max_length=1)  # L | M | K
    kind = models.CharField(max_length=4, choices=Kind.choices)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["entry", "job_id", "kind"], name="lmvote_uniq")]
