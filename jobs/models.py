import re
import uuid
from pathlib import Path

from django.conf import settings
from django.db import models


class Setting(models.Model):
    """Смета­чининг охирги танловлари (масалан сводка фоизлари) — кейинги сметалар шу билан бошланади."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="narx_settings")
    key = models.CharField(max_length=64)
    value = models.JSONField(default=dict, blank=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "key"], name="setting_owner_key")]

    def __str__(self):
        return self.key


class Project(models.Model):
    """Смета­чи папкаси (ички папкалари билан). Ҳар бир ички папка — алоҳида ``Batch``
    (одатда битта локал ва битта ресурс файл), фонда навбат билан нархланади."""

    class Status(models.TextChoices):
        PROCESSING = "processing", "Ишланяпти"
        READY = "ready", "Текширинг — ҳисоблашга тайёр"  # файллар очилди ва жуфтланди, смета­чи бошлашини кутади
        DONE = "done", "Тайёр"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROCESSING)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")  # kimning ishi (o'chirish o'rniga faolsizlantiring)
    name = models.CharField(max_length=255)
    # [{"path": "2. Блок-А/1. Подземный часть/Рес подзем.xls", "size": 123, "skip": ""}] — skip: нега олинмагани
    files = models.JSONField(default=list)
    heartbeat = models.DateTimeField(null=True, blank=True)  # fondagi ishning oxirgi belgisi
    stop_requested = models.BooleanField(default=False)  # smetachi «Тўхтатиш»ni bosdi — fondagi ish keyingi faylda to'xtaydi
    run_started = models.DateTimeField(null=True, blank=True)   # fondagi ishning oxirgi boshlanishi
    run_finished = models.DateTimeField(null=True, blank=True)  # … va tugashi (ishlayotganda bo'sh)
    worker = models.CharField(max_length=120, blank=True, db_index=True)  # qaysi worker egallagan ("" — navbatda)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return self.name

    @property
    def dir(self) -> Path:
        return Path(settings.MEDIA_ROOT) / "projects" / str(self.id)

    def abs(self, rel: str) -> Path:
        return self.dir / rel


class Batch(models.Model):
    """Bir yoki bir nechta yuklangan fayl (obyekt): listlar tahlili va juftliklar.
    Tasdiqlangandan keyin har bir juftlik — alohida ``Job``."""

    class Status(models.TextChoices):
        REVIEW = "review", "Текширишда"
        DONE = "done", "Нархланди"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REVIEW)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")  # kimning ishi (o'chirish o'rniga faolsizlantiring)
    name = models.CharField(max_length=255)
    files = models.JSONField(default=list)   # [{"name": asl nom, "path": "conv/0.xlsx", "sheets": [...]}]
    sheets = models.JSONField(default=list)  # core.batch.SheetInfo.to_dict() ro'yxati
    pairs = models.JSONField(default=list)   # core.batch.Pair.to_dict() + {"include": bool}
    roles = models.JSONField(default=dict, blank=True)  # qo'lda list turi {"0:LRV": "local"}
    columns = models.JSONField(default=dict, blank=True)  # qo'lda ustunlar {"0:LRV": {"name": 3, "unit": 4, "start": 22, …}}

    # Papka yuklanganda: qaysi loyiha va qaysi ichki papka; fondagi ish bosqichi
    class Stage(models.TextChoices):
        QUEUED = "queued", "Навбатда"
        CONVERTING = "converting", "Файллар очиляпти"
        READY = "ready", "Ҳисоблашга тайёр"  # жуфтликлар топилди — смета­чи «Ҳисоблашни бошлаш»ни босади
        PRICING = "pricing", "Нархланяпти"
        STOPPED = "stopped", "Тўхтатилди"  # смета­чи тўхтатди — «Давом эттириш» билан қайта бошланади
        DONE = "done", "Тайёр"
        REVIEW = "review", "Текширинг"
        EMPTY = "empty", "Нархланадиган лист йўқ"
        FAILED = "failed", "Хато"

    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.CASCADE, related_name="parts")
    folder = models.CharField(max_length=500, blank=True)  # loyiha ichidagi yo'l, "" — ildiz
    stage = models.CharField(max_length=16, choices=Stage.choices, blank=True)
    message = models.TextField(blank=True)
    step = models.CharField(max_length=300, blank=True)  # fonda hozir nima qilinyapti: «2/5 файл очиляпти: …»
    step_since = models.DateTimeField(null=True, blank=True)  # joriy qadam (masalan, shu fayl) qachon boshlangan
    stage_since = models.DateTimeField(null=True, blank=True)  # joriy bosqich qachon boshlangan
    durations = models.JSONField(default=dict, blank=True)  # bosqichlarga ketgan vaqt, soniya {"converting": 12.5, "pricing": 40}

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
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")  # kimning ishi (o'chirish o'rniga faolsizlantiring)

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
    column_names = models.JSONField(default=dict, blank=True)  # ustun nomlari {"K": "…", "N": "Монтаж"}; N.. — qo'shilgan
    local_columns = models.JSONField(default=dict, blank=True)  # smetachi ko'rsatgan ustunlar (Batch.columns dan)
    res_columns = models.JSONField(default=dict, blank=True)
    cleared = models.JSONField(default=list, blank=True)  # «ТОПИЛМАДИ» so'zi olib tashlangan qatorlar [183, 281]
    base_period = models.JSONField(default=dict, blank=True)  # ресурс базаси: {"region", "year", "quarter"}; {} — ишлатилмайди
    table_checked = models.BooleanField(default=False)  # 2-bosqich: L/M ko'chirildi, jadval ko'rib chiqildi

    output_path = models.CharField(max_length=500, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    duration = models.FloatField(null=True, blank=True)  # oxirgi narxlash necha soniya davom etdi
    rows = models.PositiveIntegerField(null=True, blank=True)  # nechta qator ko'rib chiqildi (100/1000 qator tezligi)

    # Qayta hisoblash navbati (jobs/recalc.py): smetachining har o'zgarishi recalc_seq ni oshiradi, worker
    # hisoblab bo'lgach recalc_done ga yozadi. recalc_seq > recalc_done — navbatda yoki hisoblanyapti.
    recalc_seq = models.PositiveIntegerField(default=0)
    recalc_done = models.PositiveIntegerField(default=0)
    recalc_requested = models.DateTimeField(null=True, blank=True)
    recalc_worker = models.CharField(max_length=100, blank=True)  # egallagan worker (lease)
    recalc_heartbeat = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]

    @property
    def recalc_pending(self) -> bool:
        return self.recalc_seq > self.recalc_done

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
            # \x00-\x1f: лист номидаги қатор ташланиши ва ҳ.к. — Windows бундай файл номини рад этади
            stem = " ".join(re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", self.batch_label).split())[:80] or "smeta"
            return f"{stem}_narxlandi.xlsx"
        stem = Path(self.local_name).stem
        ext = ".xlsm" if self.local_name.lower().endswith(".xlsm") else ".xlsx"
        return f"{stem}_narxlandi{ext}"


class Worker(models.Model):
    """Fondagi ishchi jarayon (``manage.py run_worker``): panel uni ko'radi — tirikmi, nechta ish olgan."""
    name = models.CharField(max_length=120, unique=True)  # «host:pid»
    started = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField()
    threads = models.PositiveSmallIntegerField(default=1)
    busy = models.PositiveSmallIntegerField(default=0)  # hozir nechta loyiha ustida
    done = models.PositiveIntegerField(default=0)  # ishga tushgandan beri nechta loyiha

    class Meta:
        ordering = ["-last_seen"]

    def __str__(self):
        return self.name


class FileTiming(models.Model):
    """Bitta faylni ochish vaqti: .xls → .xlsx o'girish, listlar ro'yxatini o'qish va listlar tahlili.
    Panel statistikasi uchun — ish o'chirilsa ham tarix qoladi."""
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    job = models.ForeignKey(Job, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    name = models.CharField(max_length=255)
    ext = models.CharField(max_length=8)
    size = models.BigIntegerField(null=True, blank=True)
    method = models.CharField(max_length=16, blank=True)  # libreoffice | excel | "" (.xlsx — o'girilmaydi)
    convert_s = models.FloatField(null=True, blank=True)  # .xls → .xlsx
    read_s = models.FloatField(null=True, blank=True)     # listlar ro'yxatini o'qish
    analyze_s = models.FloatField(null=True, blank=True)  # listlar tahlili (turi, ustunlar, narxlar)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created"]

    @property
    def total_s(self):
        return sum(v for v in (self.convert_s, self.read_s, self.analyze_s) if v)
