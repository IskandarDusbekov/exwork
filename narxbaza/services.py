"""Янги нарх базаси: смета­чилар ресурсларидан тасдиқлаб йиғиш, қўлда киритиш ва паст / ўрта / юқори нарх.

Қандай ҳисобланади (ресурс × вилоят × йил, ҳисобдан чиқарилмаганлар):
* учала нарх ҳар доим ҳақиқатда келган нархлардан бири — ҳеч қачон ўртачаси ҳисобланмайди;
* 0,1% гача фарқли нархлар (36 030,00 / 36 030,10) — бир нарх: энг кўп учрагани олинади;
* ҳар нархнинг вазни — неча файлда учрагани (бир файл икки тўпламда бўлса ҳам бир марта);
* паст — энг арзон ҳақиқий нарх, юқори — энг қиммат ҳақиқий нарх;
* ўрта — файлларнинг ярми шу нархда ёки арзонроқда (вазнли медиана): кўп файлда учраган нарх ўрта бўлади;
* 3 тадан кўп нарх бўлса, ўртадан 3 баробар қиммат ёки арзон нарх — «четда»: паспортда кўринади, ҳисобга кирмайди.
Нарх — бирлик нархи (1 шт, 1 м3 …); сони фақат маълумот учун."""
import hashlib
import logging
import re
from collections import OrderedDict
from datetime import date as _date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from core.normalize import fold_name, name_core, name_unit_key, norm_name, unit_key
from jobs.models import Batch as JobBatch, Job, Project
from resbase import services as old

from .models import CATEGORY_LABELS, Alias, Batch, Item, Log, Price, Stat

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
_H = {"name": re.compile(r"наименован|^номи|ресурс\s*номи|^название|^ресурс$|иш\s*ва\s*ресурс|ресурслар\s*номи", re.I),
      "unit": re.compile(r"^(ед|бирл|изм|ўлчов|олчов|ўл\.|ўлч)", re.I),
      "price": re.compile(r"цена|нарх|стоимость\s*ед|^price|^баҳо|^бахо", re.I),
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


def _unit_cat(name, unit):
    """Бирлиги / номидан аниқ белги: чел.-ч — иш ҳақи, маш.-ч — машина. Бўлмаса None."""
    from core.classify import MACHINES, WORKERS, row_kind
    k = row_kind(name, unit)
    if k == WORKERS:
        return "I", "бирлиги «чел.-ч» — иш ҳақи"
    if k == MACHINES:
        return "J", "бирлиги «маш.-ч» — машина-механизм"
    return None


def _smeta_sheet(ws):
    """Нархли локал смета листи (смета­лар ўқиладиган усулда): ``(қатор, номи, бирлиги, нархи, сони)``.
    Ish sarlavhalari (остида ресурслари бор иш) ва нархсиз қаторлар ўтказиб юборилади. Смета эмас — бўш."""
    from core.columns import detect_local_columns, num
    from core.detect import cell_value
    from core.pipeline import _work_headers
    from core.tender import money_columns
    try:
        cols = detect_local_columns(ws)
    except Exception:  # noqa: BLE001 — смета кўринишида эмас
        return []
    if cols is None:
        return []
    price_col, _amount = money_columns(ws, cols)
    if price_col is None:
        return []
    heads = _work_headers(ws, cols.data_start, cols.data_stop, cols.name, cols.unit)
    out = []
    for r in range(cols.data_start, cols.data_stop):
        if r in heads:
            continue
        name, unit = cell_value(ws, r, cols.name), cell_value(ws, r, cols.unit)
        price = num(cell_value(ws, r, price_col))
        if not name or not unit or not price or price <= 0 or num(name) is not None:
            continue
        out.append((r, name, unit, price, cell_value(ws, r, cols.qty_total) if cols.qty_total else None))
    return out


def file_rows(xlsx, kind, default_category="K") -> list:
    """Юкланган файлнинг ҳар ресурс қатори (гуруҳланмаган): ``{sheet, row, name, unit, price, qty, category,
    reason, sure}``. ``kind``: "res" — RES файл (тоифа — бўлими), "excel" — нархли локал смета ёки оддий жадвал
    (номи / бирлиги / нархи устунлари)."""
    from pathlib import Path
    default_category = category_of(default_category) if default_category != "O" else "O"
    out = []
    if kind == "res":
        from resbase.resfile import parse
        for r in parse(Path(xlsx), dedupe=False).rows:  # такрор қаторлар ҳам — ҳар бири Excel'да белгиланади
            out.append({"sheet": r.sheet[:100], "row": r.row, "name": r.name, "unit": r.unit, "price": r.price,
                        "qty": r.qty, "category": category_of(r.category),
                        "reason": f"RES файлда «{CATEGORY_LABELS.get(category_of(r.category), '')}» бўлимида",
                        "sure": category_of(r.category) != "O"})
        return out
    from openpyxl import load_workbook
    wb = load_workbook(xlsx, data_only=True)
    try:
        for ws in wb.worksheets:
            if ws.sheet_state != "visible" or ws.title.upper().startswith("LOG"):
                continue  # LOG — нархлаш журнали (дастур ёзган), смета эмас
            # «тоифа» устуни бор оддий жадвал — сарлавҳа бўйича; бўлмаса аввал нархли смета деб ўқилади
            has_cat = any("cat" in (_header(list(r)) or {}) for r in ws.iter_rows(max_row=40, values_only=True))
            got = [] if has_cat else _smeta_sheet(ws)
            if got:
                for r, name, unit, price, qty in got:
                    out.append({"sheet": ws.title[:100], "row": r, "name": name, "unit": unit, "price": price,
                                "qty": qty, "category": None, "reason": "", "sure": False})
                continue
            head = None
            for n, row in enumerate(ws.iter_rows(values_only=True), start=1):
                row = list(row)
                if head is None:
                    if n <= 40:
                        head = _header(row)
                    continue

                def cell(k):
                    return row[head[k]] if k in head and head[k] < len(row) else None
                cat = _cat_word(cell("cat"), None) if "cat" in head else None
                out.append({"sheet": ws.title[:100], "row": n, "name": cell("name"), "unit": cell("unit"),
                            "price": cell("price"), "qty": cell("qty"), "category": cat,
                            "reason": "файлдаги «тоифа» устунида" if cat else "", "sure": bool(cat)})
    finally:
        wb.close()
    for r in out:
        if r["category"] is None:
            u = _unit_cat(r["name"], r["unit"])
            r["category"], r["reason"], r["sure"] = (u[0], u[1], True) if u else (default_category, "", False)
    return out


def parse_file(path, kind, file_name, default_category="K", soffice=None) -> list:
    """Юкланган файлдан қаторлар (``group_rows`` натижаси). ``kind``: "res" ёки "excel"."""
    import tempfile
    from pathlib import Path
    from core import convert
    with tempfile.TemporaryDirectory(prefix="narx_xl_") as tmp:
        xlsx = convert.ensure_xlsx(Path(path), Path(tmp), soffice=soffice) if kind == "excel" else Path(path)
        raw = file_rows(xlsx, kind, default_category)
    return group_rows((r["name"], r["unit"], r["price"], r["qty"], r["category"], "FILE",
                       {"file": file_name[:200], "sheet": r["sheet"][:60], "row": r["row"]}) for r in raw)


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
        al = Alias.objects.filter(key=key, item__deleted_at=None).select_related("item").first()
        if al is not None:  # бирлаштирилган / эски номи — асосий ресурсга
            it = cache[key] = al.item
            it.is_new = False
            return it
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
    # базада бор ресурслар: шу вилоят/йилдаги нархи (паст/ўрта/юқори) ёзишдан олдин ва кейин — «янгиланди» ёки «ўзгармади»
    old_ids = [i.pk for i in cache.values() if not i.is_new]
    before = _stat_map(old_ids, region, day.year)
    recalc({(i.pk, region, day.year) for i in cache.values()})
    after = _stat_map(old_ids, region, day.year)
    changed = sum(1 for k in old_ids if before.get(k) != after.get(k))
    b.stats = {"prices": len(objs), "items": len(cache), "new": len(cache) - len(old_ids), "existing": len(old_ids),
               "changed": changed, "same": len(old_ids) - changed}
    b.save(update_fields=["stats"])
    log.info("narxbaza: «%s» — %s ta narx (%s, %s)", b.title, len(objs), region, day)
    return b


def _stat_map(item_ids, region, year) -> dict:
    """{item_id: (паст, ўрта, юқори)} — шу вилоят ва йилда."""
    out = {}
    for i in range(0, len(item_ids), 900):
        for iid, low, mid, high in Stat.objects.filter(item_id__in=item_ids[i:i + 900], region=region, year=year)                 .values_list("item_id", "low", "mid", "high"):
            out[iid] = (low, mid, high)
    return out


@transaction.atomic
def add_manual(*, name, unit, category, price, region, day: _date, qty=None, note="", user=None, item=None) -> Price:
    """Қўлда битта нарх: мавжуд ресурсга ёки янги ресурс (номи + бирлиги)."""
    if item is None:
        item = _item_for(item_key(name, unit), name.strip()[:500], unit.strip()[:40], category_of(category), {})
        if item.is_new and not item.verified:  # админ ўзи номи ва тоифасини ёзди
            item.verified, item.verified_at, item.verified_by = True, timezone.now(), _uid(user)
            item.save(update_fields=["verified", "verified_at", "verified_by"])
    b = Batch.objects.create(kind=Batch.Kind.MANUAL, title=f"Қўлда: {item.name}"[:500], owner=user, added_by=user,
                             region=region, date=day, note=note[:300], stats={"prices": 1, "items": 1})
    p = Price.objects.create(item=item, batch=b, price=Decimal(price).quantize(Decimal("0.0001")), qty=qty, region=region,
                             date=day, year=day.year, name=item.name, unit=item.unit, note=note[:300])
    recalc({(item.pk, region, day.year)})
    return p


def set_excluded(price: Price, excluded: bool, note="", user=None):
    if price.excluded == excluded:
        return
    price.excluded = excluded
    if note:
        price.note = note[:300]
    price.save(update_fields=["excluded", "note"])
    recalc({(price.item_id, price.region, price.year)})
    _log(Log.Action.EXCLUDE if excluded else Log.Action.INCLUDE, user, item=price.item, price=price,
         detail=f"{fmt(price.price)} сўм, {price.get_region_display()}, {price.date:%d.%m.%Y}" + (f" — {note}" if note else ""))


PIN_LABELS = {"low": "паст", "mid": "ўрта", "high": "юқори"}


def set_pin(price: Price, pin: str, user=None):
    """Нархни шу вилоят/йилнинг паст / ўрта / юқори нархи қилиб белгилаш (``""`` — белгини олиш). Айнан шу қийматли
    нархларнинг (``twins``) ҳаммасига қўйилади; белгиланган нарх ҳисобга қайтади; шу гуруҳдаги шу белгили бошқа
    қийматлардан белги олинади."""
    pin = pin if pin in PIN_LABELS else ""
    if price.pin == pin and not (pin and price.excluded):
        return
    with transaction.atomic():
        group = list(twins(price).values_list("pk", flat=True)) or [price.pk]
        if pin:
            Price.objects.filter(item_id=price.item_id, region=price.region, year=price.year, pin=pin).exclude(
                pk__in=group).update(pin="")
        old = price.pin
        Price.objects.filter(pk__in=group).update(pin=pin, **({"excluded": False} if pin else {}))
        price.pin, price.excluded = pin, (False if pin else price.excluded)
        recalc({(price.item_id, price.region, price.year)})
        what = f"{PIN_LABELS[pin]} нарх қилиб белгиланди" if pin else f"«{PIN_LABELS.get(old, old)}» белгиси олинди"
        _log(Log.Action.PIN, user, item=price.item, price=price, data={"old": old, "new": pin, "n": len(group)},
             detail=f"{fmt(price.price)} сўм, {price.get_region_display()}, {price.date:%d.%m.%Y} — {what}")


def twins(price: Price):
    """Шу ресурснинг шу вилоят/йилдаги айнан шу қийматли ва ҳолатли нархлари (ўзи ҳам) — ҳисобда бир хил,
    паспортда битта бўлиб кўринади ва амаллар ҳаммасига бирдан қўлланади."""
    return Price.objects.filter(item_id=price.item_id, region=price.region, year=price.year, price=price.price,
                                excluded=price.excluded, outlier=price.outlier, pin=price.pin)


def counted(price: Price) -> bool:
    """Нарх паст / ўрта / юқорини ҳисоблашда ишлатиладими."""
    return bool(price.pin) or not (price.excluded or price.outlier)


def delete_prices(prices, user=None) -> int:
    """Нархларни бутунлай ўчириш (тарихда ёзуви қолади), кейин шу гуруҳлар қайта ҳисобланади.
    Тўплами бўшаб қолса — тўплам ҳам ўчади (қўлда киритилганлар учун)."""
    prices = list(prices)
    if not prices:
        return 0
    with transaction.atomic():
        keys = {(p.item_id, p.region, p.year) for p in prices}
        batches = {p.batch_id for p in prices}
        items = {i.pk: i for i in Item.all_objects.filter(pk__in={p.item_id for p in prices})}
        for p in prices:
            it = items.get(p.item_id)
            Log.objects.create(action=Log.Action.PRICE_DEL, item=it, batch_id=p.batch_id, user=_uid(user),
                               title=(it.name if it else p.name)[:500],
                               detail=f"{fmt(p.price)} сўм, {p.get_region_display()}, {p.date:%d.%m.%Y}"
                                      + (" (четда)" if p.outlier else " (ҳисобдан чиқарилган)" if p.excluded else ""),
                               data={"price": str(p.price), "region": p.region, "date": p.date.isoformat(), "name": p.name})
        Price.all_objects.filter(pk__in=[p.pk for p in prices]).delete()
        recalc(keys)
        Batch.all_objects.filter(pk__in=batches, kind=Batch.Kind.MANUAL, prices__isnull=True).delete()
    return len(prices)


# ===================== тарих =====================
def fmt(v) -> str:
    """1200000.5000 → «1 200 000,5»."""
    if v is None:
        return "—"
    d = Decimal(str(v)).normalize()
    whole, _, frac = f"{d:f}".partition(".")
    sign = "-" if whole.startswith("-") else ""
    return sign + f"{int(whole.lstrip('-') or 0):,}".replace(",", " ") + ("," + frac if frac else "")


def _uid(user):
    return user if getattr(user, "pk", None) else None


def _log(action, user, *, item=None, batch=None, price=None, title="", detail="", data=None) -> Log:
    return Log.objects.create(action=action, user=_uid(user), item=item, batch=batch, price=price,
                              title=(title or str(item or batch or ""))[:500], detail=detail[:500], data=data or {})


class Conflict(Exception):
    """Шу ном + бирлик бошқа ресурсда бор — бирлаштириш керак."""

    def __init__(self, other: Item):
        super().__init__(f"«{other.name}» ({other.unit}) базада бор")
        self.other = other


REGION_NAMES = dict(Price._meta.get_field("region").choices)


# ===================== нархни тузатиш =====================
@transaction.atomic
def edit_price(p: Price, *, price, region, day: _date, qty=None, reason, user=None) -> bool:
    """Нархни тузатади — эски қиймат ва сабаби тарихда қолади. Ҳеч нарса ўзгармаса — False."""
    price = Decimal(price).quantize(Decimal("0.0001"))
    qty = Decimal(qty).quantize(Decimal("0.000001")) if qty is not None else None
    old_qty = Decimal(p.qty) if p.qty is not None else None
    parts = []
    if Decimal(p.price) != price:
        parts.append(f"нарх {fmt(p.price)} → {fmt(price)}")
    if p.region != region:
        parts.append(f"вилоят {REGION_NAMES[p.region]} → {REGION_NAMES[region]}")
    if p.date != day:
        parts.append(f"сана {p.date:%d.%m.%Y} → {day:%d.%m.%Y}")
    if old_qty != qty:
        parts.append(f"сони {fmt(old_qty)} → {fmt(qty)}")
    if not parts:
        return False
    old = {"price": str(p.price), "qty": str(p.qty) if p.qty is not None else None, "region": p.region,
           "date": p.date.isoformat()}
    touched = {(p.item_id, p.region, p.year), (p.item_id, region, day.year)}
    p.price, p.qty, p.region, p.date, p.year, p.edited = price, qty, region, day, day.year, True
    p.save(update_fields=["price", "qty", "region", "date", "year", "edited"])
    recalc(touched)
    _log(Log.Action.PRICE_EDIT, user, item=p.item, price=p, detail="; ".join(parts) + f". Сабаби: {reason}",
         data={"old": old, "new": {"price": str(price), "qty": str(qty) if qty is not None else None,
                                   "region": region, "date": day.isoformat()}, "reason": reason[:300]})
    return True


@transaction.atomic
def edit_batch(b: Batch, *, region, day: _date, note="", user=None) -> bool:
    """Тўпламнинг вилояти / санаси / изоҳи. Тўпламдаги нархлардан алоҳида тузатилмаганлари (вилояти ва санаси
    тўпламники билан бир хил) ҳам ўзгаради."""
    note = (note or "")[:300]
    parts = []
    if b.region != region:
        parts.append(f"вилоят {REGION_NAMES[b.region]} → {REGION_NAMES[region]}")
    if b.date != day:
        parts.append(f"сана {b.date:%d.%m.%Y} → {day:%d.%m.%Y}")
    if b.note != note:
        parts.append("изоҳ ўзгарди")
    if not parts:
        return False
    qs = Price.all_objects.filter(batch=b)
    touched = _groups(qs)
    n = qs.filter(region=b.region, date=b.date).update(region=region, date=day, year=day.year)
    old = {"region": b.region, "date": b.date.isoformat(), "note": b.note}
    b.region, b.date, b.note = region, day, note
    b.save(update_fields=["region", "date", "note"])
    if b.deleted_at is None:
        recalc(touched | _groups(qs))
    _log(Log.Action.BATCH_EDIT, user, batch=b, detail="; ".join(parts) + f" ({n} та нарх)",
         data={"old": old, "new": {"region": region, "date": day.isoformat(), "note": note}})
    return True


# ===================== номи, бирлаштириш =====================
def owner_of_key(key, exclude=None):
    """Шу калит қайси (саватчада бўлмаган) ресурсники — ўз калити ёки бошқа номи сифатида."""
    qs = Item.objects.filter(key=key)
    aq = Alias.objects.filter(key=key, item__deleted_at=None)
    if exclude is not None:
        qs, aq = qs.exclude(pk=exclude.pk), aq.exclude(item=exclude)
    return qs.first() or getattr(aq.select_related("item").first(), "item", None)


@transaction.atomic
def edit_item(it: Item, *, name, category, user=None) -> bool:
    """Номи / тоифаси. Номи ўзгарса калит янгиланади, эски номи «бошқа номи» бўлиб қолади (шу ном билан келган
    қаторлар шу ресурсга тушаверади). Янги ном бошқа ресурсники бўлса — ``Conflict`` (бирлаштириш керак)."""
    name = (name or "").strip()[:500] or it.name
    changed = False
    if name != it.name:
        new_key = item_key(name, it.unit)
        if new_key != it.key:
            other = owner_of_key(new_key, exclude=it)
            if other is not None:
                raise Conflict(other)
            Alias.objects.filter(key=new_key, item=it).delete()
            Alias.objects.get_or_create(key=it.key, defaults={"item": it, "name": it.name, "unit": it.unit,
                                                              "added_by": _uid(user)})
            it.key = new_key
        _log(Log.Action.RENAME, user, item=it, detail=f"«{it.name}» → «{name}»", data={"old": it.name, "new": name})
        it.name, changed = name, True
    if category in CATEGORY_LABELS and category != it.category:
        _log(Log.Action.CATEGORY, user, item=it, detail=f"{CATEGORY_LABELS[it.category]} → {CATEGORY_LABELS[category]}",
             data={"old": it.category, "new": category})
        it.category, changed = category, True
    if category in CATEGORY_LABELS and not it.verified:  # админ ўзи тоифасини кўрди — тасдиқланган
        it.verified, it.verified_at, it.verified_by, changed = True, timezone.now(), _uid(user), True
    if changed:
        it.edited = True
        it.save()
    return changed


def _name_part(key) -> str:
    return key.rsplit("|", 1)[0]


def similar_items(it: Item, q="", limit=8) -> list:
    """Бирлаштириш учун номзодлар: шу бирликдаги ресурслар, энг ўхшаши олдин. ``q`` бўлса — шу сўзлар бўйича
    қидирув, бўлмаса — номидаги узун сўзлар бўйича ва ўхшашлиги 50% дан юқорилари."""
    from difflib import SequenceMatcher

    from django.db.models import Q
    qs = Item.objects.exclude(pk=it.pk).filter(key__endswith="|" + it.unit_key)
    me = name_core(it.name)
    if q:
        for w in q.split():
            qs = qs.filter(Q(key__icontains=name_core(w)) | Q(name__icontains=w))
        cands = list(qs[:200])
    else:
        words = sorted({name_core(w) for w in re.split(r"[\W_]+", fold_name(norm_name(it.name))) if len(w) >= 3},
                       key=len, reverse=True)[:2]
        if not words:
            return []
        cond = Q()
        for w in words:
            cond |= Q(key__contains=w)
        cands = list(qs.filter(cond)[:300])
    scored = [(SequenceMatcher(None, me, name_core(c.name)).ratio(), c) for c in cands]
    scored = [(r, c) for r, c in scored if q or r >= 0.5]
    scored.sort(key=lambda t: (-t[0], t[1].name))
    out, spec = [], _spec(it.name)
    for r, c in scored[:limit]:
        c.similarity = round(100 * r)
        c.digits_differ = _spec(c.name) != spec  # 5ММ ва 6ММ, AII ва AIII — бошқа ресурс
        out.append(c)
    return out


@transaction.atomic
def merged_into(pk: int):
    """Бирлаштирилиб ўчган ресурс (``pk``) ҳозир қайси ресурсда — эски ҳаволалар учун. Занжир бўлса (A → B → C)
    охиргисигача боради; топилмаса — ``None``."""
    seen = set()
    while pk not in seen:
        seen.add(pk)
        it = Item.objects.filter(pk=pk).first()
        if it is not None:
            return it
        lg = Log.objects.filter(action=Log.Action.MERGE, data__from__pk=pk).exclude(item=None).order_by("-id").first()
        if lg is None:
            return None
        pk = lg.item_id
    return None


def merge_items(target: Item, source: Item, user=None) -> int:
    """``source`` ни ``target`` га қўшади: нархлари, бошқа номлари ва тарихи ўтади, ``source`` номи «бошқа номи»
    бўлиб қолади, ``source`` ўчади. Бирлиги бир хил бўлиши шарт. Ўтган нархлар сонини қайтаради."""
    if target.pk == source.pk:
        raise ValueError("Ресурсни ўзига қўшиб бўлмайди.")
    if target.deleted_at is not None:
        raise ValueError("Саватчадаги ресурсга қўшиб бўлмайди.")
    if target.unit_key != source.unit_key:
        raise ValueError(f"Бирлиги бошқа: «{target.unit}» ва «{source.unit}» — бирлаштириб бўлмайди.")
    src_key, src_pk = source.key_before or source.key, source.pk
    prices = Price.all_objects.filter(item=source)
    touched = {(target.pk, r, y) for r, y in prices.values_list("region", "year")}
    n = prices.update(item=target)
    Alias.objects.filter(item=source).update(item=target)
    if src_key != target.key:
        Alias.objects.update_or_create(key=src_key, defaults={"item": target, "name": source.name,
                                                               "unit": source.unit, "added_by": _uid(user)})
    Log.objects.filter(item=source).update(item=target)
    if target.category == "O" and source.category != "O":
        target.category = source.category
        target.save(update_fields=["category", "updated"])
    title = f"{source.name} ({source.unit})"
    source.delete()
    recalc(touched)
    _log(Log.Action.MERGE, user, item=target, detail=f"«{title}» шу ресурсга қўшилди: {n} та нарх",
         data={"from": {"name": source.name, "unit": source.unit, "key": src_key, "pk": src_pk}, "prices": n})
    return n


@transaction.atomic
def remove_alias(al: Alias, user=None):
    """Бошқа номни олиб ташлайди: шу ном билан келадиган янги қаторлар энди алоҳида ресурс бўлади
    (аввал қўшилган нархлар жойида қолади)."""
    _log(Log.Action.ALIAS_DEL, user, item=al.item, detail=f"«{al.name}» ({al.unit})")
    al.delete()


# ===================== саватча =====================
def _groups(qs) -> set:
    return set(qs.values_list("item_id", "region", "year"))


@transaction.atomic
def trash_item(it: Item, user=None):
    """Саватчага: рўйхатда кўринмайди, нархлари ҳисобга кирмайди, шу ном билан келган янги қатор — янги ресурс.
    ``restore_item`` қайтаради."""
    touched = _groups(Price.all_objects.filter(item=it))
    it.key_before, it.key = it.key, f"~trash~{it.pk}"
    it.deleted_at, it.deleted_by = timezone.now(), _uid(user)
    it.save()
    recalc(touched)
    _log(Log.Action.TRASH, user, item=it, title=f"{it.name} ({it.unit})",
         detail=f"{Price.all_objects.filter(item=it).count()} та нарх")


@transaction.atomic
def restore_item(it: Item, user=None) -> Item:
    """Саватчадан қайтаради. Шу орада шу ном билан янги ресурс очилган бўлса — ўшанга қўшилади. Қайтган
    (ёки қўшилган) ресурсни қайтаради."""
    key = it.key_before
    live = owner_of_key(key)
    if live is not None:
        merge_items(live, it, user)
        _log(Log.Action.RESTORE, user, item=live, detail="саватчадан қайтди ва шу номдаги ресурсга қўшилди")
        return live
    it.key, it.key_before, it.deleted_at, it.deleted_by = key, "", None, None
    it.save()
    recalc(_groups(Price.all_objects.filter(item=it)))
    _log(Log.Action.RESTORE, user, item=it)
    return it


@transaction.atomic
def trash_batch(b: Batch, user=None):
    """Тўпламни саватчага: нархлари ҳисобдан чиқади, смета яна «Тасдиқлаб йиғиш» да кўринади."""
    b.deleted_at, b.deleted_by = timezone.now(), _uid(user)
    b.save(update_fields=["deleted_at", "deleted_by"])
    qs = Price.all_objects.filter(batch=b)
    recalc(_groups(qs))
    _log(Log.Action.TRASH, user, batch=b, detail=f"тўплам, {qs.count()} та нарх")


@transaction.atomic
def restore_batch(b: Batch, user=None):
    if b.work_id and Batch.objects.filter(kind=b.kind, work_id=b.work_id).exists():
        raise ValueError("Шу иш базага қайта киритилган — аввал янгисини саватчага ташланг.")
    b.deleted_at = b.deleted_by = None
    b.save(update_fields=["deleted_at", "deleted_by"])
    recalc(_groups(Price.all_objects.filter(batch=b)))
    _log(Log.Action.RESTORE, user, batch=b)


@transaction.atomic
def purge_batch(b: Batch, user=None):
    """Саватчадан бутунлай: нархлари ўчади, нархи қолмаган ресурслар ҳам кетади, юкланган файл нусхаси ҳам."""
    from pathlib import Path

    from django.conf import settings
    qs = Price.all_objects.filter(batch=b)
    items, touched = set(qs.values_list("item_id", flat=True)), _groups(qs)
    _log(Log.Action.PURGE, user, title=b.title, detail=f"тўплам, {REGION_NAMES[b.region]}, {b.date:%d.%m.%Y}, "
                                                       f"{qs.count()} та нарх")
    path = b.file_path
    b.delete()
    recalc(touched)
    Item.all_objects.filter(pk__in=items, prices__isnull=True).delete()
    if path and not Batch.all_objects.filter(file_path=path).exists():
        (Path(settings.MEDIA_ROOT) / path).unlink(missing_ok=True)


@transaction.atomic
def purge_item(it: Item, user=None):
    """Ресурсни ва ҳамма нархларини бутунлай ўчиради; бўшаб қолган қўлда киритилган тўпламлар ҳам кетади."""
    batches = set(Price.all_objects.filter(item=it).values_list("batch_id", flat=True))
    _log(Log.Action.PURGE, user, title=f"{it.name} ({it.unit})",
         detail=f"ресурс, {Price.all_objects.filter(item=it).count()} та нарх")
    it.delete()
    Batch.all_objects.filter(pk__in=batches, kind=Batch.Kind.MANUAL, prices__isnull=True).delete()


def trash_counts() -> dict:
    return {"items": Item.all_objects.exclude(deleted_at=None).count(),
            "batches": Batch.all_objects.exclude(deleted_at=None).count()}


def live_items():
    """Ҳисобга кирадиган (саватчада бўлмаган) нархи бор ресурслар."""
    return Item.objects.filter(pk__in=Price.objects.values("item_id"))


# ===================== паст / ўрта / юқори =====================
SAME = Decimal("0.001")  # 0,1% гача фарқ — бир нарх (36 030,00 / 36 030,10 / 36 030,12 — яхлитлаш фарқи)


def _levels(prices, seen=None) -> list:
    """Нархларни «бир хил нарх» гуруҳларига йиғади: [(вакил нарх, вазни, {гуруҳдаги нархлар})], арзонидан.
    ``seen[i]`` — сон ёки манбалар тўплами (файл/смета): тўпламлар бирлашади — бир файл икки марта саналмайди.
    Вакил — гуруҳда энг кўп учрагани (тенг бўлса арзони)."""
    rows = sorted(zip(prices, seen if seen is not None else [1] * len(prices)), key=lambda r: r[0])
    groups = []
    for p, w in rows:
        if not groups or p > groups[-1][0][0] * (1 + SAME):
            groups.append([])
        groups[-1].append((p, w))

    def weight(ws):
        n, src = 0, set()
        for w in ws:
            if isinstance(w, (set, frozenset)):
                src |= w
            else:
                n += w
        return n + len(src)

    out = []
    for g in groups:
        by_price = {}
        for p, w in g:
            by_price.setdefault(p, []).append(w)
        rep = max(by_price, key=lambda p: (weight(by_price[p]), -p))
        out.append((rep, weight([w for _, w in g]), set(by_price)))
    return out


def _wmedian(levels):
    """Вазнли медиана: файлларнинг ярми шу нархда ёки арзонроқда (тенг бўлса арзони)."""
    total, cum = sum(w for _, w, _ in levels), 0
    for rep, w, _ in levels:
        cum += w
        if cum * 2 >= total:
            return rep
    return levels[-1][0]


def three(prices, seen=None) -> tuple:
    """(паст, ўрта, юқори, четдагилар тўплами) — модуль бошидаги қоида бўйича.
    ``seen`` — ҳар нарх неча файлда учрагани (сон ёки манбалар тўплами), ``prices`` билан бир тартибда."""
    if not prices:
        return None, None, None, set()
    lv = _levels(prices, seen)
    out = set()
    if len(prices) > 3:
        med = _wmedian(lv)
        bad = [x for x in lv if x[0] > med * OUTLIER or x[0] * OUTLIER < med]
        if len(bad) < len(lv):
            out = set().union(*(x[2] for x in bad)) if bad else set()
            lv = [x for x in lv if x not in bad]
    return lv[0][0], _wmedian(lv), lv[-1][0], out


def _sources(pk, places, seen):
    """Нарх қайси файлларда учрагани — вазн учун (бир файл икки тўпламда бўлса ҳам бир марта)."""
    src = {str(pl.get("job") or pl.get("file") or "") for pl in (places or []) if isinstance(pl, dict)}
    src.discard("")
    return frozenset(src) if src else frozenset({f"p{pk}"})


def _money(v) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"))


def recalc(keys):
    """keys — {(item_id, region, year)}: шу гуруҳларнинг нархи қайта ҳисобланади, «четда» белгиси янгиланади.
    Админ белгилаган (``pin``) нархлар ҳисобга аралашмайди — ҳисобдан кейин паст / ўрта / юқори ўрнига қўйилади."""
    for item_id, region, year in keys:
        qs = Price.objects.filter(item_id=item_id, region=region, year=year)
        rows = list(qs.filter(excluded=False).values_list("id", "price", "date", "places", "seen", "pin"))
        pins = {}
        for i, p, _, _, _, pin in sorted(rows, key=lambda r: r[0]):  # бир хил белги иккита бўлса — кейингиси
            if pin:
                pins[pin] = p
        free = [r for r in rows if not r[5]]
        live = [(i, p, d) for i, p, d, *_ in free]
        low, mid, high, out = three([p for _, p, _ in live], [_sources(i, pl, sn) for i, _, _, pl, sn, _ in free])
        if low is None and pins:
            low, mid, high, _ = three(list(pins.values()))
        qs.exclude(outlier=False).update(outlier=False)
        if out:
            qs.filter(id__in=[i for i, p, _ in live if p in out]).update(outlier=True)
        if low is None:
            Stat.objects.filter(item_id=item_id, region=region, year=year).delete()
            continue
        low, mid, high = pins.get("low", low), pins.get("mid", mid), pins.get("high", high)
        n = len(live) - sum(1 for _, p, _ in live if p in out) + sum(1 for r in rows if r[5])
        Stat.objects.update_or_create(item_id=item_id, region=region, year=year, defaults={
            "low": _money(low), "mid": _money(mid) if mid is not None else None, "high": _money(high), "n": n,
            "last_date": max(d for _, _, d, *_ in rows)})


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
    return {"items": live_items().count(), "prices": Price.objects.count(),
            "batches": Batch.objects.count(), "excluded": Price.objects.filter(excluded=True).count(),
            "outliers": Price.objects.filter(outlier=True, excluded=False).count()}


# ===================== кўрсаткичлар =====================
def category_stats() -> list:
    """[(тоифа, номи, ресурслар, нархлар)]."""
    from django.db.models import Count
    items = dict(live_items().values_list("category").annotate(c=Count("id")))
    prices = dict(Price.objects.values_list("item__category").annotate(c=Count("id")))
    return [(k, label, items.get(k, 0), prices.get(k, 0)) for k, label in CATEGORY_LABELS.items()]


COVER_JOBS = 60
JOB_ROWS_TTL = 7 * 24 * 3600


def _job_rows(jobs: list) -> dict:
    """{смета id: [(калит, тоифа), ...]} — сметанинг ресурс қаторлари. ``result`` катта (ҳар бири ярим МБгача),
    шунинг учун ҳар смета бир марта ўқилиб, кешда сақланади; смета қайта нархланса (жами ўзгаради) — янгидан."""
    from django.core.cache import cache
    ck = {j[0]: f"narxbaza:jobrows:{j[0]}:{j[1]}:{j[2]}" for j in jobs}
    got = cache.get_many(list(ck.values()))
    out = {jid: got[k] for jid, k in ck.items() if k in got}
    miss = [jid for jid in ck if jid not in out]
    fresh = {}
    for jid, res in Job.objects.filter(pk__in=miss).values_list("id", "result").iterator(chunk_size=10):
        rows = []
        for r in (res or {}).get("rows") or []:
            name, unit = str(r.get("name") or "").strip(), str(r.get("unit") or "").strip()
            if r.get("status") == "WORK_HEADER" or not name or not unit:
                continue
            rows.append((item_key(name, unit), category_of(r.get("category") or r.get("default_category"))))
        out[jid] = fresh[ck[jid]] = rows
    if fresh:
        cache.set_many(fresh, JOB_ROWS_TTL)
    return out


def coverage(limit=COVER_JOBS) -> dict:
    """База охирги нархланган сметаларни тахминан неча фоиз нархлай олади (номи + бирлиги бўйича, вилоят ва йилдан
    қатъи назар). Сметанинг ўзидан киритилган нархлар ҳисобланмайди — бўлмаса ўз нархи билан «қопланиб» қолади."""
    from django.core.cache import cache
    last = Price.objects.order_by("-id").values_list("id", flat=True).first() or 0
    njobs = Job.objects.filter(status=Job.Status.DONE).count()
    from django.db.models import Max
    stamp = f'{Log.objects.aggregate(m=Max("id"))["m"] or 0}:{Alias.objects.count()}'  # тузатиш / бирлаштириш
    ck = f"narxbaza:cover4:{last}:{Price.objects.count()}:{njobs}:{limit}:{stamp}"
    got = cache.get(ck)
    if got is not None:
        return got
    # ресурс → қайси тўпламлардан нархи бор; смета → у кирган тўпламлар (ўз папкасининг нархлари ҳисобланмайди)
    src, job_batches = {}, {}
    for key, bid in Price.objects.filter(excluded=False).values_list("item__key", "batch_id"):
        src.setdefault(key, set()).add(bid)
    for akey, ikey in Alias.objects.filter(item__deleted_at=None).values_list("key", "item__key"):
        if ikey in src:
            src[akey] = src[ikey]
    for b in Batch.objects.exclude(kind__in=(Batch.Kind.MANUAL, Batch.Kind.FILE)).exclude(work_id=None):
        jq = {"folder": {"batch__project_id": b.work_id}, "object": {"batch_id": b.work_id}, "smeta": {"pk": b.work_id}}[b.kind]
        for jid in Job.objects.filter(**jq).values_list("id", flat=True):
            job_batches.setdefault(str(jid), set()).add(b.pk)
    total = covered = 0
    by_cat, per_job = {k: [0, 0] for k in CATEGORY_LABELS}, []
    # фақат индексдаги устунлар — ``result``га тегмайди
    jobs = list(Job.objects.filter(status=Job.Status.DONE).order_by("-created")
                .values_list("id", "sum_total", "sum_not_priced")[:limit])
    rows_of = _job_rows(jobs)
    for jid, *_ in jobs:
        mine, t, c = job_batches.get(str(jid), set()), 0, 0
        for key, cat in rows_of.get(jid, ()):
            ok = bool(src.get(key, set()) - mine)
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
    out, aliases = {}, {}
    for akey, iid in Alias.objects.filter(item__deleted_at=None).values_list("key", "item_id"):
        aliases.setdefault(iid, []).append(akey)
    for s in Stat.objects.filter(region=period["region"], year=int(period["year"]),
                                 item__deleted_at=None).select_related("item"):
        price = getattr(s, lv)
        if price is None:
            price = s.mid if s.mid is not None else s.low
        it = s.item
        out[it.key] = {"price": float(price), "n": s.n, "unit_key": it.key.rsplit("|", 1)[-1], "unit": it.unit,
                       "category": it.category if it.category in ("I", "J", "K", "L", "M") else "",
                       "note": f"{word} нарх, {s.n} та нархдан", "name": it.name, "item": it.key}
        for akey in aliases.get(it.pk, ()):  # эски / бирлаштирилган номи билан келган қатор ҳам
            out.setdefault(akey, {**out[it.key], "unit_key": akey.rsplit("|", 1)[-1]})
    return out


# ===================== нарх динамикаси =====================
def _val(s):
    return s.mid if s.mid is not None else s.low


def dynamics(stats) -> dict:
    """Паспорт учун: ``latest`` — ҳар вилоятнинг охирги йил нархи; ``years`` ва ``rows`` — вилоят × йил жадвали
    (ўрта нарх, олдинги йилга нисбатан ўзгариш %, кичик график нуқталари). Бир йил бўлса — жадвал йўқ."""
    from resbase.regions import REGIONS
    order = {k: i for i, (k, _) in enumerate(REGIONS)}
    by_region = {}
    for s in stats:
        by_region.setdefault(s.region, {})[s.year] = s
    regions = sorted(by_region, key=lambda r: order.get(r, 99))
    latest = [by_region[r][max(by_region[r])] for r in regions]
    for s in latest:  # паспорт карточкасида: ўрта нарх паст…юқори оралиғининг қаерида (%)
        mid = s.mid if s.mid is not None else s.low
        s.mid_pos = round(float((mid - s.low) / (s.high - s.low) * 100), 1) if s.high > s.low else 50.0
    years = sorted({s.year for s in stats})
    rows = []
    if len(years) > 1:
        for r in regions:
            cells, prev, pts = [], None, []
            for y in years:
                s = by_region[r].get(y)
                change = None
                if s is not None:
                    v = _val(s)
                    if prev is not None and prev > 0:
                        change = round(float((v - prev) / prev * 100), 1)
                    prev = v
                    pts.append((y, float(v)))
                cells.append({"year": y, "s": s, "change": change})
            rows.append({"region": r, "label": REGION_NAMES.get(r, r), "cells": cells, "spark": _spark(pts, years)})
    return {"latest": latest, "years": years, "rows": rows}


def _spark(pts, years, w=96, h=24) -> str:
    """SVG polyline нуқталари: x — йил, y — нарх (шу вилоятнинг энг арзонидан энг қимматигача)."""
    if len(pts) < 2:
        return ""
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
    y0, y1 = years[0], years[-1]
    span = (hi - lo) or 1
    return " ".join(f"{(y - y0) / (y1 - y0) * (w - 4) + 2:.1f},{h - 2 - (v - lo) / span * (h - 4):.1f}" for y, v in pts)


# ===================== текшириш навбати =====================
STALE_DAYS = 365  # стандарт; амалдагиси — панелдан (stale_days)


def stale_days() -> int:
    from panel import conf
    return conf.get("price_stale_days")
DUP_MIN = 0.86  # номлари шунчалик ўхшаш ва рақамлари бир хил бўлса — эҳтимол икки нусха


_ROMAN = re.compile(r"[a-zа-я]{0,2}(i{1,3}|iv|vi{0,3}|ix|xi{0,3})")


def _spec(name) -> tuple:
    """Номдаги «ўлчам ва синф» белгилари: рақамли сўзлар ва рим рақамли синф («A-II», «III сорта») — улар бошқача
    бўлса, номи ўхшаш бўлса ҳам бошқа ресурс («8 мм» ≠ «10 мм», «AII» ≠ «AIII»)."""
    words = re.split(r"[\W_]+", fold_name(norm_name(name)))
    return tuple(sorted(w for w in words if w and (any(c.isdigit() for c in w) or _ROMAN.fullmatch(w))))


def duplicate_pairs() -> list:
    """Эҳтимол бир ресурснинг икки нусхаси: бирлиги бир хил, номи ≥86% ўхшаш, номидаги ўлчам ва синф белгилари
    (``_spec``) бир хил, «бошқа-бошқа» деб белгиланмаган. [(ўхшашлик %, a, b)] — энг ўхшаши олдин. 1 соат кешланади.
    Фақат нуқта / вергул / бўшлиқ / лотин-кирилл ҳарфи билан фарқ қилганлар бу ерга тушмайди — калити бир хил,
    ўзи бирлашади."""
    from difflib import SequenceMatcher

    from django.core.cache import cache
    from django.db.models import Max

    from core.normalize import name_core

    from .models import DupIgnore
    live = live_items()
    stamp = (2, live.count(), live.aggregate(m=Max("updated"))["m"], Log.objects.aggregate(m=Max("id"))["m"],
             DupIgnore.objects.count())
    ck = "narxbaza:dups:" + hashlib.md5(repr(stamp).encode()).hexdigest()
    got = cache.get(ck)
    if got is None:
        groups = {}
        for pk, name, key in live.values_list("pk", "name", "key"):
            unit = key.rpartition("|")[2]
            words = [w for w in re.split(r"[\W_]+", fold_name(norm_name(name))) if len(w) >= 3 and not w.isdigit()]
            if words:
                groups.setdefault((unit, words[0]), []).append((pk, name_core(name), _spec(name)))
        ignored = set(DupIgnore.objects.values_list("a_id", "b_id"))
        got = []
        for g in groups.values():
            for i in range(len(g)):
                for j in range(i + 1, len(g)):
                    (pa, na, da), (pb, nb, db) = g[i], g[j]
                    if da != db or (min(pa, pb), max(pa, pb)) in ignored:
                        continue
                    sm = SequenceMatcher(None, na, nb)
                    if sm.quick_ratio() >= DUP_MIN and (r := sm.ratio()) >= DUP_MIN:
                        got.append((round(100 * r), min(pa, pb), max(pa, pb)))
        got.sort(key=lambda t: -t[0])
        cache.set(ck, got, 3600)
    return got


def ignore_pair(a_id, b_id, user=None):
    from .models import DupIgnore
    a, b = min(a_id, b_id), max(a_id, b_id)
    DupIgnore.objects.get_or_create(a_id=a, b_id=b, defaults={"added_by": _uid(user)})


def stale_items():
    """1 йилдан бери янги нарх келмаган ресурслар (охирги нарх санаси бўйича, эскиси олдин)."""
    from datetime import timedelta

    from django.db.models import Max
    cutoff = timezone.localdate() - timedelta(days=stale_days())
    return live_items().annotate(last=Max("prices__date")).filter(last__lt=cutoff).order_by("last")


def review_counts() -> dict:
    return {"unver": live_items().filter(verified=False).count(), "cat": live_items().filter(category="O").count(),
            "outlier": Price.objects.filter(outlier=True, excluded=False).count(),
            "dup": len(duplicate_pairs()), "stale": stale_items().count()}


# ===================== бирданига =====================
@transaction.atomic
def bulk_category(items, category, user=None) -> int:
    return sum(1 for it in items if edit_item(it, name=it.name, category=category, user=user))


@transaction.atomic
def bulk_trash(items, user=None) -> int:
    n = 0
    for it in items:
        trash_item(it, user)
        n += 1
    return n


@transaction.atomic
def bulk_merge(target, items, user=None) -> tuple:
    """Белгиланганларни ``target`` га: (қўшилган ресурслар, ўтган нархлар); бирлиги бошқалари — ўтказиб юборилади."""
    done = moved = 0
    skipped = []
    for it in items:
        if it.pk == target.pk:
            continue
        if it.unit_key != target.unit_key:
            skipped.append(it)
            continue
        moved += merge_items(target, it, user)
        done += 1
    return done, moved, skipped


# ===================== калитларни янгилаш =====================
@transaction.atomic
def rekey_all(user=None) -> dict:
    """Ном калити қоидаси ўзгарганда (``core.normalize.name_core``): ҳамма ресурс, бошқа номи ва текширув қаторининг
    калити қайта ҳисобланади. Энди бир хил калитли бўлиб қолган ресурслар (фақат нуқта, вергул, бўшлиқ ёки
    лотин/кирилл ҳарфи билан фарқ қилганлар) кўп нархлисига бирлаштирилади. ``{merged, items, aliases, rows}``."""
    from collections import defaultdict

    from django.db.models import Count

    from .models import IntakeRow
    out = {"merged": 0, "items": 0, "aliases": 0, "rows": 0}
    if not Item.all_objects.exists():
        return out
    groups = defaultdict(list)
    for it in Item.all_objects.filter(deleted_at=None):
        groups[item_key(it.name, it.unit)].append(it)
    n = dict(Price.all_objects.values_list("item_id").annotate(c=Count("id")))
    for g in groups.values():
        if len(g) < 2:
            continue
        g.sort(key=lambda i: (-n.get(i.pk, 0), not i.verified, i.pk))
        for src in g[1:]:
            src.refresh_from_db()
            merge_items(g[0], src, user=user)
            out["merged"] += 1
    # ресурслар: аввал вақтинча калит (unique бузилмасин), кейин янгиси
    live = list(Item.all_objects.filter(deleted_at=None))
    want = {it.pk: item_key(it.name, it.unit) for it in live}
    changed = [it for it in live if it.key != want[it.pk]]
    for it in changed:
        it.key = f"~rk~{it.pk}"
    Item.all_objects.bulk_update(changed, ["key"], batch_size=500)
    for it in changed:
        it.key = want[it.pk]
    Item.all_objects.bulk_update(changed, ["key"], batch_size=500)
    out["items"] = len(changed)
    trashed = list(Item.all_objects.exclude(deleted_at=None).exclude(key_before=""))
    for it in trashed:
        it.key_before = item_key(it.name, it.unit)
    Item.all_objects.bulk_update(trashed, ["key_before"], batch_size=500)
    # бошқа номлар: ресурснинг ўз калити ёки бошқа ресурс калити бўлиб қолса — керак эмас
    taken = set(want.values())
    keep, drop, seen = [], [], set()
    for a in Alias.objects.all():
        k = item_key(a.name, a.unit)
        if k in taken or k in seen:
            drop.append(a.pk)
        else:
            seen.add(k)
            if a.key != k:
                a.key = k
                keep.append(a)
    Alias.objects.filter(pk__in=drop).delete()
    for a in keep:
        a.key, a.new = f"~rk~{a.pk}", a.key
    Alias.objects.bulk_update(keep, ["key"], batch_size=500)
    for a in keep:
        a.key = a.new
    Alias.objects.bulk_update(keep, ["key"], batch_size=500)
    out["aliases"] = len(keep) + len(drop)
    owner = {k: pk for pk, k in want.items()}
    owner.update({k: a for k, a in Alias.objects.values_list("key", "item_id") if k not in owner})
    rows = []
    for r in IntakeRow.objects.only("pk", "key", "name", "unit", "item_id").iterator(chunk_size=2000):
        k = item_key(r.name, r.unit)
        if r.key != k or (r.item_id is None and k in owner):
            r.key = k
            if r.item_id is None:  # бирлаштирилган ресурс ўчди — қатор энди асосийсига тегишли
                r.item_id = owner.get(k)
            rows.append(r)
    IntakeRow.objects.bulk_update(rows, ["key", "item"], batch_size=1000)
    out["rows"] = len(rows)
    log.info("narxbaza: калитлар янгиланди — %s", out)
    return out
