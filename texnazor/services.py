"""Texnazor: ofyortani bazaga o'qish, oyma-oy hajmlar va pul hisobi.

Hamma pul hisobi bazada: Σ (hajm × 1 birlik narxi turlar bo'yicha) -> ``calc.chain``. Excel faqat yuklashda
o'qiladi (bir marta, fonda) — sahifa ochilganda emas.
"""
import re
import shutil
from pathlib import Path
from typing import Dict, Optional

from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from core import convert
from core.convert import check_xlsx

from . import calc
from .models import COST_FIELDS, Done, Item, Period, Section, SiteObject
from .ofyorta import Ofyorta, OfyortaError, parse

ALLOWED_EXT = (".xls", ".xlsx", ".xlsm")
MAX_UPLOAD = 60 * 1024 * 1024
ITEM_KINDS = (Item.Kind.WORK, Item.Kind.FREE, Item.Kind.EXTRA)  # texnazor hajmini belgilaydiganlar


class TexnazorError(Exception):
    pass


# ===================== obyekt qo'shish va ofyortani o'qish =====================
def create_object(owner, upload, **fields) -> SiteObject:
    ext = Path(upload.name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise TexnazorError("Офёрта .xls, .xlsx ёки .xlsm бўлиши керак")
    if upload.size > MAX_UPLOAD:
        raise TexnazorError(f"Файл жуда катта ({upload.size // 2**20} МБ) — кўпи билан {MAX_UPLOAD // 2**20} МБ")
    obj = SiteObject(owner=owner, source_name=Path(upload.name).name[:255], source_path=f"src/ofyorta{ext}",
                     name=(fields.get("name") or Path(upload.name).stem)[:300],
                     **{k: (fields.get(k) or "")[:300] for k in ("address", "customer", "contractor", "contract")})
    obj.save()
    dest = obj.abs(obj.source_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as out:
        for chunk in upload.chunks():
            out.write(chunk)
    return obj


def import_ofyorta(obj: SiteObject, progress=None):
    """Fonda (worker): .xls -> .xlsx, o'qish, bazaga yozish. Xato — ``obj.status = FAILED`` va sababi."""
    def step(text, done=0, total=0):
        obj.progress = {"step": text, "done": done, "total": total}
        SiteObject.objects.filter(pk=obj.pk).update(progress=obj.progress)
        if progress:
            progress()

    try:
        src = obj.abs(obj.source_path)
        step("Файл очилмоқда")
        xlsx = convert.ensure_xlsx(src, obj.abs("conv"))
        check_xlsx(xlsx)
        step("Офёрта ўқилмоқда")
        of = parse(xlsx, progress=lambda d, t: step("Офёрта ўқилмоқда", d, t))
        if not of.items:
            raise OfyortaError("Офёртада иш топилмади (А устунида тартиб рақами, C да номи бўлган қаторлар)")
        step("Базага ёзилмоқда", 0, len(of.rows))
        _store(obj, of)
    except Exception as e:  # noqa: BLE001 — sabab sahifada ko'rinadi
        obj.status, obj.error = SiteObject.Status.FAILED, f"{type(e).__name__}: {e}" if not isinstance(e, OfyortaError) else str(e)
        obj.save(update_fields=["status", "error"])
        return obj
    return obj


@transaction.atomic
def _store(obj: SiteObject, of: Ofyorta):
    obj.items.all().delete()
    obj.sections.all().delete()
    sections = Section.objects.bulk_create([Section(obj=obj, order=i, title=t[:500]) for i, t in enumerate(of.sections)])
    extra_section = None
    if any(r.kind == "extra" for r in of.rows):
        extra_section = Section.objects.create(obj=obj, order=len(sections), title="Сводкадаги қўшимча қаторлар")
    tops, children = [], []
    by_index = {}
    for i, r in enumerate(of.rows):
        if r.kind == "section":
            continue
        it = Item(obj=obj, order=i, excel_row=r.excel_row, kind=r.kind, num=r.num[:20], code=r.code[:80], name=r.name,
                  search=search_key(f"{r.num} {r.code} {r.name}"),
                  unit=r.unit[:80], qty=r.qty, total_qty=r.total_qty, price=r.price, amount=r.amount, category=r.category,
                  section=extra_section if r.kind == "extra" else (sections[r.section] if r.section is not None else None),
                  **{COST_FIELDS[c]: v for c, v in r.unit_cost.items() if c in COST_FIELDS})
        by_index[i] = it
        (children if r.kind == "res" else tops).append((i, it))
    Item.objects.bulk_create([it for _i, it in tops], batch_size=2000)
    for i, it in children:
        it.parent = by_index[of.rows[i].parent]
    Item.objects.bulk_create([it for _i, it in children], batch_size=2000)
    counts = {}
    for _i, it in tops:
        if it.section_id:
            counts[it.section_id] = counts.get(it.section_id, 0) + 1
    for s in sections + ([extra_section] if extra_section else []):
        s.items_count = counts.get(s.pk, 0)
    Section.objects.bulk_update(sections + ([extra_section] if extra_section else []), ["items_count"])

    obj.sheet = of.sheet
    obj.rates, obj.file_totals, obj.warnings = of.rates, of.file_totals, of.warnings
    if of.title and obj.name == Path(obj.source_name).stem[:300]:
        obj.name = of.title[:300]  # nomi kiritilmagan — ofyortadagi obyekt nomi
    obj.object_type = of.object_type[:200]
    cat = {}
    for _i, it in tops:
        for c, f in COST_FIELDS.items():
            cat[c] = cat.get(c, 0.0) + getattr(it, f) * it.qty
    obj.contract_totals = calc.chain(cat, obj.rates)
    obj.status, obj.error = SiteObject.Status.READY, ""
    obj.progress = {"step": "Тайёр", "done": len(of.rows), "total": len(of.rows)}
    obj.save()


def search_key(text: str) -> str:
    return " ".join(str(text).upper().replace("Ё", "Е").split())


def delete_object(obj: SiteObject):
    shutil.rmtree(obj.dir, ignore_errors=True)
    obj.delete()


# ===================== oylar =====================
def periods(obj: SiteObject):
    return list(obj.periods.all())


def add_period(obj: SiteObject, year: int, month: int) -> Period:
    if not (1 <= month <= 12 and 2000 <= year <= 2100):
        raise TexnazorError("Ой нотўғри")
    last = obj.periods.order_by("-year", "-month").first()
    if last and (year, month) <= (last.year, last.month):
        raise TexnazorError(f"Янги ой охиргисидан ({last.title}) кейин бўлиши керак")
    return Period.objects.create(obj=obj, year=year, month=month)


def next_month(obj: SiteObject):
    last = obj.periods.order_by("-year", "-month").first()
    if last is None:
        now = timezone.localdate()
        return now.year, now.month
    return (last.year + 1, 1) if last.month == 12 else (last.year, last.month + 1)


def _before(period: Period) -> Q:
    return Q(period__year__lt=period.year) | Q(period__year=period.year, period__month__lt=period.month)


# ===================== hajm belgilash =====================
_PCT = re.compile(r"^\s*([\d\s.,]+)\s*%\s*$")


def parse_value(text, contract_qty: float) -> Optional[float]:
    """«35», «35,5», «60%» (shartnoma hajmidan). Bo'sh — None (yozuv o'chiriladi)."""
    s = str(text if text is not None else "").strip()
    if not s:
        return None
    m = _PCT.match(s)
    if m:
        return float(m.group(1).replace(" ", "").replace(",", ".")) / 100 * contract_qty
    try:
        return float(s.replace(" ", "").replace("\xa0", "").replace(",", "."))
    except ValueError:
        raise TexnazorError(f"Сон ёки фоиз ёзинг (масалан «35» ёки «60%»): «{s}»")


def done_before(period: Period, items) -> Dict[int, float]:
    rows = (Done.objects.filter(_before(period), period__obj_id=period.obj_id, item__in=items)
            .values("item_id").annotate(q=Sum("qty")))
    return {r["item_id"]: r["q"] for r in rows}


def set_done(period: Period, item: Item, text) -> Optional[float]:
    """Shu oyda bajarilgan hajm. Qoldiqdan oshib ketmasin (qolgan oylarni ham hisobga olib)."""
    if period.closed:
        raise TexnazorError(f"{period.title} ёпилган — ўзгартириб бўлмайди")
    if item.kind not in ITEM_KINDS or item.obj_id != period.obj_id:
        raise TexnazorError("Бу қаторнинг ҳажми белгиланмайди (ресурс иш ҳажмига эргашади)")
    qty = parse_value(text, item.qty)
    if qty is None or abs(qty) < 1e-12:
        Done.objects.filter(period=period, item=item).delete()
        return None
    if qty < 0:
        raise TexnazorError("Ҳажм манфий бўлмайди")
    others = Done.objects.filter(item=item).exclude(period=period).aggregate(q=Sum("qty"))["q"] or 0.0
    left = item.qty - others
    if qty > left + max(1e-9, abs(item.qty) * 1e-9):
        raise TexnazorError(f"Қолдиқ {fmt_qty(left)} {item.unit} — {fmt_qty(qty)} ёзиб бўлмайди"
                            f" (шартнома {fmt_qty(item.qty)}, бошқа ойларда {fmt_qty(others)})")
    Done.objects.update_or_create(period=period, item=item, defaults={"qty": qty})
    return qty


def fill_section(period: Period, section: Section, pct: float) -> int:
    """Bo'limdagi hamma ish jami ``pct`` % bajarilgan bo'lsin (shu oyga — yetmagan qismi). Nechta qator o'zgardi."""
    if period.closed:
        raise TexnazorError(f"{period.title} ёпилган — ўзгартириб бўлмайди")
    pct = max(0.0, min(100.0, float(pct)))
    items = list(section.items.filter(kind__in=ITEM_KINDS))
    before = done_before(period, items)
    after = {r["item_id"]: r["q"] for r in Done.objects.filter(item__in=items, period__obj_id=period.obj_id)
             .exclude(_before(period)).exclude(period=period).values("item_id").annotate(q=Sum("qty"))}
    n = 0
    with transaction.atomic():
        for it in items:
            want = it.qty * pct / 100 - before.get(it.pk, 0.0)
            want = min(max(want, 0.0), it.qty - before.get(it.pk, 0.0) - after.get(it.pk, 0.0))
            if want > 1e-12:
                Done.objects.update_or_create(period=period, item=it, defaults={"qty": want})
            else:
                Done.objects.filter(period=period, item=it).delete()
            n += 1
    return n


def fmt_qty(v: float) -> str:
    s = f"{v:,.6f}".rstrip("0").rstrip(".")
    return s.replace(",", " ").replace(".", ",")  # sahifadagidek: 1 234,5


# ===================== pul =====================
def _cost_sum(prefix: str = "item__"):
    expr = None
    for f in COST_FIELDS.values():
        e = F("qty") * F(prefix + f)
        expr = e if expr is None else expr + e
    return expr


def _cats(qs) -> Dict[str, float]:
    agg = qs.aggregate(**{c: Sum(F("qty") * F("item__" + f)) for c, f in COST_FIELDS.items()})
    return {c: v or 0.0 for c, v in agg.items()}


def period_totals(period: Period) -> Dict[str, dict]:
    """Ф-3 kabi: shu oy, avvalgi oylar, boshidan jami, qoldiq — har biri svodka zanjiri bilan."""
    obj = period.obj
    rates = obj.rates or {}
    this = calc.chain(_cats(Done.objects.filter(period=period)), rates)
    before = calc.chain(_cats(Done.objects.filter(_before(period), period__obj=obj)), rates)
    cum = {k: this[k] + before[k] for k in this}
    contract = obj.contract_totals or calc.chain({}, rates)
    left = {k: contract.get(k, 0.0) - cum[k] for k in this}
    return {"this": this, "before": before, "cum": cum, "contract": contract, "left": left}


def section_stats(period: Period) -> Dict[int, dict]:
    """Bo'limlar bo'yicha: shartnoma (to'g'ridan-to'g'ri), avval, shu oy (summalar)."""
    obj = period.obj
    contract = {r["section_id"]: r["s"] for r in obj.items.filter(kind__in=ITEM_KINDS).values("section_id").annotate(s=Sum("amount"))}
    def by_section(qs):
        return {r["item__section_id"]: r["s"] for r in qs.values("item__section_id").annotate(s=Sum(_cost_sum()))}
    before = by_section(Done.objects.filter(_before(period), period__obj=obj))
    this = by_section(Done.objects.filter(period=period))
    return {sid: {"contract": c or 0.0, "before": before.get(sid, 0.0), "this": this.get(sid, 0.0)}
            for sid, c in contract.items()}


def item_rows(period: Period, items) -> list:
    """Jadval qatorlari: shartnoma hajmi, avval, shu oy, jami, qoldiq, % va summalar."""
    items = list(items)
    before = done_before(period, items)
    this = {d.item_id: d.qty for d in Done.objects.filter(period=period, item__in=items)}
    later = {r["item_id"]: r["q"] for r in Done.objects.filter(item__in=items).exclude(_before(period))
             .exclude(period=period).values("item_id").annotate(q=Sum("qty"))}
    out = []
    for it in items:
        b, t = before.get(it.pk, 0.0), this.get(it.pk)
        cum = b + (t or 0.0)
        uc = it.unit_cost
        out.append({"item": it, "before": b, "this": t, "cum": cum, "left": it.qty - cum - later.get(it.pk, 0.0),
                    "pct": 100 * cum / it.qty if it.qty else 0.0, "this_amount": (t or 0.0) * uc, "unit_cost": uc})
    return out
