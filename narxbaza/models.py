"""Янги нарх базаси: миллий кодсиз — ресурс номи + бирлиги бўйича.

* ``Item`` — ресурс картаси (номи, бирлиги, тоифаси).
* ``Price`` — битта нарх кузатуви: қаердан келди (смета файли ёки қўлда), ким киритди, вилоят, аниқ сана, сони.
  Ҳар ресурснинг «паспорти» — шу ёзувлар тарихи. Ҳеч нарса ўчирилмайди — ҳисобдан чиқарилади.
* ``Batch`` — бир марта тасдиқлаб киритилган тўплам (бир смета/папкадан ёки қўлда).
* ``Stat`` — ресурс × вилоят × йил: паст / ўрта / юқори нарх (``services.recalc``).
* ``Alias`` — ресурснинг бошқа номи (бирлаштирилган ёки эски номи): шу ном билан келган қатор ўша ресурсга тушади.
* ``Log`` — ресурс тарихи: нарх тузатилди, ҳисобдан чиқарилди, номи ўзгарди, бирлаштирилди, саватчага…

Саватча: ресурс ёки тўплам ўчирилганда аввал ``deleted_at`` қўйилади (``objects`` уларни кўрмайди, нархлари
ҳисобга кирмайди), тиклаш мумкин; бутунлай ўчириш — фақат саватчадан (``all_objects``)."""
from django.conf import settings
from django.db import models

from resbase.regions import REGIONS

CATEGORIES = [("I", "Иш ҳақи"), ("J", "Машина ва механизмлар"), ("K", "Материал"), ("M", "Ускуна"),
              ("L", "Кабель"), ("O", "Бошқа")]
CATEGORY_LABELS = dict(CATEGORIES)


class LiveManager(models.Manager):
    """Саватчадагиларсиз."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at=None)


class LivePrices(models.Manager):
    """Саватчадаги ресурс ёки тўпламнинг нархларисиз — ҳамма ҳисоб шу орқали."""

    def get_queryset(self):
        return super().get_queryset().filter(item__deleted_at=None, batch__deleted_at=None)


class Item(models.Model):
    key = models.CharField(max_length=560, unique=True)  # services.item_key(name, unit); саватчада — «~trash~pk»
    key_before = models.CharField(max_length=560, blank=True)  # саватчага тушганда асл калити (тиклашда қайтади)
    name = models.CharField(max_length=500)
    unit = models.CharField(max_length=40)
    category = models.CharField(max_length=1, choices=CATEGORIES, default="O")
    edited = models.BooleanField(default=False)  # админ тоифа/номни қўлда тузатган — йиғиш ўзгартирмайди
    # админ ўзи кўриб тоифасини тасдиқлаган: шундан кейин ҳеч қандай автоматика тоифани ўзгартирмайди,
    # кейин келган шу ресурс қатори текширувда «тасдиқланган ресурс» бўлиб чиқади
    verified = models.BooleanField(default=False, db_index=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)  # саватчада
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    objects = LiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["name"]

    @property
    def unit_key(self) -> str:
        return (self.key_before or self.key).rsplit("|", 1)[-1]

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
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)  # саватчада
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    objects = LiveManager()
    all_objects = models.Manager()

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
    excluded = models.BooleanField(default=False)  # админ ҳисобдан чиқарган — ҳисобга кирмайди
    outlier = models.BooleanField(default=False)  # бошқалардан 3 баробар фарқ — ҳисобга кирмайди (recalc)
    # админ белгилагани: шу вилоят/йилда паст / ўрта / юқори нарх айнан шу бўлади (четда бўлса ҳам; ҳисобдан кейин қўйилади)
    pin = models.CharField(max_length=4, blank=True, default="",
                           choices=[("low", "паст"), ("mid", "ўрта"), ("high", "юқори")])
    edited = models.BooleanField(default=False)  # админ тузатган (эски қиймати — Log'да)
    note = models.CharField(max_length=300, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    objects = LivePrices()
    all_objects = models.Manager()

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
        indexes = [models.Index(fields=["region", "year"])]  # нархлаш: вилоят + йил бўйича ҳаммаси

    @property
    def single(self):
        return self.low == self.high


class Alias(models.Model):
    """Ресурснинг бошқа номи + бирлиги: бирлаштирилганда ёки номи ўзгарганда эскиси шу ерда қолади —
    йиғишда ва нархлашда шу калит билан келган қатор ``item`` га тушади."""
    key = models.CharField(max_length=560, unique=True)
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="aliases")
    name = models.CharField(max_length=500)
    unit = models.CharField(max_length=40)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="+")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.unit})"


class Log(models.Model):
    """Ресурс тарихи — ким, қачон, нимани ўзгартирди. Ресурс бутунлай ўчса ҳам ёзув қолади (``title``)."""

    class Action(models.TextChoices):
        PRICE_EDIT = "price_edit", "Нарх тузатилди"
        EXCLUDE = "exclude", "Ҳисобдан чиқарилди"
        INCLUDE = "include", "Ҳисобга қайтарилди"
        RENAME = "rename", "Номи ўзгарди"
        CATEGORY = "category", "Тоифаси ўзгарди"
        MERGE = "merge", "Бирлаштирилди"
        ALIAS_DEL = "alias_del", "Бошқа номи олиб ташланди"
        BATCH_EDIT = "batch_edit", "Тўплам тузатилди"
        TRASH = "trash", "Саватчага ташланди"
        RESTORE = "restore", "Саватчадан тикланди"
        PURGE = "purge", "Бутунлай ўчирилди"
        PIN = "pin", "Нарх белгиланди"
        PRICE_DEL = "price_del", "Нарх ўчирилди"

    action = models.CharField(max_length=12, choices=Action.choices)
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.SET_NULL, related_name="logs")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.SET_NULL, related_name="logs")
    price = models.ForeignKey(Price, null=True, blank=True, on_delete=models.SET_NULL, related_name="logs")
    title = models.CharField(max_length=500)  # ресурс / тўплам номи (ўчса ҳам кўринади)
    detail = models.CharField(max_length=500, blank=True)  # «1 200 → 12 000», сабаби
    data = models.JSONField(default=dict, blank=True)  # эски / янги қийматлар
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="+")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-id"]


class DupIgnore(models.Model):
    """«Булар бошқа-бошқа ресурс» — текшириш навбатида шу жуфтлик икки нусха деб қайта кўрсатилмайди (a < b)."""
    a = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="+")
    b = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="+")
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="+")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("a", "b")]


class Intake(models.Model):
    """Текширув навбати: смета­чи иши ёки юкланган файлнинг ҳар қатори аввал шу ерга тушади. Тизим тоифани
    таклиф қилади, админ Excel кўринишида кўриб тасдиқлайди / тузатади / рад этади. Ҳамма қатор кўрилгандан
    кейингина «Базага ёзиш» мумкин — базага фақат тасдиқланганлари тушади (``intake.commit``)."""

    class Status(models.TextChoices):
        READING = "reading", "Файллар ўқилмоқда"
        FAILED = "failed", "Ўқиб бўлмади"
        OPEN = "open", "Текширилмоқда"
        DONE = "done", "Базага ёзилди"
        CANCELLED = "cancelled", "Бекор қилинди"

    kind = models.CharField(max_length=10, choices=Batch.Kind.choices)  # folder | object | smeta | file
    work_id = models.UUIDField(null=True, blank=True, db_index=True)
    title = models.CharField(max_length=500)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")  # смета эгаси (файлда — юклаган)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    file_path = models.CharField(max_length=300, blank=True)  # юкланган асл файл (MEDIA_ROOT ичида)
    view_path = models.CharField(max_length=300, blank=True)  # Excel кўриниш учун .xlsx нусха (асли .xls бўлса)
    file_kind = models.CharField(max_length=10, blank=True)  # excel | res
    files = models.JSONField(default=list, blank=True)  # [{"job": uuid|None, "file": "номи", "sheet": "лист"}]
    sig = models.CharField(max_length=16, blank=True)  # очилгандаги сметалар ҳолати (қайта нархланса — огоҳлантириш)
    region = models.CharField(max_length=20, choices=REGIONS, blank=True)
    region_hint = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # орқа фонда ўқиш: {"stage", "done", "total", "file", "error", "bad": [[номи, сабаби]], "src": [...], "root"}
    progress = models.JSONField(default=dict, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return self.title


class IntakeRow(models.Model):
    """Файлдаги битта ресурс қатори. Тоифа ресурс бўйича битта: бир қаторда ўзгартирилса, шу навбатдаги шу
    ном+бирликли ҳамма қаторда ўзгаради."""

    class State(models.TextChoices):
        NEW = "new", "Кўрилмаган"
        OK = "ok", "Тасдиқланди"
        SKIP = "skip", "Рад этилди"

    intake = models.ForeignKey(Intake, on_delete=models.CASCADE, related_name="rows")
    fi = models.PositiveSmallIntegerField(default=0)  # ``intake.files`` даги тартиби
    row = models.PositiveIntegerField()  # Excel қатори
    sheet = models.CharField(max_length=100, blank=True)
    name = models.CharField(max_length=500)
    unit = models.CharField(max_length=40)
    key = models.CharField(max_length=560, db_index=True)
    price = models.DecimalField(max_digits=20, decimal_places=4)
    qty = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    src_status = models.CharField(max_length=20, blank=True)  # нархлашдаги ҳолати (EXACT, MANUAL, FILE…)
    suggested = models.CharField(max_length=1, choices=CATEGORIES)  # тизим таклифи
    reason = models.CharField(max_length=300, blank=True)  # нега шу тоифа
    sure = models.BooleanField(default=False)  # таклиф ишончли (RES бўлими, бирлиги, тасдиқланган ресурс)
    category = models.CharField(max_length=1, choices=CATEGORIES)  # ҳозирги (сукут — таклиф; админ тузатади)
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")  # базада шу ресурс
    known = models.BooleanField(default=False)  # ``item`` админ тасдиқлаган ва тоифа бир хил — «такрорий»
    warn = models.CharField(max_length=300, blank=True)  # нарх 3× фарқ, бирлиги бошқа…
    state = models.CharField(max_length=4, choices=State.choices, default=State.NEW, db_index=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["fi", "row"]
        indexes = [models.Index(fields=["intake", "state"])]


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
