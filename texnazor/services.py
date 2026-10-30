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
from .models import COST_FIELDS, Correction, Done, Item, Period, Section, SiteObject
from .ofyorta import CATEGORY_LABELS, Ofyorta, OfyortaError, parse

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
    obj.contract_totals = _contract([it for _i, it in tops], obj.rates)
    obj.status, obj.error = SiteObject.Status.READY, ""
    obj.progress = {"step": "Тайёр", "done": len(of.rows), "total": len(of.rows)}
    obj.save()


def _contract(items, rates) -> Dict[str, float]:
    """Shartnoma zanjiri: Σ (shartnoma hajmi × 1 birlik narxi) turlar bo'yicha -> svodka foizlari."""
    cat = {}
    for it in items:
        for c, f in COST_FIELDS.items():
            cat[c] = cat.get(c, 0.0) + getattr(it, f) * it.qty
    return calc.chain(cat, rates)


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


# ===================== ofyortaga tuzatish =====================
def correct_item(user, item: Item, period: Optional[Period], text, basis: str, note: str = "") -> Correction:
    """Qatorning shartnoma hajmini asos hujjati bilan o'zgartirish. Bajarilganidan kam qilib bo'lmaydi.

    Bajarilgan hajmlar o'zgarmaydi, shuning uchun yopilgan oylar summasi ham o'zgarmaydi — o'zgaradigani shartnoma
    va qoldiq. Ofyortada hajmi 0 bo'lgan ishning 1 birlik narxi resurslarining me'yoridan tiklanadi (narx × me'yor)."""
    if item.kind not in ITEM_KINDS:
        raise TexnazorError("Ресурс ҳажми алоҳида тузатилмайди — ишнинг ҳажмини тузатинг")
    if period is not None and period.closed:
        raise TexnazorError(f"{period.title} ёпилган — тузатиш очиқ ойдан кучга киради")
    basis = " ".join(str(basis or "").split())
    if len(basis) < 3:
        raise TexnazorError("Асос ҳужжатини ёзинг (масалан: «Қўшимча келишув №2, 12.11.2026»)")
    new = parse_value(text, item.qty)
    if new is None or new < 0:
        raise TexnazorError("Янги ҳажмни ёзинг (0 ёки ундан катта сон)")
    done = Done.objects.filter(item=item).aggregate(q=Sum("qty"))["q"] or 0.0
    if new < done - max(1e-9, abs(done) * 1e-9):
        raise TexnazorError(f"Бажарилган {fmt_qty(done)} {item.unit} — шартнома ҳажмини ундан кам қилиб бўлмайди")
    with transaction.atomic():
        c = Correction.objects.create(obj_id=item.obj_id, item=item, period=period, author=user, old_qty=item.qty,
                                      new_qty=new, basis=basis[:300], note=str(note or "").strip())
        _set_contract_qty(item, new)
    return c


def _set_contract_qty(item: Item, new: float):
    """Qatorning shartnoma hajmi -> resurslari (jami = me'yor × hajm) va obyektning shartnoma zanjiri."""
    res = list(item.resources.all()) if item.kind == Item.Kind.WORK else []
    if res and not item.unit_cost:
        for f in COST_FIELDS.values():
            setattr(item, f, 0.0)
        for r in res:
            f = COST_FIELDS.get(r.category)
            if f:
                setattr(item, f, getattr(item, f) + r.price * r.qty)
    item.qty = new
    if item.kind != Item.Kind.WORK:
        item.total_qty = new
    item.amount = item.unit_cost * new
    item.save()
    for r in res:
        r.total_qty = r.qty * new
        r.amount = r.price * r.total_qty
    Item.objects.bulk_update(res, ["total_qty", "amount"])
    obj = item.obj
    obj.contract_totals = _contract(obj.items.filter(kind__in=ITEM_KINDS), obj.rates or {})
    obj.save(update_fields=["contract_totals"])


REVERT_WORD = "БЕКОР"
DELETE_WORD = "ЎЧИРИШ"


def _confirmed(text, word: str) -> bool:
    return search_key(text or "") == search_key(word)


def revert_correction(user, c: Correction, confirm: str) -> Correction:
    """Tuzatishni bekor qilish (ikkinchi bosqich — «БЕКОР» deb yozish): hajm eskisiga qaytadi, yozuv tarixda qoladi.
    Faqat shu ishning oxirgi amaldagi tuzatishi bekor qilinadi (zanjir buzilmasin)."""
    if not _confirmed(confirm, REVERT_WORD):
        raise TexnazorError(f"Тасдиқлаш учун «{REVERT_WORD}» деб ёзинг")
    if c.reverted:
        raise TexnazorError("Бу тузатиш аллақачон бекор қилинган")
    last = c.item.corrections.filter(reverted__isnull=True).first()
    if last is None or last.pk != c.pk:
        raise TexnazorError("Аввал шу ишнинг кейинги тузатишини бекор қилинг (энг охиргиси биринчи)")
    done = Done.objects.filter(item=c.item).aggregate(q=Sum("qty"))["q"] or 0.0
    if c.old_qty < done - max(1e-9, abs(done) * 1e-9):
        raise TexnazorError(f"Бажарилган {fmt_qty(done)} {c.item.unit} — ҳажмни {fmt_qty(c.old_qty)} га қайтариб бўлмайди. "
                            "Аввал ортиқча бажарилган ҳажмни ўчиринг.")
    with transaction.atomic():
        c.reverted, c.reverted_by = timezone.now(), user
        c.save(update_fields=["reverted", "reverted_by"])
        _set_contract_qty(c.item, c.old_qty)
    return c


def delete_period(period: Period, confirm: str):
    """Oyni o'chirish (ikkinchi bosqich — oy nomini yozish): ichidagi hajmlar ham o'chadi. Yopilgan oy o'chmaydi."""
    if period.closed:
        raise TexnazorError(f"{period.title} ёпилган (тўлов шу бўйича) — ўчириш учун аввал «Қайта очиш»")
    if not _confirmed(confirm, period.title):
        raise TexnazorError(f"Тасдиқлаш учун ой номини ёзинг: «{period.title}»")
    period.delete()


def period_info(period: Period) -> dict:
    """O'chirishdan oldin ko'rsatiladi: nechta qator va qancha summa yo'qoladi."""
    return {"rows": period.done.count(), "sum": period_totals(period)["this"]["contractor"]}


def payments(obj: SiteObject) -> dict:
    """Ф-3 bo'yicha to'langan va tizim hisobi (pudratchi, QQS bilan) oyma-oy; farq + — ortiqcha to'langan."""
    rows = []
    for p in obj.periods.all():
        calc_ = period_totals(p)["this"]["contractor"]
        rows.append({"p": p, "calc": calc_, "paid": p.paid, "diff": (p.paid - calc_) if p.paid is not None else None})
    over = sum(r["diff"] for r in rows if r["diff"] is not None)
    return {"rows": rows, "over": over, "any": any(r["paid"] is not None for r in rows)}


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
        left = it.qty - cum - later.get(it.pk, 0.0)
        uc = it.unit_cost
        # resurslar ishga ergashadi: me'yor × ishning hajmi (Ф-2 dagidek)
        res = [{"r": r, "total": r.total_qty, "before": r.qty * b, "this": r.qty * (t or 0.0),
                "cum": r.qty * cum, "left": r.qty * left,
                "this_amount": r.price * r.qty * (t or 0.0), "cat": CATEGORY_LABELS.get(r.category, ""), "tone": _tone(r)} for r in it.resources.all()] if it.kind == Item.Kind.WORK else []
        out.append({"item": it, "before": b, "this": t, "cum": cum, "left": left, "res": res, "done": it.qty - left,
                    "cat": CATEGORY_LABELS.get(it.category, "") if it.kind == Item.Kind.FREE else "",
                    "pct": 100 * cum / it.qty if it.qty else 0.0, "this_amount": (t or 0.0) * uc, "unit_cost": uc})
    return out


def _tone(r: Item) -> str:
    """Resurs qatorining rangi: чел-ч — иш ҳақи, маш-ч — машина, қолгани — материал/ускуна (бирлик бўйича)."""
    u = r.unit.upper()
    return "j" if "ЧЕЛ" in u else "k" if "МАШ" in u else "m"


def section_tree(obj: SiteObject, stats: Dict[int, dict]) -> list:
    """Mundarija: bloklar («НА АС БЛОК-1», «НА ЭО» — ofyortada qizil) va ularning bo'limlari. Ichida ishi yo'q
    bo'lim (masalan «ПОЛ 1-ЭТАЖ», ostida «ПОЛ ИЗ РЕЛИН …») — oraliq sarlavha. Blok summalari — bo'limlaridan.
    Birinchi blokdan oldingi bo'limlar — nomsiz blokda."""
    blocks = [{"s": None, "label": "", "items": [], "contract": 0.0, "before": 0.0, "this": 0.0}]
    for s in obj.sections.all():
        if _BLOCK.search(s.title):
            blocks.append({"s": s, "label": block_label(s.title), "items": [], "contract": 0.0, "before": 0.0, "this": 0.0})
        elif not s.items_count:
            blocks[-1]["items"].append({"kind": "group", "s": s})
        else:
            st = stats.get(s.pk) or {"contract": 0.0, "before": 0.0, "this": 0.0}
            blocks[-1]["items"].append({"kind": "section", "s": s, **section_pct(st)})
            for k in ("contract", "before", "this"):
                blocks[-1][k] += st[k]
    return [{**b, **section_pct(b)} for b in blocks if b["items"] or b["s"]]


_BLOCK = re.compile(r"^\s*НА\s+|\bБЛОК[\s№-]*\d", re.I)  # «НА АС БЛОК-1», «НА ЭО» (ofyortada qizil) yoki «АС БЛОК-2»
_NA = re.compile(r"^\s*НА\s+", re.I)


def block_label(title: str) -> str:
    """«НА АС БЛОК-1» -> «АС БЛОК-1» (mundarijada «НА» ortiqcha)."""
    return _NA.sub("", title, count=1)


def section_nav(obj: SiteObject, section: Section) -> dict:
    """Bo'lim sahifasi uchun: qaysi blokda, oldingi va keyingi bo'lim (ishi borlari, ofyorta tartibida)."""
    block, prev, found = "", None, None
    for sid, title, n in obj.sections.values_list("id", "title", "items_count"):
        if _BLOCK.search(title):
            if found is None:
                block = block_label(title)
            continue
        if not n:
            continue
        if found is not None:
            return {"block": block, "prev": prev, "next": (sid, title)}
        if sid == section.pk:
            found = sid
        else:
            prev = (sid, title)
    return {"block": block, "prev": prev, "next": None}


def section_paths(obj: SiteObject) -> Dict[int, tuple]:
    """Bo'lim -> (blok, bo'lim) — qidiruv natijasida qator qayerdanligi."""
    out, block = {}, ""
    for sid, title in obj.sections.values_list("id", "title"):
        if _BLOCK.search(title):
            block = block_label(title)
        out[sid] = (block, title)
    return out


_ROW = re.compile(r"^(\d+)-қатор[:\s]*(.*)$", re.S)


def warnings(obj: SiteObject) -> list:
    """Ofyortadagi xatolar — har biri qaysi ishga tegishli (sahifada «Кўрсатиш» shu bo'lim va qatorga olib boradi).
    Eski obyektlarda matn ko'rinishida saqlangan: «8911-қатор …»."""
    out = []
    for w in obj.warnings or []:
        if isinstance(w, str):
            m = _ROW.match(w)
            w = {"row": int(m.group(1)), "title": m.group(2)[:160], "text": ""} if m else {"row": 0, "title": w, "text": ""}
        out.append(dict(w))
    rows = {w["row"] for w in out if w.get("row")}
    found = {it.excel_row: it for it in obj.items.filter(excel_row__in=rows).select_related("parent")}
    for w in out:
        it = found.get(w.get("row"))
        if it is not None and it.kind == Item.Kind.RES and it.parent_id:
            it = it.parent
        w["item"], w["section"] = (it.pk, it.section_id) if it is not None else (None, None)
    fixed = latest_corrections(obj, [w["item"] for w in out if w["item"]])
    for w in out:
        w["fixed"] = fixed.get(w["item"])  # шу иш тузатилган / тасдиқланган бўлса — охирги тузатиш
    return out


def latest_corrections(obj: SiteObject, items=None) -> Dict[int, Correction]:
    """Ish -> uning oxirgi tuzatishi (jadvalda «тузатилган» belgisi, ogohlantirishda «тузатилди»)."""
    qs = obj.corrections.filter(reverted__isnull=True)
    if items is not None:
        qs = qs.filter(item_id__in=items)
    out = {}
    for c in qs:  # yangisi birinchi
        out.setdefault(c.item_id, c)
    return out


def section_pct(st: dict) -> dict:
    cum = st["before"] + st["this"]
    return {**st, "cum": cum, "pct": 100 * cum / st["contract"] if st["contract"] else 0.0}
