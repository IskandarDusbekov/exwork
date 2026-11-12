"""Янги нарх базаси: смета­чилар ресурсларидан тасдиқлаб йиғиш, қўлда киритиш ва паст / ўрта / юқори нарх.

Қандай ҳисобланади (ресурс × вилоят × йил, истисно қилинмаганлар):
* учала нарх ҳар доим ҳақиқатда келган нархлардан бири — ҳеч қачон ўртачаси ҳисобланмайди;
* ҳамма нарх бир хил — битта нарх (паст = ўрта = юқори);
* 2 та нарх — паст ва юқори; ўртаси — сметаларда кўп учрагани, тенг бўлса арзони;
* 3 та ва кўп — арзонидан қимматига саралаб, 25%, 50% ва 75% ўриндаги нарх («яқин ранг»): 3 тада — энг арзони,
  ўртадагиси, энг қиммати; 25 тада — 7-, 13- ва 19-нарх. Янги нарх келса учаласи қайта танланади;
* 3 тадан кўп нарх бўлса, ўртадагидан 3 баробар қиммат ёки арзон нарх — «четда»: паспортда кўринади, ҳисобга кирмайди.
Нарх — бирлик нархи (1 шт, 1 м3 …); сони фақат маълумот учун."""
import hashlib
import logging
import re
from collections import OrderedDict
from datetime import date as _date
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from statistics import median

from django.db import transaction

from core.normalize import fold_name, name_unit_key, norm_name, unit_key
from jobs.models import Batch as JobBatch, Job, Project
from resbase import services as old

from .models import CATEGORY_LABELS, Batch, Item, Price, Stat

log = logging.getLogger(__name__)

OUTLIER = Decimal(3)
SURE = {"EXACT", "MANUAL", "BASE", "FILE"}  # RES билан аниқ мос ёки смета­чи қўйган — сукут бўйича белгиланади
STATUS_LABELS = {"FILE": "файлдан", "EXACT": "RES билан аниқ", "MANUAL": "смета­чи қўлда", "BASE": "базадан", "FUZZY": "ўхшаш (тахминий)",
                 "AMBIGUOUS": "бир нечта ўхшаш", "NOT_FOUND": "топилмаган"}


def item_key(name, unit) -> str:
    """Номи + бирлиги: катта-кичик ҳарф, бўшлиқ, лотин/кирилл ўхшаш ҳарфлар, «ЧЕЛ.-Ч» = «ЧЕЛ-ЧАС» фарқ қилмайди."""
    return name_unit_key(name, unit)


def category_of(c) -> str:
    c = (c or "")[:1]
    return c if c in CATEGORY_LABELS and c != "O" else "O"


def _dec(v):
    try:
        d = Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d if d.is_finite() else None


# ===================== смета­чи ишлари =====================
def work_of(kind, work_id):
    return old.work_of(kind, work_id)


def jobs_of(kind, work) -> list:
    return old.jobs_of(kind, work)


def title_of(kind, work) -> str:
    return old.title_of(kind, work)


def added_works() -> set:
    return {(k, str(w)) for k, w in Batch.objects.exclude(kind=Batch.Kind.MANUAL).values_list("kind", "work_id")}


def pending_works() -> list:
    """Нархланган, лекин ҳали янги базага киритилмаган ишлар: [(kind, obj)]."""
    added, out = added_works(), []
    for p in Project.objects.filter(parts__jobs__status=Job.Status.DONE).distinct().select_related("owner"):
        if ("folder", str(p.id)) not in added:
            out.append(("folder", p))
    for b in JobBatch.objects.filter(project__isnull=True, jobs__status=Job.Status.DONE).distinct().select_related("owner"):
        if ("object", str(b.id)) not in added:
            out.append(("object", b))
    for j in Job.objects.filter(batch__isnull=True, status=Job.Status.DONE).select_related("owner").defer("result"):
        if ("smeta", str(j.id)) not in added:
            out.append(("smeta", j))
    out.sort(key=lambda t: t[1].created, reverse=True)
    return out


def _job_items(jobs):
    """Нархланган смета қаторлари: (name, unit, price, qty, category, status, place)."""
    for job in jobs:
        for r in (job.result or {}).get("rows") or []:
            status = r.get("status") or ""
            if status == "WORK_HEADER":
                continue
            if r.get("manual"):
                status = "MANUAL"
            yield (r.get("name"), r.get("unit"), r.get("g"),
                   r.get("qty_total") if r.get("qty_total") is not None else r.get("qty"),
                   r.get("category") or r.get("default_category"), status,
                   {"job": str(job.id), "file": job.local_name[:200], "row": int(r.get("row") or 0)})


def group_rows(items) -> list:
    """Тасдиқлаш учун қаторлар: бир хил ресурс + бир хил нарх битта қатор (сони қўшилади, қаерда учрагани — рўйхат).

    Ҳар қатор: {i, key, name, unit, category, price, qty, n, status, sure, places}. Тартиби ўзгармас —
    тасдиқлашда белгиланганлар рақами (``i``) бўйича олинади."""
    groups = OrderedDict()
    for name, unit, price, qty, cat, status, place in items:
        name, unit = str(name or "").strip(), str(unit or "").strip()
        price = _dec(price)
        if not name or not unit or price is None or price <= 0:
            continue
        price = price.quantize(Decimal("0.0001"))
        key = item_key(name, unit)
        g = groups.get((key, price))
        if g is None:
            g = groups[(key, price)] = {"key": key, "name": name[:500], "unit": unit[:40], "price": price,
                                        "category": category_of(cat), "qty": Decimal(0), "n": 0, "status": status,
                                        "places": []}
        q = _dec(qty)
        if q is not None and q > 0:
            g["qty"] += q
        g["n"] += 1
        if status in SURE and g["status"] not in SURE:
            g["status"] = status
        same = [p for p in g["places"] if p.get("job") == place.get("job") and p.get("file") == place.get("file")]
        if len(g["places"]) < 20 and not same:
            g["places"].append(place)
    out = []
    for i, g in enumerate(groups.values()):
        g["i"] = i
        g["sure"] = g["status"] in SURE
        g["status_label"] = STATUS_LABELS.get(g["status"], g["status"] or "—")
        out.append(g)
    return out


def rows_from_jobs(jobs) -> list:
    return group_rows(_job_items(jobs))


# ===================== файлдан =====================
FILE_KINDS = [("excel", "Оддий Excel: номи, бирлиги, нархи устунлари (сони, тоифаси — ихтиёрий)"),
              ("res", "RES файл: тоифа бўлимлари бўйича (иш ҳақи, машина, материал, ускуна, кабель)")]
_H = {"name": re.compile(r"наименован|^номи|ресурс\s*номи|^название|^ресурс$", re.I),
      "unit": re.compile(r"^(ед|бирлиг|изм|ўлчов|олчов)", re.I),
      "price": re.compile(r"цена|нарх|стоимость\s*ед|^price", re.I),
      "qty": re.compile(r"^(кол|сони|миқдор|микдор|qty)", re.I),
      "cat": re.compile(r"^(тоифа|тури|тип|категор)", re.I)}
_CAT_WORDS = [("I", r"иш\s*ҳақ|иш\s*хак|труд|мехнат|меҳнат"), ("J", r"машин|механизм"), ("M", r"ускуна|оборуд"),
              ("L", r"кабел"), ("K", r"материал")]


def _cat_word(v, default):
    t = str(v or "").strip()
    if len(t) == 1 and t.upper() in CATEGORY_LABELS:
        return t.upper()
    for k, rx in _CAT_WORDS:
        if re.search(rx, t, re.I):
            return k
    return default


def _header(row):
    cols = {}
    for i, v in enumerate(row):
        t = str(v or "").strip()
        if not t or len(t) > 60:
            continue
        for k, rx in _H.items():
            if k not in cols and rx.search(t):
                cols[k] = i
                break
    return cols if {"name", "unit", "price"} <= cols.keys() else None


def _excel_items(path, file_name, default_category, soffice=None):
    import tempfile
    from pathlib import Path
    from openpyxl import load_workbook
    from core import convert
    with tempfile.TemporaryDirectory(prefix="narx_xl_") as tmp:
        xlsx = convert.ensure_xlsx(Path(path), Path(tmp), soffice=soffice)
        wb = load_workbook(xlsx, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                head = None
                for n, row in enumerate(ws.iter_rows(values_only=True), start=1):
                    row = list(row)
                    if head is None:
                        if n <= 40:
                            head = _header(row)
                        continue

                    def cell(k):
                        return row[head[k]] if k in head and head[k] < len(row) else None
                    yield (cell("name"), cell("unit"), cell("price"), cell("qty"),
                           _cat_word(cell("cat"), default_category), "FILE",
                           {"file": file_name[:200], "sheet": ws.title[:60], "row": n})
        finally:
            wb.close()


def parse_file(path, kind, file_name, default_category="K", soffice=None) -> list:
    """Юкланган файлдан қаторлар (``group_rows`` натижаси). ``kind``: "res" ёки "excel"."""
    if kind == "res":
        from pathlib import Path
        from resbase.resfile import parse
        rf = parse(Path(path), soffice=soffice)
        return group_rows((r.name, r.unit, r.price, r.qty, r.category, "FILE",
                           {"file": file_name[:200], "sheet": r.sheet[:60], "row": r.row}) for r in rf.rows)
    return group_rows(list(_excel_items(path, file_name, category_of(default_category), soffice)))


def signature(rows) -> str:
    """Тасдиқлаш саҳифаси очилгандан кейин смета қайта нархланган бўлса — рақамлар бошқа қаторга тушмасин."""
    h = hashlib.sha1()
    for r in rows:
        h.update(f"{r['key']}|{r['price']};".encode())
    return h.hexdigest()[:16]


# ===================== киритиш =====================
def _item_for(key, name, unit, category, cache):
    it = cache.get(key)
    if it is None:
        it, created = Item.objects.get_or_create(key=key, defaults={"name": name, "unit": unit, "category": category})
        if not created and not it.edited and it.category == "O" and category != "O":
            it.category = category
            it.save(update_fields=["category", "updated"])
        it.is_new = created
        cache[key] = it
    return it


@transaction.atomic
def add_batch(kind, work, rows, *, region, day: _date, user=None, note="", title="", file_path="") -> Batch:
    """Тасдиқланган қаторларни базага ёзади (бир тўплам), кейин ўша ресурсларнинг нархларини қайта ҳисоблайди.
    ``work`` — смета­чи иши (папка/объект/смета) ёки ``None`` (юкланган файл: ``title`` — файл номи)."""
    owner = getattr(work, "owner_id", None) if work is not None else getattr(user, "pk", None)
    b = Batch.objects.create(kind=kind, work_id=getattr(work, "pk", None), title=(title or title_of(kind, work))[:500],
                             file_path=file_path[:300], owner_id=owner, added_by=user, region=region, date=day, note=note)
    cache, objs = {}, []
    for r in rows:
        it = _item_for(r["key"], r["name"], r["unit"], r["category"], cache)
        objs.append(Price(item=it, batch=b, price=r["price"], qty=r["qty"] or None, region=region, date=day,
                          year=day.year, name=r["name"], unit=r["unit"], places=r["places"], seen=max(1, r.get("n") or 1)))
    Price.objects.bulk_create(objs, batch_size=1000)
    b.stats = {"prices": len(objs), "items": len(cache), "new": sum(1 for i in cache.values() if i.is_new)}
    b.save(update_fields=["stats"])
    recalc({(i.pk, region, day.year) for i in cache.values()})
    log.info("narxbaza: «%s» — %s ta narx (%s, %s)", b.title, len(objs), region, day)
    return b


@transaction.atomic
def add_manual(*, name, unit, category, price, region, day: _date, qty=None, note="", user=None, item=None) -> Price:
    """Қўлда битта нарх: мавжуд ресурсга ёки янги ресурс (номи + бирлиги)."""
    if item is None:
        item = _item_for(item_key(name, unit), name.strip()[:500], unit.strip()[:40], category_of(category), {})
    b = Batch.objects.create(kind=Batch.Kind.MANUAL, title=f"Қўлда: {item.name}"[:500], owner=user, added_by=user,
                             region=region, date=day, note=note[:300], stats={"prices": 1, "items": 1})
    p = Price.objects.create(item=item, batch=b, price=Decimal(price).quantize(Decimal("0.0001")), qty=qty, region=region,
                             date=day, year=day.year, name=item.name, unit=item.unit, note=note[:300])
    recalc({(item.pk, region, day.year)})
    return p


def set_excluded(price: Price, excluded: bool, note=""):
    price.excluded = excluded
    if note:
        price.note = note[:300]
    price.save(update_fields=["excluded", "note"])
    recalc({(price.item_id, price.region, price.year)})


@transaction.atomic
def delete_batch(b: Batch):
    touched = set(b.prices.values_list("item_id", "region", "year"))
    b.delete()
    recalc(touched)
    Item.objects.filter(prices__isnull=True).delete()


# ===================== паст / ўрта / юқори =====================
def three(prices, seen=None) -> tuple:
    """(паст, ўрта, юқори, четдагилар тўплами) — модуль бошидаги қоида бўйича. ``seen`` — ҳар нарх неча марта
    учрагани (``prices`` билан бир тартибда): иккита нархда кўп учрагани ўрта бўлади."""
    weight = {}
    for i, p in enumerate(prices):
        weight[p] = weight.get(p, 0) + (seen[i] if seen else 1)
    ps = sorted(prices)
    if not ps:
        return None, None, None, set()
    out = set()
    if len(ps) > 3:
        med = median(ps)
        out = {p for p in ps if p > med * OUTLIER or p * OUTLIER < med}
        ps = [p for p in ps if p not in out] or ps
    n = len(ps)
    if n == 1 or ps[0] == ps[-1]:
        return ps[0], ps[0], ps[0], out
    if n == 2:  # иккита нарх — ўртачаси эмас: кўп учрагани, тенг бўлса арзони
        return ps[0], (ps[1] if weight[ps[1]] > weight[ps[0]] else ps[0]), ps[1], out

    def rank(p):  # «яқин ранг» усули: ҳар доим ҳақиқатда келган нархлардан бири
        return ps[max(1, -(-n * p // 1)) - 1]
    return rank(Fraction(1, 4)), rank(Fraction(1, 2)), rank(Fraction(3, 4)), out


def _money(v) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"))


def recalc(keys):
    """keys — {(item_id, region, year)}: шу гуруҳларнинг нархи қайта ҳисобланади, «четда» белгиси янгиланади."""
    for item_id, region, year in keys:
        qs = Price.objects.filter(item_id=item_id, region=region, year=year)
        rows = list(qs.filter(excluded=False).values_list("id", "price", "date", "seen"))
        live = [(i, p, d) for i, p, d, _ in rows]
        low, mid, high, out = three([p for _, p, _ in live], [s for *_, s in rows])
        qs.exclude(outlier=False).update(outlier=False)
        if out:
            qs.filter(id__in=[i for i, p, _ in live if p in out]).update(outlier=True)
        if low is None:
            Stat.objects.filter(item_id=item_id, region=region, year=year).delete()
            continue
        Stat.objects.update_or_create(item_id=item_id, region=region, year=year, defaults={
            "low": _money(low), "mid": _money(mid) if mid is not None else None, "high": _money(high), "n": len(live) - sum(1 for _, p, _ in live if p in out),
            "last_date": max(d for _, _, d in live)})


def recalc_all():
    recalc(set(Price.objects.values_list("item_id", "region", "year").distinct()))


# ===================== кўриш =====================
def best_stat(item_ids, region="", year=None) -> dict:
    """{item_id: Stat} — танланган вилоят/йил; танланмаса — энг охирги йил, кўп нархлиси."""
    qs = Stat.objects.filter(item_id__in=item_ids)
    if region:
        qs = qs.filter(region=region)
    if year:
        qs = qs.filter(year=year)
    out = {}
    for s in qs.order_by("item_id", "-year", "-n"):
        out.setdefault(s.item_id, s)
    return out


def counts() -> dict:
    return {"items": Item.objects.count(), "prices": Price.objects.count(),
            "batches": Batch.objects.count(), "excluded": Price.objects.filter(excluded=True).count(),
            "outliers": Price.objects.filter(outlier=True, excluded=False).count()}


# ===================== кўрсаткичлар =====================
def category_stats() -> list:
    """[(тоифа, номи, ресурслар, нархлар)]."""
    from django.db.models import Count
    items = dict(Item.objects.values_list("category").annotate(c=Count("id")))
    prices = dict(Price.objects.values_list("item__category").annotate(c=Count("id")))
    return [(k, label, items.get(k, 0), prices.get(k, 0)) for k, label in CATEGORY_LABELS.items()]


COVER_JOBS = 60


def coverage(limit=COVER_JOBS) -> dict:
    """База охирги нархланган сметаларни тахминан неча фоиз нархлай олади (номи + бирлиги бўйича, вилоят ва йилдан
    қатъи назар). Сметанинг ўзидан киритилган нархлар ҳисобланмайди — бўлмаса ўз нархи билан «қопланиб» қолади."""
    from django.core.cache import cache
    last = Price.objects.order_by("-id").values_list("id", flat=True).first() or 0
    njobs = Job.objects.filter(status=Job.Status.DONE).count()
    ck = f"narxbaza:cover3:{last}:{Price.objects.count()}:{njobs}:{limit}"
    got = cache.get(ck)
    if got is not None:
        return got
    # ресурс → қайси тўпламлардан нархи бор; смета → у кирган тўпламлар (ўз папкасининг нархлари ҳисобланмайди)
    src, job_batches = {}, {}
    for key, bid in Price.objects.filter(excluded=False).values_list("item__key", "batch_id"):
        src.setdefault(key, set()).add(bid)
    for b in Batch.objects.exclude(kind__in=(Batch.Kind.MANUAL, Batch.Kind.FILE)).exclude(work_id=None):
        jq = {"folder": {"batch__project_id": b.work_id}, "object": {"batch_id": b.work_id}, "smeta": {"pk": b.work_id}}[b.kind]
        for jid in Job.objects.filter(**jq).values_list("id", flat=True):
            job_batches.setdefault(str(jid), set()).add(b.pk)
    total = covered = 0
    by_cat, per_job = {k: [0, 0] for k in CATEGORY_LABELS}, []
    jobs = Job.objects.filter(status=Job.Status.DONE).order_by("-created").only("id", "result")[:limit]
    for job in jobs:
        mine, t, c = job_batches.get(str(job.id), set()), 0, 0
        for r in (job.result or {}).get("rows") or []:
            name, unit = str(r.get("name") or "").strip(), str(r.get("unit") or "").strip()
            if r.get("status") == "WORK_HEADER" or not name or not unit:
                continue
            cat = category_of(r.get("category") or r.get("default_category"))
            ok = bool(src.get(item_key(name, unit), set()) - mine)
            t += 1
            c += ok
            by_cat[cat][0] += 1
            by_cat[cat][1] += ok
        if t:
            total += t
            covered += c
            per_job.append(c / t)
    pct = lambda c, t: round(100 * c / t) if t else 0  # noqa: E731
    out = {"jobs": len(per_job), "rows": total, "covered": covered, "pct": pct(covered, total),
           "avg": round(100 * sum(per_job) / len(per_job)) if per_job else 0,
           "by_cat": [(k, CATEGORY_LABELS[k], t, c, pct(c, t)) for k, (t, c) in by_cat.items() if t]}
    cache.set(ck, out, 900)
    return out


@transaction.atomic
def delete_item(item: Item):
    """Ресурсни ва ҳамма нархларини ўчиради; бўшаб қолган қўлда киритилган тўпламлар ҳам кетади."""
    batches = set(item.prices.values_list("batch_id", flat=True))
    item.delete()
    Batch.objects.filter(pk__in=batches, kind=Batch.Kind.MANUAL, prices__isnull=True).delete()


# ===================== нархлаш учун =====================
def enabled() -> bool:
    """Базадан нархлаш ёқилганми (панелда ёқилади / ўчирилади). Эски «Ресурс базаси» ҳеч қачон ишлатилмайди."""
    from .models import Config
    return Config.get().enabled


def level_of(period) -> str:
    from .models import Config, LEVEL_LABELS
    lv = (period or {}).get("level")
    return lv if lv in LEVEL_LABELS else Config.get().level


def price_map(period) -> dict:
    """Нархлаш учун (``core.pipeline.Options.base_prices``): {номи|бирлиги калити: {price, n, unit_key, unit,
    category, note}} — танланган вилоят ва йилнинг паст / ўрта / юқори нархи (смета­чи танлаган даражаси)."""
    from .models import LEVEL_LABELS
    if not (period and period.get("region") and period.get("year")):
        return {}
    lv = level_of(period)
    word = LEVEL_LABELS[lv].split()[0].lower()
    out = {}
    for s in Stat.objects.filter(region=period["region"], year=int(period["year"])).select_related("item"):
        price = getattr(s, lv)
        if price is None:
            price = s.mid if s.mid is not None else s.low
        it = s.item
        out[it.key] = {"price": float(price), "n": s.n, "unit_key": it.key.rsplit("|", 1)[-1], "unit": it.unit,
                       "category": it.category if it.category in ("I", "J", "K", "L", "M") else "",
                       "note": f"{word} нарх, {s.n} та нархдан"}
    return out
