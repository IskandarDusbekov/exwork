"""Текширув навбати (ярим автомат йиғиш): ҳар қатор аввал навбатга тушади, тизим тоифани белгилайди,
админ Excel кўринишида кўриб тасдиқлайди / тузатади / рад этади, кейин «Базага ёзиш».

Хатосиз база учун қоидалар:
* базага фақат админ «тасдиқлади» деган қатор тушади; битта ҳам кўрилмаган қатор қолса — ёзилмайди;
* тоифа — ресурс бўйича битта: бир қаторда ўзгартирилса, навбатдаги шу ном+бирликли ҳамма қаторда ўзгаради;
* ёзилгандан кейин ресурс «тасдиқланган» бўлади: кейин ҳеч қандай автоматика унинг тоифасини ўзгартирмайди,
  кейинги файлларда шу ресурс ўзи белгиланиб «такрорий» рўйхатга тушади (нархи 3× фарқ қилмаса);
* ҳар қарор дарҳол сақланади — браузер ёпилса ҳам иш йўқолмайди."""
import datetime as dt
import logging
from datetime import date as _date
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from . import services as S
from .models import CATEGORY_LABELS, Alias, Batch, Intake, IntakeRow, Item, Log

log = logging.getLogger(__name__)

WORK_KINDS = ("folder", "object", "smeta")
FAR = Decimal(3)  # базадаги нархдан шунча баробар фарқ — огоҳлантириш, «такрорий» эмас
SKIP_STATUS = {"WORK_HEADER", "MACHINIST", "NO_PRICE", "UNIT_MISMATCH"}


# ===================== таклиф =====================
def _lm():
    try:
        from resbase.lm import load_base
        return load_base()
    except Exception:  # noqa: BLE001
        return None


def suggest(name, unit, item, src=None, lm=None) -> tuple:
    """``(тоифа, сабаби, ишончли)``. ``src`` — манбанинг ўз белгиси ``(тоифа, сабаби, ишончли)`` (RES бўлими,
    смета­чи танлови, файлдаги устун). Тартиб: сиз тасдиқлаган ресурс → манба → бирлиги → ускуна/кабель
    базаси → номидаги сўз → базадаги (текширилмаган) тоифаси → материал."""
    if item is not None and item.verified:
        why = f"сиз тасдиқлаган ресурс: {CATEGORY_LABELS[item.category]}"
        if src and src[0] and src[0] != item.category:
            why += f" (манбада — {CATEGORY_LABELS.get(src[0], src[0])})"
        return item.category, why, True
    if src and src[0] and src[2]:
        return src[0], src[1], True
    u = S._unit_cat(name, unit)
    if u:
        return u[0], u[1], True
    from core.classify import KIND_COLUMN, OTHER, name_hint
    if lm is not None:
        hit = lm.lookup(name)
        if hit and hit.kind != OTHER:
            return KIND_COLUMN[hit.kind], f"ускуна/кабель базаси: {hit.reason}", bool(hit.strong)
    h = name_hint(name)
    if h:
        return KIND_COLUMN[h[0]], f"номида «{h[1]}…»", False
    if src and src[0]:
        return src[0], src[1] or "манбада шундай", False
    if item is not None and item.category != "O":
        return item.category, "базада шундай (ҳали текширилмаган)", False
    return "K", "белги топилмади — материал деб олинди", False


# ===================== навбат очиш =====================
def _job_src(r) -> tuple:
    if r.get("override") or r.get("manual") and r.get("category"):
        return r.get("category"), "смета­чи ўзи танлаган", bool(r.get("override"))
    if r.get("suggested_category"):
        return r["suggested_category"], r.get("suggest_reason") or "нархлаш таклифи", bool(r.get("suggest_sure"))
    if r.get("res_section"):
        cat = r.get("category")
        return cat, f"RES'да «{CATEGORY_LABELS.get(cat, cat)}» бўлимида", True
    return r.get("category") or r.get("default_category"), "", False


def _job_raw(jobs) -> tuple:
    files, raw = [], []
    for job in jobs:
        fi = len(files)
        files.append({"job": str(job.id), "file": job.local_name[:200], "sheet": job.local_sheet or ""})
        for r in (job.result or {}).get("rows") or []:
            if (r.get("status") or "") in SKIP_STATUS or S.from_base(r):
                continue  # базадан олинган нарх — базанинг ўзи, янги кузатув эмас
            raw.append({"fi": fi, "sheet": job.local_sheet or "", "row": int(r.get("row") or 0), "name": r.get("name"),
                        "unit": r.get("unit"), "price": r.get("g"),
                        "qty": r.get("qty_total") if r.get("qty_total") is not None else r.get("qty"),
                        "status": "MANUAL" if r.get("manual") else (r.get("status") or ""), "src": _job_src(r)})
    return files, raw


def _clean(raw) -> list:
    out = []
    for r in raw:
        name, unit = str(r["name"] or "").strip(), str(r["unit"] or "").strip()
        price = S._dec(r["price"]) if not isinstance(r["price"], bool) else None
        if not name or not unit or price is None or price <= 0 or not r["row"]:
            continue
        q = S._dec(r.get("qty"))
        out.append({**r, "name": name[:500], "unit": unit[:40], "key": S.item_key(name, unit),
                    "price": price.quantize(Decimal("0.0001")), "qty": q if q is not None and q > 0 else None})
    return out


def _item_map(keys) -> dict:
    keys = list(keys)
    out = {}
    for i in range(0, len(keys), 500):
        part = keys[i:i + 500]
        out.update({it.key: it for it in Item.objects.filter(key__in=part)})
        for al in Alias.objects.filter(key__in=part, item__deleted_at=None).select_related("item"):
            out.setdefault(al.key, al.item)
    return out


def _other_units(keys) -> dict:
    """{ном қисми: {бирликлар}} — базада шу ном бошқа бирликда ҳам борми."""
    names = {k.rsplit("|", 1)[0] for k in keys}
    out = {}
    names = list(names)
    for i in range(0, len(names), 200):
        q = Q()
        for n in names[i:i + 200]:
            q |= Q(key__startswith=n + "|")
        for k, u in Item.objects.filter(q).values_list("key", "unit"):
            out.setdefault(k.rsplit("|", 1)[0], {})[k] = u
    return out


def ref_stats(item_ids, region="") -> dict:
    """{item_id: (Stat, шу вилоятникими)} — солиштириш учун базадаги нарх: аввал навбат вилояти (``region``);
    у ерда нархи йўқ ресурс — энг охирги йил, кўп нархли бошқа вилоят билан (``False`` — шуни кўрсатиш учун)."""
    ids = list(item_ids)
    out = {k: (s, True) for k, s in S.best_stat(ids, region=region).items()} if region else {}
    rest = [i for i in ids if i not in out]
    if rest:
        out.update({k: (s, not region) for k, s in S.best_stat(rest).items()})
    return out


CMP_ALL = "all"


def cmp_region(intake: Intake) -> str:
    """Базадаги нархлар қайси вилоятники билан солиштирилади: ``progress["cmp"]`` — ``""`` (навбат вилояти),
    ``"all"`` (умумий база — ҳамма вилоятлар, қайтаради ``""``) ёки вилоят коди."""
    c = (intake.progress or {}).get("cmp", "")
    if c == CMP_ALL:
        return ""
    return c or intake.region


def _region_name(code) -> str:
    from resbase.regions import REGION_LABELS
    return REGION_LABELS.get(code, code)


def _warns(price, key, it, ref, ps, units, region) -> list:
    """Қатор огоҳлантиришлари: базадаги нархдан 3× фарқ (навбат вилояти билан), навбат ичида 3× фарқ,
    шу ном бошқа бирликда."""
    warn = []
    st, own = ref.get(it.pk, (None, True)) if it is not None else (None, True)
    base = (st.mid if st.mid is not None else st.low) if st else None
    if base and base > 0:
        x = price / base
        if x >= FAR or x <= 1 / FAR:
            times = f"{(x if x > 1 else 1 / x):.1f}".replace(".", ",")
            what = f"{times}× {'қиммат' if x > 1 else 'арзон'} (базада {S.fmt(base)})"
            if not region:
                warn.append(f"базадаги нархдан {what}, {_region_name(st.region)}")
            elif own:
                warn.append(f"{_region_name(region)} нархидан {what}")
            else:
                warn.append(f"{_region_name(region)}да нархи йўқ — {_region_name(st.region)} нархидан {what}")
    if ps and max(ps) / min(ps) >= FAR:
        warn.append(f"шу навбатда бу ресурс {len(ps)} та қаторда, нархи 3× дан кўп фарқ: "
                    f"{S.fmt(min(ps))} – {S.fmt(max(ps))}")
    other = {k: u for k, u in units.get(key.rsplit("|", 1)[0], {}).items() if k != key}
    if other and (it is None or it.key not in other):
        warn.append("базада бу ном бошқа бирликда бор: " + ", ".join(sorted(set(other.values())))[:120])
    return warn


def _build(intake: Intake, rows: list) -> None:
    keys = {r["key"] for r in rows}
    items = _item_map(keys)
    region = cmp_region(intake)
    ref = ref_stats([i.pk for i in items.values()], region)
    units = _other_units(keys)
    lm = _lm()
    by_key = {}
    for r in rows:
        by_key.setdefault(r["key"], []).append(r["price"])
    sug_cache, objs = {}, []
    for r in rows:
        it = items.get(r["key"])
        if r["key"] not in sug_cache:
            sug_cache[r["key"]] = suggest(r["name"], r["unit"], it, r.get("src"), lm)
        cat, why, sure = sug_cache[r["key"]]
        warn = _warns(r["price"], r["key"], it, ref, by_key[r["key"]], units, region)
        objs.append(IntakeRow(intake=intake, fi=r.get("fi", 0), row=r["row"], sheet=r.get("sheet", "")[:100],
                              name=r["name"], unit=r["unit"], key=r["key"], price=r["price"], qty=r["qty"],
                              src_status=(r.get("status") or "")[:20], suggested=cat, reason=why[:300], sure=sure,
                              category=cat, item=it, warn="; ".join(warn)[:300],
                              known=bool(it is not None and it.verified and cat == it.category and not warn)))
    IntakeRow.objects.bulk_create(objs, batch_size=1000)


@transaction.atomic
def warns_stale(intake: Intake) -> bool:
    """Огоҳлантиришлар ҳозирги солиштириш вилояти билан ҳисобланмаган (эски навбат ёки вилоят ўзгарган)."""
    return intake.status == Intake.Status.OPEN and (intake.progress or {}).get("warn_region") != cmp_region(intake)


def set_region(intake: Intake, region: str) -> int:
    """Навбат вилоятини ўзгартиради ва базага ёзилмаган қаторлар огоҳлантиришини солиштириш вилояти
    (``cmp_region``) нархлари билан қайта ҳисоблайди (``known`` ҳам). Ўзгарган қаторлар сонини қайтаради."""
    from resbase.regions import REGION_LABELS
    if region and region not in REGION_LABELS:
        raise ValueError("Номаълум вилоят")
    return _rewarn(intake, region=region)


def set_cmp(intake: Intake, cmp: str) -> int:
    """Солиштириш: ``""`` — навбат вилояти, ``"all"`` — умумий база, ёки вилоят коди. Огоҳлантиришлар қайта."""
    from resbase.regions import REGION_LABELS
    if cmp and cmp != CMP_ALL and cmp not in REGION_LABELS:
        raise ValueError("Номаълум вилоят")
    return _rewarn(intake, cmp=cmp)


@transaction.atomic
def _rewarn(intake: Intake, region=None, cmp=None) -> int:
    intake = Intake.objects.select_for_update().get(pk=intake.pk)
    if intake.status != Intake.Status.OPEN:
        if region is not None:
            raise ValueError("Навбат ёпилган")
        intake.progress = {**(intake.progress or {}), "cmp": cmp}  # ёпилганда фақат кўриш учун
        intake.save(update_fields=["progress", "updated"])
        return 0
    pr = dict(intake.progress or {})
    if region is not None:
        intake.region = region
    if cmp is not None:
        pr["cmp"] = cmp
    intake.progress = pr
    region = cmp_region(intake)
    pr["warn_region"] = region
    intake.save(update_fields=["region", "progress", "updated"])
    rows = list(_live(intake).select_related("item"))
    if not rows:
        return 0
    keys = {r.key for r in rows}
    ref = ref_stats({r.item_id for r in rows if r.item_id}, region)
    units = _other_units(keys)
    by_key = {}
    for r in rows:
        by_key.setdefault(r.key, []).append(r.price)
    changed = []
    for r in rows:
        warn = "; ".join(_warns(r.price, r.key, r.item, ref, by_key[r.key], units, region))[:300]
        known = bool(r.item is not None and r.item.verified and r.category == r.item.category and not warn)
        if warn != r.warn or known != r.known:
            r.warn, r.known = warn, known
            changed.append(r)
    IntakeRow.objects.bulk_update(changed, ["warn", "known"], batch_size=1000)
    return len(changed)


def open_for_work(kind, work, user) -> Intake:
    """Иш учун очиқ навбат (бўлса — ўшани қайтаради)."""
    ex = Intake.objects.filter(kind=kind, work_id=work.pk, status=Intake.Status.OPEN).first()
    if ex is not None:
        return ex
    jobs = S.jobs_of(kind, work)
    region, hint = S.old.region_suggestion(kind, work)
    with transaction.atomic():
        files, raw = _job_raw(jobs)
        it = Intake.objects.create(kind=kind, work_id=work.pk, title=S.title_of(kind, work)[:500],
                                   owner_id=getattr(work, "owner_id", None), created_by=S._uid(user), files=files,
                                   sig=S.signature(S.rows_from_jobs(jobs)), region=region or "",
                                   region_hint=(hint or "")[:100])
        _build(it, _clean(raw))
    return it


def queue_prices(job, picks, user) -> "Intake | None":
    """Смета­чи «нархни базага ҳам сақлаш» деб ёзган нархлар — **тўғридан-тўғри базага эмас**, админ текширув
    навбатига (база хатосиз бўлсин). ``picks`` — ``[(натижа қатори, нарх)]``. Ҳар смета учун битта очиқ навбат
    («Қўлда киритилган»); шу қатор қайта юборилса — кўрилмаган эски ёзуви янгисига алмашади. Админ тасдиқлагач
    ресурс базага тушади ва кейинги сметаларда ўзи нархланади."""
    raw = [{"fi": 0, "sheet": job.local_sheet or "", "row": int(r.get("row") or 0), "name": r.get("name"),
            "unit": r.get("unit"), "price": price,
            "qty": r.get("qty_total") if r.get("qty_total") is not None else r.get("qty"),
            "status": "MANUAL", "src": _job_src(r)} for r, price in picks]
    rows = _clean(raw)
    if not rows:
        return None
    with transaction.atomic():
        it = (Intake.objects.select_for_update()
              .filter(kind=Batch.Kind.MANUAL, work_id=job.pk, status=Intake.Status.OPEN).first())
        if it is None:
            it = Intake.objects.create(
                kind=Batch.Kind.MANUAL, work_id=job.pk, title=f"Смета­чи нархлари: {job.local_name}"[:500],
                owner_id=job.owner_id, created_by=S._uid(user),
                files=[{"job": str(job.id), "file": job.local_name[:200], "sheet": job.local_sheet or ""}])
        it.rows.filter(row__in={r["row"] for r in rows}, state=IntakeRow.State.NEW).delete()
        _build(it, rows)
        Intake.objects.filter(pk=it.pk).update(updated=timezone.now())
    return it


def _read_sources(sources, kind, default_category, tick=None) -> tuple:
    """``sources`` — ``[(кўринадиган номи, .xlsx йўли, MEDIA_ROOT ичидаги нисбий йўли)]`` → ``(files, rows, bad)``.
    Ҳар лист — алоҳида «файл»; бузуқ ёки нархсиз файл бошқаларини тўхтатмайди (``bad``)."""
    files, rows, bad = [], [], []
    for n, (name, xlsx, rel) in enumerate(sources):
        if tick:
            tick(n, name)
        try:
            raw = S.file_rows(xlsx, kind, default_category)
        except Exception as e:  # noqa: BLE001
            bad.append((name, str(e)[:120] or "ўқиб бўлмади"))
            continue
        got = _clean(raw)
        if not got:
            bad.append((name, "нархли ресурс топилмади"))
            continue
        sheets = []
        for r in got:
            if r["sheet"] not in sheets:
                sheets.append(r["sheet"])
        fi = {}
        for sh in sheets:
            fi[sh] = len(files)
            files.append({"job": None, "file": str(name)[:200], "sheet": sh, "path": rel})
        rows += [{**r, "fi": fi[r["sheet"]], "status": "FILE", "src": (r["category"], r["reason"], r["sure"])}
                 for r in got]
    return files, rows, bad


def open_for_files(sources, *, kind, title, user, file_path="", default_category="K") -> tuple:
    """Тайёр .xlsx файллардан дарҳол (орқа фонсиз). ``(intake, ўқилмаганлар)``; ресурс топилмаса — ``ValueError``."""
    files, rows, bad = _read_sources(sources, kind, default_category)
    if not rows:
        raise ValueError("; ".join(f"{n}: {why}" for n, why in bad[:5]) or "no rows")
    with transaction.atomic():
        it = Intake.objects.create(kind=Batch.Kind.FILE, title=title[:500], owner=S._uid(user),
                                   created_by=S._uid(user), file_path=file_path[:300], file_kind=kind, files=files)
        _build(it, rows)
    return it, bad


# ===================== юклаш: орқа фонда ўқиш =====================
STUCK_MINUTES = 15  # шунча вақт ҳеч ўзгармаса — сервер қайта ишга тушган, ўқиш тўхтаб қолган
CONVERT_WORKERS = 4  # .xls ни бир вақтда ўгирадиган LibreOffice'лар


def start_upload(*, items, root, kind, title, user, file_path="", default_category="K", inline=None) -> Intake:
    """Сақланган файллар (``items``: ``[[кўринадиган номи, MEDIA_ROOT ичидаги нисбий йўли, архивми]]``) —
    навбат «ўқилмоқда» ҳолатида очилади, архивни очиш, .xls ни ўгириш ва ўқиш — орқа фонда (``read_upload``).
    Саҳифа ``progress`` ни кўрсатиб туради."""
    from django.conf import settings
    it = Intake.objects.create(kind=Batch.Kind.FILE, title=title[:500], owner=S._uid(user), created_by=S._uid(user),
                               file_path=file_path[:300], file_kind=kind, status=Intake.Status.READING,
                               progress={"stage": "Навбатда", "done": 0, "total": len(items), "src": items,
                                         "root": root, "cat": default_category})
    run(it.pk, inline)
    return it


# Ўқиш оғир (архив, юзлаб файл, катта Excel): ``NARXLASH_TASKS=worker`` да сайт жараёнида эмас — ``run_worker``
# навбатдан олади (lease: ``worker`` + ``heartbeat``, ``jobs.pricing`` каби). thread режимида (ишлаб чиқиш) — оқим.
LEASE_STALE = dt.timedelta(minutes=5)  # ишчи шунча жим бўлса (ўчиб қолган) — бошқаси қайтадан олади
KEEPALIVE_SECONDS = 30


def worker_mode() -> bool:
    from django.conf import settings
    return getattr(settings, "NARXLASH_TASKS", "thread") == "worker"


def _reading():
    return Intake.objects.filter(status=Intake.Status.READING)


def _free():
    return Q(worker="") | Q(heartbeat__isnull=True) | Q(heartbeat__lt=timezone.now() - LEASE_STALE)


def _claim(pk, worker: str) -> bool:
    return bool(_reading().filter(pk=pk).filter(_free()).update(worker=worker, heartbeat=timezone.now()))


def claim_next(worker: str):
    """Навбатдаги ўқишни эгаллайди (атомар UPDATE): аввал юклангани биринчи. Навбат id'си ёки None."""
    for pk in _reading().filter(_free()).order_by("created").values_list("id", flat=True)[:10]:
        if _claim(pk, worker):
            return pk
    return None


def run(pk, inline=None):
    """Ўқишни бошлаш: inline (тестлар) — шу ерда; worker режимида — ҳеч нарса (бўш ишчи ўзи олади);
    thread режимида — сайт жараёнидаги оқим."""
    from django.conf import settings
    if inline if inline is not None else getattr(settings, "NARXBAZA_INLINE", False):
        run_now(pk)
        return
    if worker_mode():
        return
    import threading
    transaction.on_commit(lambda: threading.Thread(target=run_now, args=(pk,), daemon=True,
                                                   name=f"narx-intake-{pk}").start())


def run_now(pk):
    """Шу жараёнда (thread режими, тестлар): эгаллаб, ўқийди."""
    import os
    import threading
    me = f"thread:{os.getpid()}:{threading.get_ident()}"
    if _claim(pk, me):
        read_upload(pk, me)


def requeue(it: Intake):
    """Тўхтаб қолган / хато берган ўқишни қайта навбатга қўяди (сақланган файллардан)."""
    pr = {k: v for k, v in (it.progress or {}).items() if k in ("src", "root", "cat")}
    it.status, it.progress = Intake.Status.READING, {**pr, "stage": "Қайта бошланди", "done": 0}
    it.worker, it.heartbeat = "", None
    it.save(update_fields=["status", "progress", "worker", "heartbeat", "updated"])
    run(it.pk)


def stuck(it: Intake) -> bool:
    """Ўқиш олдинга силжимаяпти: 15 дақиқа ўзгармаган ва ҳеч бир ишчи тирик белги бермаяпти (ишчи ўчган ёки
    сервер қайта ишга тушган). Ишчи ишлаётган бўлса (бир файл узоқ ўгирилса ҳам) — тўхтаган эмас."""
    if it.status != Intake.Status.READING:
        return False
    now = timezone.now()
    if it.heartbeat and now - it.heartbeat < LEASE_STALE:
        return False
    return (now - it.updated).total_seconds() > STUCK_MINUTES * 60


def read_upload(pk, worker: str = "") -> None:
    """Юкланган файлларни ўқийди. ``worker`` — навбатни эгаллаган ишчи: ўқиш давомида тириклик белгиси
    қўйилади, тугагач навбат бўшатилади."""
    import threading
    stop = threading.Event()
    if worker:
        def keepalive():
            from django.db import close_old_connections
            while not stop.wait(KEEPALIVE_SECONDS):
                try:
                    Intake.objects.filter(pk=pk, worker=worker).update(heartbeat=timezone.now())
                except Exception:  # noqa: BLE001 — белги қўйилмаса, кейингисида
                    pass
                finally:
                    close_old_connections()
        threading.Thread(target=keepalive, daemon=True, name=f"narx-intake-keepalive-{pk}").start()
    try:
        _read_upload(pk)
    finally:
        stop.set()
        if worker:
            Intake.objects.filter(pk=pk, worker=worker).update(worker="", heartbeat=None)


def _read_upload(pk) -> None:
    from pathlib import Path, PurePosixPath

    from django.conf import settings
    from django.db import close_old_connections

    from core import xlsfast
    from jobs.archives import ArchiveError, extract_excel
    close_old_connections()
    it = Intake.objects.get(pk=pk)
    pr = dict(it.progress or {})
    media = Path(settings.MEDIA_ROOT)
    root = media / pr["root"]

    def setp(**kw):
        pr.update(kw)
        Intake.objects.filter(pk=pk).update(progress=pr, updated=timezone.now())

    try:
        found = []
        for n, (show, rel, arch) in enumerate(pr.get("src") or []):
            if arch:
                setp(stage=f"Архив очилмоқда: {PurePosixPath(show).name}", done=n, file=show)
                try:
                    got = extract_excel(media / rel, root / "unpack" / str(n), PurePosixPath(show).parent,
                                        name=PurePosixPath(show).name)
                except ArchiveError as e:
                    raise ValueError(f"{PurePosixPath(show).name}: {e}") from e
                found += [(str(r), p) for r, p in got]
            else:
                found.append((show, media / rel))
        if not found:
            raise ValueError("Excel файл топилмади (.xls, .xlsx, .xlsm ёки улар солинган ZIP / RAR / 7z архив).")
        # .xls → .xlsx (LibreOffice, ҳар чақирувда алоҳида профил) — бир неча файл параллел: катта архивда
        # вақтнинг асосий қисми шу
        # (базага фақат шу оқим ёзади — ишчи оқимлар фақат ўгиради)
        import os
        from concurrent.futures import ThreadPoolExecutor, as_completed
        total = len(found)
        setp(stage="Файллар Excel'га тайёрланмоқда", done=0, total=total, file="")

        def conv(n, show, path):
            try:
                # базага фақат қийматлар керак: .xls — xlrd билан (LibreOffice'сиз), бўлмаса — LibreOffice
                x = xlsfast.to_xlsx_values(path, root / "view" / str(n))
                return (show, x, x.relative_to(media).as_posix()), None
            except Exception as e:  # noqa: BLE001
                return None, (show, str(e)[:120] or "очиб бўлмади")

        got = [None] * total
        with ThreadPoolExecutor(max_workers=max(1, min(CONVERT_WORKERS, os.cpu_count() or 1))) as ex:
            futs = {ex.submit(conv, n, show, path): n for n, (show, path) in enumerate(found)}
            for k, f in enumerate(as_completed(futs), 1):
                got[futs[f]] = f.result()
                setp(done=k, file=found[futs[f]][0])
        sources = [s for s, _ in got if s]
        bad = [b for _, b in got if b]
        files, rows, bad2 = _read_sources(sources, it.file_kind, pr.get("cat") or "K",
                                          tick=lambda n, name: setp(stage="Ресурслар ўқилмоқда", done=n,
                                                                    total=len(sources), file=name))
        bad += bad2
        if not rows:
            raise ValueError("Файлларда нархли ресурс топилмади." + (
                " " + "; ".join(f"{n} — {w}" for n, w in bad[:5]) if bad else ""))
        setp(stage=f"Базадаги ресурслар билан солиштирилмоқда ({len(rows)} та қатор)", done=total, total=total, file="")
        with transaction.atomic():
            it = Intake.objects.select_for_update().get(pk=pk)
            if it.status != Intake.Status.READING:  # шу орада бекор қилинган
                return
            _build(it, rows)
            it.files, it.status = files, Intake.Status.OPEN
            # src — асл юкланган файллар: Excel кўриниши йўқолса (масалан, захира нусхадан тикланганда) қайта тузиш учун
            it.progress = {"bad": [list(b) for b in bad], "root": pr.get("root"), "src": pr.get("src") or []}
            it.save(update_fields=["files", "status", "progress", "updated"])
        log.info("narxbaza: юклаш «%s» ўқилди — %s та файл, %s та қатор", it.title, len(files), len(rows))
        try:  # архивлар (ичидаги PDF ва б.), .xls асллари, нархсиз файллар — серверда жой эгалламасин
            from .storage import prune_upload
            prune_upload(it)
        except Exception:  # noqa: BLE001
            log.exception("narxbaza: «%s» — керакмас файлларни ўчириб бўлмади", it.title)
    except Exception as e:  # noqa: BLE001
        log.exception("narxbaza: юклашни ўқиб бўлмади (%s)", pk)
        msg = str(e) if isinstance(e, ValueError) else f"Файлни ўқиб бўлмади: {e}"
        Intake.objects.filter(pk=pk, status=Intake.Status.READING).update(
            status=Intake.Status.FAILED, progress={**pr, "error": msg[:500]}, updated=timezone.now())
    finally:
        close_old_connections()


def restore_view(it: Intake, fi: int):
    """Файлнинг Excel кўриниши (``files[fi]["path"]``) серверда йўқ бўлса — асл юкланган файлдан (архив бўлса —
    ичидан топиб) қайта тузади ва йўлини сақлайди. Топилмаса — ``None``. Масалан, захира нусхадан тиклангандан кейин."""
    from pathlib import Path, PurePosixPath

    from django.conf import settings

    from core import xlsfast
    from jobs.archives import extract_excel, is_archive
    media = Path(settings.MEDIA_ROOT)
    files = list(it.files or [])
    if not 0 <= fi < len(files):
        return None
    want = files[fi]["file"]
    src = [tuple(x) for x in (it.progress or {}).get("src") or [] if len(x) == 3]
    if not src and it.file_path:
        src = [(it.title, it.file_path, is_archive(it.file_path))]
    root = media / ((it.progress or {}).get("root") or PurePosixPath(it.file_path).parent.parent.as_posix())
    for n, (show, rel, arch) in enumerate(src):
        p = media / rel
        if not p.is_file():
            continue
        if arch:
            got = extract_excel(p, root / "unpack_r" / str(n), PurePosixPath(show).parent, name=PurePosixPath(show).name)
            match = [path for r, path in got if str(r) == want]
        else:
            match = [p] if show == want or len(src) == 1 else []
        if match:
            x = xlsfast.to_xlsx_values(match[0], root / "view_r" / f"{fi}")
            files[fi] = {**files[fi], "path": x.relative_to(media).as_posix()}
            Intake.objects.filter(pk=it.pk).update(files=files)
            it.files = files
            log.info("narxbaza: «%s» — Excel кўриниши асл файлдан қайта тузилди (%s)", it.title, want)
            return x
    return None


# ===================== ҳолат =====================
def counts(intake: Intake) -> dict:
    agg = dict(intake.rows.values_list("state").annotate(c=Count("id")))
    known_new = intake.rows.filter(state=IntakeRow.State.NEW, known=True).count()
    warn_new = intake.rows.filter(state=IntakeRow.State.NEW).exclude(warn="").count()
    total = sum(agg.values())
    return {"total": total, "new": agg.get("new", 0), "ok": agg.get("ok", 0), "skip": agg.get("skip", 0),
            "known_new": known_new, "warn_new": warn_new, "done_pct": round(100 * (total - agg.get("new", 0)) / total) if total else 0}


def compare(intake: Intake) -> dict:
    """Базага нисбатан, ҳар файл учун (рад этилмаган қаторлар, ресурс бўйича — ном + бирлик): ``{fi: {new, same,
    changed}}`` ва ``"all"`` — ҳамма файл бўйича. ``new`` — базада йўқ (янги ресурс); ``same`` — базада бор ва нархи
    базадагидек; ``changed`` — базада бор, лекин бошқа нархда (ёзилса базадаги нархи янгиланади). Солиштириш —
    солиштириш вилояти (``cmp_region``; у ерда йўқ бўлса — бошқа вилоят) охирги йил нархи (ўрта, бўлмаса паст) билан."""
    data = {}
    for fi, key, price in intake.rows.exclude(state=IntakeRow.State.SKIP).values_list("fi", "key", "price"):
        data.setdefault(fi, {}).setdefault(key, set()).add(price)
    keys = {k for d in data.values() for k in d}
    items = _item_map(keys)
    ref = ref_stats(list({it.pk for it in items.values()}), cmp_region(intake))

    def kind(key, prices):
        it = items.get(key)
        if it is None:
            return "new"
        st = (ref.get(it.pk) or (None,))[0]
        base = (st.mid if st.mid is not None else st.low) if st else None
        if base is None:
            return "changed"
        return "same" if all(abs(p - base) < Decimal("0.01") for p in prices) else "changed"

    out, every = {}, {}
    for fi, d in data.items():
        c = {"new": 0, "same": 0, "changed": 0}
        for key, prices in d.items():
            c[kind(key, prices)] += 1
            every.setdefault(key, set()).update(prices)
        out[fi] = c
    tot = {"new": 0, "same": 0, "changed": 0}
    for key, prices in every.items():
        tot[kind(key, prices)] += 1
    out["all"] = tot
    return out


def row_json(r: IntakeRow) -> dict:
    return {"id": r.pk, "fi": r.fi, "row": r.row, "name": r.name, "unit": r.unit, "price": S.fmt(r.price),
            "qty": S.fmt(r.qty) if r.qty is not None else "", "cat": r.category, "sug": r.suggested, "reason": r.reason,
            "sure": r.sure, "warn": r.warn, "known": r.known, "state": r.state,
            "item": r.item_id if r.item_id else None, "item_verified": bool(r.item_id and r.item.verified),
            "item_cat": r.item.category if r.item_id else None}


def base_prices(item_ids, region="") -> dict:
    """Текширувда кўрсатиш учун: ``{item_id: {low, mid, high, n, region, year, single, own}}`` — навбат вилояти
    (``region``) нархлари; у ерда йўқ бўлса — бошқа вилоятники (``own=False``, ``ref_stats``)."""
    out = {}
    for iid, (st, own) in ref_stats(item_ids, region).items():
        out[iid] = {"low": S.fmt(st.low), "mid": S.fmt(st.mid) if st.mid is not None else "", "high": S.fmt(st.high),
                    "ref": float(st.mid if st.mid is not None else st.low), "n": st.n, "year": st.year,
                    "region": _region_name(st.region), "single": st.low == st.high, "own": own}
    return out


def rows_json(intake: Intake) -> list:
    """``row_json`` ҳамма қатор учун — модел объектларисиз (минглаб қаторда бир неча баравар тез)."""
    cols = ("pk", "fi", "row", "name", "unit", "price", "qty", "category", "suggested", "reason", "sure", "warn",
            "known", "state", "item_id", "item__verified", "item__category")
    out = []
    for (pk, fi, row, name, unit, price, qty, cat, sug, reason, sure, warn, known, state, iid, iver,
         icat) in intake.rows.order_by(*IntakeRow._meta.ordering or ["pk"]).values_list(*cols):
        out.append({"id": pk, "fi": fi, "row": row, "name": name, "unit": unit, "price": S.fmt(price), "pv": float(price),
                    "qty": S.fmt(qty) if qty is not None else "", "cat": cat, "sug": sug, "reason": reason,
                    "sure": sure, "warn": warn, "known": known, "state": state, "item": iid or None,
                    "item_verified": bool(iid and iver), "item_cat": icat if iid else None})
    return out


def stale(intake: Intake) -> bool:
    """Смета навбат очилгандан кейин қайта нархланганми."""
    if intake.kind not in WORK_KINDS or intake.status != Intake.Status.OPEN:
        return False
    import hashlib
    from django.core.cache import cache
    from jobs.models import Job
    try:
        work = S.work_of(intake.kind, intake.work_id)
    except Exception:  # noqa: BLE001 — иш ўчирилган
        return True
    # сметалар натижаси катта — ҳар очилганда қайта ўқимаслик учун имзо уларнинг версияси (жамиси) бўйича кешда
    flt = {"folder": {"batch__project": work}, "object": {"batch": work}}.get(intake.kind, {"pk": work.pk})
    ver = list(Job.objects.filter(status=Job.Status.DONE, **flt).order_by("id")
               .values_list("id", "sum_total", "sum_not_priced"))
    ck = "narxbaza:sig:" + hashlib.sha1(f"{intake.kind}|{intake.work_id}|{ver}".encode()).hexdigest()
    sig = cache.get(ck)
    if sig is None:
        sig = S.signature(S.rows_from_jobs(S.jobs_of(intake.kind, work)))
        cache.set(ck, sig, 24 * 3600)
    return sig != intake.sig


# ===================== қарорлар =====================
def done_fis(intake: Intake) -> set:
    """Базага ёзилган файллар (``intake.files[i]["done"]``) — уларнинг қаторлари энди ўзгармайди."""
    return {i for i, f in enumerate(intake.files or []) if f.get("done")}


def _live(intake: Intake):
    return intake.rows.exclude(fi__in=done_fis(intake))


@transaction.atomic
def decide(intake: Intake, ids, state=None, category=None, user=None) -> list:
    """Белгиланган қаторлар: ҳолати (ok / skip / new) ва/ёки тоифаси. Тоифа шу ном+бирликли ҳамма қаторга
    ўтади (базага ёзилмаган файлларда). Ўзгарган қаторлар рўйхатини қайтаради."""
    if intake.status != Intake.Status.OPEN:
        raise ValueError("Навбат ёпилган")
    live = _live(intake)
    rows = list(live.filter(pk__in=list(ids)[:5000]))
    if not rows:
        if ids and intake.rows.filter(pk__in=list(ids)[:5000]).exists():
            raise ValueError("Бу файл базага ёзилган — қаторлари ўзгармайди")
        return []
    now, touched = timezone.now(), {r.pk for r in rows}
    if category in CATEGORY_LABELS:
        keys = {r.key for r in rows}
        same = live.filter(key__in=keys)
        same.update(category=category)
        for r in same.select_related("item").filter(item__isnull=False):
            k = bool(r.item.verified and r.item.category == category and not r.warn)
            if k != r.known:
                IntakeRow.objects.filter(pk=r.pk).update(known=k)
        touched |= set(same.values_list("pk", flat=True))
    if state in IntakeRow.State.values:
        IntakeRow.objects.filter(pk__in=[r.pk for r in rows]).update(
            state=state, decided_by=S._uid(user), decided_at=now if state != IntakeRow.State.NEW else None)
    Intake.objects.filter(pk=intake.pk).update(updated=now)
    return [row_json(r) for r in IntakeRow.objects.filter(pk__in=touched).select_related("item")]


def accept_known(intake: Intake, user=None) -> int:
    """«Такрорий»: сиз тасдиқлаган ресурс, тоифаси бир хил, нархида огоҳлантириш йўқ — бирданига тасдиқлаш."""
    qs = _live(intake).filter(state=IntakeRow.State.NEW, known=True)
    return qs.update(state=IntakeRow.State.OK, decided_by=S._uid(user), decided_at=timezone.now())


@transaction.atomic
def commit(intake: Intake, *, region, day: _date, note="", user=None, fis=None) -> Batch:
    """Танланган файллар (``fis`` — ``intake.files`` даги тартиби; ``None`` — базага ёзилмаганларнинг ҳаммаси)
    тасдиқланган қаторларини базага ёзади — битта тўплам. Шу файлларда кўрилмаган қатор қолса — ``ValueError``.
    Ҳамма қатори рад этилган файл тўпламсиз «ёзилган» бўлади (``None`` қайтади). Ҳамма файл ёзилгач — навбат ёпилади."""
    intake = Intake.objects.select_for_update().get(pk=intake.pk)
    if intake.status != Intake.Status.OPEN:
        raise ValueError("Бу навбат аллақачон ёпилган.")
    files = intake.files or []
    done = done_fis(intake)
    fis = sorted({int(i) for i in (range(len(files)) if fis is None else fis) if 0 <= int(i) < len(files)} - done)
    if not fis:
        raise ValueError("Базага ёзиладиган файл танланмаган (ёки танланганлари аллақачон ёзилган).")
    rows = intake.rows.filter(fi__in=fis)
    left = rows.filter(state=IntakeRow.State.NEW)
    if left.exists():
        bad = sorted({files[i]["file"] for i in left.values_list("fi", flat=True).distinct()})
        raise ValueError(f"{left.count()} та қатор ҳали кўрилмаган: " + ", ".join(f"«{x}»" for x in bad[:4]))
    ok = list(rows.filter(state=IntakeRow.State.OK))
    b = None
    if ok:
        cats = {r.key: r.category for r in ok}
        grouped = S.group_rows((r.name, r.unit, r.price, r.qty, r.category, r.src_status or "FILE",
                                {"job": files[r.fi].get("job"), "file": files[r.fi]["file"][:200],
                                 "sheet": r.sheet[:60], "row": r.row}) for r in ok)
        for g in grouped:
            g["category"] = cats[g["key"]]  # «Бошқа» ҳам — админ танлагани
        work = None
        if intake.kind in WORK_KINDS:
            try:
                work = S.work_of(intake.kind, intake.work_id)
            except Exception:  # noqa: BLE001
                work = None
        names = sorted({files[i]["file"] for i in fis})
        title = names[0] if len(names) == 1 and len(files) > 1 else intake.title
        if len(names) > 1 and len(names) < len({f["file"] for f in files}):
            title = f"{intake.title} ({len(names)} та файл)"
        b = S.add_batch(intake.kind, work, grouped, region=region, day=day, user=user, note=note,
                        title=title[:500], file_path=_batch_file(intake, fis))
        if work is None and intake.work_id:
            Batch.objects.filter(pk=b.pk).update(work_id=intake.work_id, owner_id=intake.owner_id)
        now = timezone.now()
        for key, cat in cats.items():
            it = S.owner_of_key(key)
            if it is None:
                continue
            fields = []
            if it.category != cat:
                S._log(Log.Action.CATEGORY, user, item=it, detail=f"{CATEGORY_LABELS[it.category]} → {CATEGORY_LABELS[cat]} "
                       f"(текширувда: «{intake.title[:80]}»)", data={"old": it.category, "new": cat})
                it.category = cat
                fields.append("category")
            if not it.verified:
                it.verified, it.verified_at, it.verified_by = True, now, S._uid(user)
                fields += ["verified", "verified_at", "verified_by"]
            if fields:
                it.save(update_fields=fields + ["updated"])
    for i in fis:
        files[i] = {**files[i], "done": True, "batch": b.pk if b else None, "region": region, "date": day.isoformat()}
    intake.files, intake.region = files, region
    if b is not None:
        intake.batch = b
    if len(done_fis(intake)) == len(files):
        intake.status = Intake.Status.DONE
    intake.save(update_fields=["files", "region", "batch", "status", "updated"])
    log.info("narxbaza: текширув «%s» — %s та файл, %s та қатор базага", intake.title, len(fis), len(ok))
    return b


def _batch_file(intake: Intake, fis) -> str:
    """Тўплам кўрсатадиган файл: битта файлдан бўлса — унинг Excel кўриниши, ҳамма файл бўлса — асл юкланган файл
    (у ўчирилган бўлса — бўш)."""
    from pathlib import Path

    from django.conf import settings
    files = intake.files or []
    if len(fis) == 1 and files[fis[0]].get("path"):
        return files[fis[0]]["path"]
    if len(fis) == len(files) and intake.file_path and (Path(settings.MEDIA_ROOT) / intake.file_path).is_file():
        return intake.file_path
    return ""


def cancel(intake: Intake) -> None:
    if intake.status in (Intake.Status.OPEN, Intake.Status.READING, Intake.Status.FAILED):
        intake.status = Intake.Status.CANCELLED
        intake.save(update_fields=["status", "updated"])


# ===================== базадаги текширилмаганлар =====================
@transaction.atomic
def verify_items(pairs, user=None) -> int:
    """``[(item, тоифа)]`` — админ кўриб тасдиқлади (тоифа ўзгарса — тарихда)."""
    now, n = timezone.now(), 0
    for it, cat in pairs:
        if cat not in CATEGORY_LABELS:
            continue
        if it.category != cat:
            S._log(Log.Action.CATEGORY, user, item=it, detail=f"{CATEGORY_LABELS[it.category]} → {CATEGORY_LABELS[cat]}",
                   data={"old": it.category, "new": cat})
            it.category = cat
        it.verified, it.verified_at, it.verified_by, it.edited = True, now, S._uid(user), True
        it.save(update_fields=["category", "verified", "verified_at", "verified_by", "edited", "updated"])
        n += 1
    return n
