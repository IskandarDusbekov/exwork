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
    # юклашда танланган усул: True — «Автоматик», False — «Қўлда»; None — эски пакет (фойдаланувчи созламаси)
    auto = models.BooleanField(null=True, blank=True)
    # «Бошлаш» — fonda narxlash navbati (``jobs.pricing``): smetachi tanlovi {juftlik: {...}}; None — navbatda emas
    pricing = models.JSONField(null=True, blank=True)
    worker = models.CharField(max_length=120, blank=True)  # qaysi ishchi olgan (lease)
    heartbeat = models.DateTimeField(null=True, blank=True)  # ishchi tirikligi: 5 daqiqa jim bo'lsa — boshqasi oladi
    price_error = models.TextField(blank=True)  # oxirgi narxlash xatosi (sahifada ko'rsatiladi)

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
    # L/M га тизим таклифини қабул қилиб ўтказилган қаторлар [183, …] — ускуна базасига смета­чи овози эмас
    # (база ўз таклифини ўзи тасдиқлаб кетмасин). None — эски смета, маълумот йўқ.
    lm_accepted = models.JSONField(null=True, blank=True, default=None)
    prices = models.JSONField(default=dict, blank=True)      # qo'lda narx / tasdiqlangan taklif {"qator": narx}
    column_names = models.JSONField(default=dict, blank=True)  # ustun nomlari {"K": "…", "N": "Монтаж"}; N.. — qo'shilgan
    local_columns = models.JSONField(default=dict, blank=True)  # smetachi ko'rsatgan ustunlar (Batch.columns dan)
    res_columns = models.JSONField(default=dict, blank=True)
    cleared = models.JSONField(default=list, blank=True)  # «ТОПИЛМАДИ» so'zi olib tashlangan qatorlar [183, 281]
    marked = models.JSONField(default=list, blank=True)  # sariq belgilangan (so'z olingan, G sariq — «ko'rib chiqildi»)
    # birligi ham, taklifi ham yo'q qatorlar (bo'lim nomlari) bir marta o'zi tozalandi — smetachi qaytarsa, qayta tozalanmaydi
    auto_cleared = models.BooleanField(default=False)
    # шу смета қайси усулда нархланган: True — «Автоматик», False — «Қўлда»; None — эски (пакет / созлама)
    auto = models.BooleanField(null=True, blank=True)
    base_period = models.JSONField(default=dict, blank=True)  # ресурс базаси: {"region", "year", "quarter"}; {} — ишлатилмайди
    table_checked = models.BooleanField(default=False)  # 2-bosqich: L/M ko'chirildi, jadval ko'rib chiqildi

    output_path = models.CharField(max_length=500, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    duration = models.FloatField(null=True, blank=True)  # oxirgi narxlash necha soniya davom etdi
    rows = models.PositiveIntegerField(null=True, blank=True)  # nechta qator ko'rib chiqildi (100/1000 qator tezligi)
    rows_done = models.PositiveIntegerField(null=True, blank=True)  # шулардан ишлов берилгани (ресурс қатори, ҳолати бор)
    # ``result``дан қисқа жамлама (рўйхат ва дашборд учун): катта JSON ҳар сафар ўқилмасин — save() ўзи тўлдиради
    sum_total = models.FloatField(null=True, blank=True)  # result.totals.s_nds
    sum_not_priced = models.PositiveIntegerField(default=0)  # result.stats.NOT_PRICED
    sum_checks = models.BooleanField(default=False)  # result.checks бўш эмас

    # Qayta hisoblash navbati (jobs/recalc.py): smetachining har o'zgarishi recalc_seq ni oshiradi, worker
    # hisoblab bo'lgach recalc_done ga yozadi. recalc_seq > recalc_done — navbatda yoki hisoblanyapti.
    recalc_seq = models.PositiveIntegerField(default=0)
    recalc_done = models.PositiveIntegerField(default=0)
    recalc_requested = models.DateTimeField(null=True, blank=True)
    recalc_worker = models.CharField(max_length=100, blank=True)  # egallagan worker (lease)
    recalc_heartbeat = models.DateTimeField(null=True, blank=True)
    preview_ready = models.BooleanField(default=False)  # natija ko'rinishi keshda tayyor (jobs/preview.py)

    class Meta:
        ordering = ["-created"]
        # рўйхат учун тўлиқ (covering) индекс: SQLite жадвал қаторига тегмайди — катта ``result`` орқали
        # охирги устунларга (sum_*) етиб бориш учун уни бутун ўқиб чиқмайди (views.light_jobs)
        indexes = [models.Index(fields=["batch", "id", "created", "status", "owner", "batch_order", "local_name",
                                        "local_sheet", "sum_total", "sum_not_priced", "sum_checks", "rows", "rows_done"],
                                name="job_list_cover2")]

    SUMMARY_FIELDS = ["sum_total", "sum_not_priced", "sum_checks", "rows_done"]

    @property
    def recalc_pending(self) -> bool:
        return self.recalc_seq > self.recalc_done

    def fill_summary(self):
        res = self.result or {}
        total = (res.get("totals") or {}).get("s_nds")
        self.sum_total = float(total) if isinstance(total, (int, float)) else None
        self.sum_not_priced = int((res.get("stats") or {}).get("NOT_PRICED") or 0)
        self.sum_checks = bool(res.get("checks"))
        if res.get("rows") is not None:
            from .usage import processed_rows
            self.rows_done = processed_rows(res)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        result_saved = "result" not in self.get_deferred_fields() and (update_fields is None or "result" in update_fields)
        if result_saved:
            self.fill_summary()
            if update_fields is not None:
                kwargs["update_fields"] = [*update_fields, *self.SUMMARY_FIELDS]
        super().save(*args, **kwargs)
        if result_saved and self.status == self.Status.DONE and self.rows:
            from .usage import record
            record(self)  # қатор ҳисоби (тўлов учун) — смета ўчирилса ҳам қолади

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


class ProjectOfyorta(models.Model):
    """Папкадаги нархланган сметалардан офёрта: смета­чи белгилаган тартибда битта ЛРВ варағида, ҳар бири
    сариқ сарлавҳа ва ўз сводкаси билан, охирида умумий жами (``jobs.ofyorta``). Ҳар папкага битта."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Тузилмаган"
        BUILDING = "building", "Тузиляпти"
        READY = "ready", "Тайёр"
        FAILED = "failed", "Хато"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    # папка ёки алоҳида юкланган файллар (объект) — иккаласидан бири
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name="ofyorta", null=True, blank=True)
    batch = models.OneToOneField(Batch, on_delete=models.CASCADE, related_name="ofyorta", null=True, blank=True)
    name = models.CharField(max_length=300)
    # [{"job": id, "title": сариқ сарлавҳа, "include": bool}] — тартиби офёртадаги тартиб
    parts = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    error = models.TextField(blank=True)
    file = models.CharField(max_length=500, blank=True)  # project.dir га нисбатан
    built = models.DateTimeField(null=True, blank=True)
    total = models.FloatField(null=True, blank=True)  # кирган сметалар «Всего с НДС» йиғиндиси (нархлаш натижасидан)
    progress = models.JSONField(default=dict, blank=True)  # {"step", "done", "total"}
    worker = models.CharField(max_length=100, blank=True)  # тузишни эгаллаган worker (lease)
    heartbeat = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name

    @property
    def work(self):
        """Офёрта кимники: папка (``Project``) ёки объект (``Batch``) — файл шу ишнинг папкасида сақланади."""
        return self.project if self.project_id else self.batch


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


class SheetMemo(models.Model):
    """Смета­чи листни қўлда тушунтирди (тури, устунлари, биринчи қатор) — шундай тузилмали лист кейин келса,
    тизим ўзи қўллайди. Номга эмас, тузилмага боғланади (``core.batch.sheet_fingerprint``): устун кенгликлари ва
    шапкадаги бирлашган катаклар бир дастур / бир шаблондан чиққан файлларда бир хил, матни эса ҳар хил."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    fp = models.CharField(max_length=40, db_index=True)  # аниқ из
    fpl = models.CharField(max_length=40, db_index=True, blank=True)  # кенг из (тизим ўзи эплолмаганда)
    role = models.CharField(max_length=16, blank=True)  # local | resource | unknown | "" (тури ўзгармаган)
    columns = models.JSONField(default=dict, blank=True)  # {"name": 3, "unit": 4, "price": 6, "start_off": 2, …}
    sample = models.CharField(max_length=300, blank=True)  # қайси файл / листда кўрсатилган (смета­чига эслатиш)
    uses = models.PositiveIntegerField(default=0)  # неча марта ўзи қўлланди
    # қайси объектда кўрсатилган (админ панелда намунани очиш учун); объект ўчирилса ҳам эслатма қолади
    batch_id = models.UUIDField(null=True, blank=True)
    sheet_key = models.CharField(max_length=300, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated"]
        constraints = [models.UniqueConstraint(fields=["owner", "fp"], name="sheetmemo_owner_fp")]

    def payload(self) -> dict:
        return {"role": self.role, "columns": self.columns or {}}

    def __str__(self):
        return self.sample or self.fp


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


class UploadTiming(models.Model):
    """Битта юклаш (файллар, папка ёки архив): браузердан серверга юбориш (``upload_s`` — смета­чининг
    интернети, браузер ўлчайди) ва сервердаги иш (``server_s`` — сақлаш, архив очиш, .xls ўгириш, таҳлил).
    Катта файлда вақтнинг кўпи шу ерда кетади — ``FileTiming`` фақат файлни очишни ўлчайди."""
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=10)  # object | folder | smeta
    name = models.CharField(max_length=255, blank=True)
    files = models.PositiveIntegerField(default=0)
    size = models.BigIntegerField(default=0)  # юборилган байт (архив — сиқилган ҳолида)
    upload_s = models.FloatField(null=True, blank=True)
    server_s = models.FloatField(null=True, blank=True)
    error = models.CharField(max_length=300, blank=True)
    work_id = models.CharField(max_length=40, blank=True)  # очилган объект / папка / смета (ўчирилса ҳам тарих қолади)

    class Meta:
        ordering = ["-created"]

    @property
    def total_s(self):
        return sum(v for v in (self.upload_s, self.server_s) if v)

    @property
    def kind_label(self):
        return {"object": "файллар", "folder": "папка ёки архив", "smeta": "смета"}.get(self.kind, self.kind)

    @property
    def mbps(self):
        """Интернет тезлиги, МБ/сония."""
        return self.size / (1024 * 1024) / self.upload_s if self.upload_s and self.size else None


class TenderSettings(models.Model):
    """Объект таҳлили созламалари (панел → «Объект таҳлили»): ёқилганми ва таҳлил нимага асосланади
    (``jobs.tender.PARAMS``). Битта ёзув (pk=1)."""
    enabled = models.BooleanField(default=False)
    params = models.JSONField(default=dict, blank=True)  # {калит: қиймат}; йўқлари — стандарт
    updated = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")


class TenderFactor(models.Model):
    """Админнинг тажриба омили: қурувчи амалда смета нархидан бошқача сарфлайди ёки қўшимча олади. Таъсири
    ``effect`` % — харажатга (иш ҳақи, машина, материал …) ёки даромадга. Фойдаланувчига фақат ``visible``
    бўлса ``user_text`` кўринади; ``note`` — фақат админ учун, ҳеч қаерда кўрсатилмайди."""

    class Scope(models.TextChoices):
        ALL = "all", "Барча тўғри харажатлар"
        I = "I", "Иш ҳақи (I)"
        J = "J", "Машина ва механизмлар (J)"
        K = "K", "Материаллар (K)"
        L = "L", "Кабель маҳсулотлари (L)"
        M = "M", "Ускуналар (M)"
        PROCHIE = "prochie", "Прочие затраты (устама)"
        REVENUE = "revenue", "Даромад (тўлов)"

    title = models.CharField(max_length=200)
    scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.ALL)
    effect = models.FloatField(default=0)  # %: харажатда манфий — арзонроқ; даромадда мусбат — кўпроқ
    active = models.BooleanField(default=True)
    visible = models.BooleanField(default=False)
    user_text = models.CharField(max_length=300, blank=True)
    note = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title


class RowUsage(models.Model):
    """Қатор ҳисоби — келажакда тўлов шунга қараб: ҳар нархланган смета (Job) — битта ёзув.
    Смета ўчирилса ҳам ёзув қолади (``job_id`` — FK эмас). Қайта нархланса ёки қайта ҳисобланса сонлари
    янгиланади, лекин сана — биринчи нархланган пайт — ўзгармайди: бир сметани қайта-қайта ҳисоблаш
    иккинчи марта санаб кетилмайди. ``rows_total`` — сметадаги жами қатор, ``rows_done`` — ишлов берилгани."""

    job_id = models.UUIDField(unique=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="row_usage")
    created = models.DateTimeField(db_index=True)  # биринчи марта нархланган пайт
    updated = models.DateTimeField(auto_now=True)
    kind = models.CharField(max_length=10, default="smeta")  # smeta | object | folder
    work_id = models.UUIDField(null=True, blank=True)  # смета / объект / папка
    title = models.CharField(max_length=255, blank=True)
    rows_total = models.PositiveIntegerField(default=0)
    rows_done = models.PositiveIntegerField(default=0)
    runs = models.PositiveIntegerField(default=1)  # неча марта нархланди / қайта ҳисобланди (маълумот учун)

    class Meta:
        ordering = ["-created"]
        indexes = [models.Index(fields=["owner", "created"])]

    def __str__(self):
        return f"{self.title} · {self.rows_done}/{self.rows_total}"
