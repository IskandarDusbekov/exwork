"""Texnazor — dalolatnomalar tartibi: ofyorta listi fayldagidek saqlanadi (``RawRow``), ish hajmlari №1, №2 …
dalolatnomalarga yoziladi, har bir dalolatnoma (va «Остатка») faylning o'z formulalari bilan hisoblanadi (``sheet.Book``).

Yuklash: fayl -> listlar ko'rib chiqiladi (``list_sheets``) -> foydalanuvchi listni tasdiqlaydi / o'zgartiradi ->
list o'qiladi (``import_raw``). Eski (oylar) obyekti shu yerda dalolatnomalarga o'tadi: oylar sana tartibida №1, №2 …
"""
import datetime as dt
import json
import threading
import time
from typing import Dict, List, Optional

from django.db import transaction
from django.db.models import Max

from core import convert
from core.convert import check_xlsx

from .models import Act, ActQty, Done, RawRow, SiteObject
from .sheet import READER, Book, SheetError, list_sheets, read
from .sheetstyle import VERSION as STYLE_VERSION, read_style
from .services import TexnazorError


def _step(obj: SiteObject, text: str, done=0, total=0, beat=None):
    obj.progress = {"step": text, "done": done, "total": total}
    SiteObject.objects.filter(pk=obj.pk).update(progress=obj.progress)
    if beat:
        beat()


def _xlsx(obj: SiteObject):
    xlsx = convert.ensure_xlsx(obj.abs(obj.source_path), obj.abs("conv"))
    check_xlsx(xlsx)
    return xlsx


def prepare(obj: SiteObject, progress=None):
    """Fonda: list hali tanlanmagan bo'lsa — listlarni ko'rib, tanlashga qo'yadi; tanlangan bo'lsa — o'qiydi."""
    try:
        _step(obj, "Файл очилмоқда", beat=progress)
        xlsx = _xlsx(obj)
        if not obj.sheet:
            _step(obj, "Листлар кўрилмоқда", beat=progress)
            obj.sheets = list_sheets(xlsx)
            if not any(s["header"] for s in obj.sheets):
                raise SheetError("Файлнинг бирорта листида офёрта жадвали («Наименование работ…» сарлавҳали) топилмади")
            obj.status, obj.error = SiteObject.Status.CHOOSE, ""
            obj.save(update_fields=["sheets", "status", "error"])
            return obj
        import_raw(obj, xlsx, progress)
    except Exception as e:  # noqa: BLE001 — sabab sahifada ko'rinadi
        obj.status = SiteObject.Status.FAILED
        obj.error = str(e) if isinstance(e, (SheetError, TexnazorError)) else f"{type(e).__name__}: {e}"
        obj.save(update_fields=["status", "error"])
    return obj


def import_raw(obj: SiteObject, xlsx, progress=None):
    _step(obj, "Офёрта ўқилмоқда", beat=progress)
    grid = read(xlsx, obj.sheet, progress=lambda d, t: _step(obj, "Офёрта ўқилмоқда", d, t, progress))
    _step(obj, "Кўриниши (ранглар, кенгликлар) ўқилмоқда", beat=progress)
    look = read_style(xlsx, obj.sheet, max(grid.layout.width, grid.layout.block_end + 1), len(grid.rows))
    _step(obj, "Базага ёзилмоқда", 0, len(grid.rows), progress)
    with transaction.atomic():
        old_acts = obj.acts.exists()
        obj.raw_rows.all().delete()
        RawRow.objects.bulk_create([RawRow(obj=obj, row=r["row"], kind=r["kind"], num=r["num"][:20], parent=r["parent"],
                                           vals=r["vals"], fmls={str(k): v for k, v in r["fmls"].items()})
                                    for r in grid.rows], batch_size=1000)
        obj.layout = dict(grid.layout.as_dict(), stamp=time.time(), reader=READER)
        obj.warnings = grid.warnings + Book(grid.layout.as_dict(), grid.rows).fixed_money()
        if obj.mode != SiteObject.Mode.ACTS or (not old_acts and obj.periods.exists()):
            obj.warnings += _from_months(obj, grid)
        obj.mode = SiteObject.Mode.ACTS
        if grid.title and obj.name == obj.source_name.rsplit(".", 1)[0][:300]:
            obj.name = grid.title[:300]
        obj.status, obj.error = SiteObject.Status.READY, ""
        obj.progress = {"step": "Тайёр", "done": len(grid.rows), "total": len(grid.rows)}
        obj.save()
    _save_style(obj, look)
    return obj


# ===================== ko'rinish (Excel uslublari) =====================
def _style_path(obj: SiteObject):
    return obj.abs("style.json")


def _save_style(obj: SiteObject, look: dict):
    look = dict(look, stamp=(obj.layout or {}).get("stamp"))
    p = _style_path(obj)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(look, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(p)


def look(obj: SiteObject) -> dict:
    """Listning Excel'dagi ko'rinishi (``sheetstyle``); fayl yo'q yoki eski bo'lsa — qayta o'qiladi."""
    p, stamp = _style_path(obj), (obj.layout or {}).get("stamp")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("v") == STYLE_VERSION and d.get("stamp") == stamp:
            return d
    except (OSError, ValueError):
        pass
    lay = obj.layout or {}
    d = read_style(_xlsx(obj), obj.sheet, max(lay.get("width", 12), lay.get("block_end", 11) + 1),
                   obj.raw_rows.aggregate(m=Max("row"))["m"] or 0)
    _save_style(obj, d)
    return dict(d, stamp=stamp)


def stale(obj: SiteObject) -> bool:
    """Eski qoidalar bilan o'qilgan list (masalan, svodkasi topilmagan) — qayta o'qish kerak."""
    return obj.status == SiteObject.Status.READY and obj.mode == SiteObject.Mode.ACTS and obj.sheet and         (obj.layout or {}).get("reader") != READER


def reread(obj: SiteObject):
    """Fonda qayta o'qish: dalolatnomalar va yozilgan hajmlar saqlanadi (Excel qatori bo'yicha)."""
    from . import tasks
    obj.status, obj.error = SiteObject.Status.PARSING, ""
    obj.progress = {"step": "Офёрта янги қоидалар билан қайта ўқилмоқда"}
    obj.save(update_fields=["status", "error", "progress"])
    tasks.start(obj.pk)


def _from_months(obj: SiteObject, grid) -> List[dict]:
    """Eski oylar -> №1, №2 … (sana tartibida, sanasi — oyning 1-kuni, yopilgani yopiq qoladi). Hajm ish qatoriga
    (Excel qatori bo'yicha) yoziladi; svodkadagi qo'shimcha qatorlar (eski «qo'shimcha») ko'chmaydi — ogohlantirish."""
    obj.acts.all().delete()
    tops = {r["row"] for r in grid.rows if r["kind"] in ("work", "free")}
    lost, warn = 0, []
    for n, p in enumerate(obj.periods.order_by("year", "month"), start=1):
        act = Act.objects.create(obj=obj, number=n, date=dt.date(p.year, p.month, 1), closed=p.closed)
        qs = []
        for row, qty in Done.objects.filter(period=p).values_list("item__excel_row", "qty"):
            if row in tops and qty:
                qs.append(ActQty(act=act, row=row, qty=qty))
            elif qty:
                lost += 1
        ActQty.objects.bulk_create(qs)
    if lost:
        warn.append({"row": 0, "title": f"{lost} та ёзилган ҳажм далолатномага ўтмади",
                     "text": "Улар офёртада иш қатори эмас (масалан, сводкадаги қўшимча қатор). Керак бўлса, қайта киритинг."})
    if obj.corrections.filter(reverted__isnull=True).exists():
        warn.append({"row": 0, "title": "Эски тузатишлар ҳисобга олинмади",
                     "text": "Офёрта энди файлдагидек ўқилади — шартнома ҳажмлари файлдаги қийматлар."})
    return warn


# ===================== hisob =====================
_books: Dict[str, tuple] = {}
_lock = threading.Lock()


def book(obj: SiteObject) -> Book:
    """Listni xotirada saqlaydi (formulalar bir marta kompilyatsiya qilinadi); qayta o'qilsa — yangilanadi."""
    key, stamp = str(obj.pk), (obj.layout or {}).get("stamp")
    with _lock:
        hit = _books.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
    rows = [{"row": r.row, "kind": r.kind, "num": r.num, "parent": r.parent, "vals": r.vals,
             "fmls": {int(k): v for k, v in r.fmls.items()}} for r in obj.raw_rows.all()]
    lay = {k: v for k, v in (obj.layout or {}).items() if k not in ("stamp", "reader")}
    b = Book(lay, rows)
    with _lock:
        if len(_books) > 20:
            _books.clear()
        _books[key] = (stamp, b)
    return b


def acts(obj: SiteObject) -> List[Act]:
    return list(obj.acts.all())


def qty_of(act: Act) -> Dict[int, float]:
    return dict(act.qtys.values_list("row", "qty"))


def all_qty(obj: SiteObject) -> Dict[int, Dict[int, float]]:
    out: Dict[int, Dict[int, float]] = {}
    for act_id, row, qty in ActQty.objects.filter(act__obj=obj).values_list("act_id", "row", "qty"):
        out.setdefault(act_id, {})[row] = qty
    return out


def rest_qty(b: Book, per_act: Dict[int, Dict[int, float]]) -> Dict[int, float]:
    """«Остатка»: shartnoma hajmi − hamma dalolatnomalar."""
    done: Dict[int, float] = {}
    for q in per_act.values():
        for row, v in q.items():
            done[row] = done.get(row, 0.0) + v
    out = {}
    for row in b.tops:
        base = b.contract_qty(row)
        v = base - done.get(row, 0.0)
        out[row] = 0.0 if abs(v) <= 1e-9 * max(1.0, abs(base)) else v  # 100% o'tkazilgan — qoldiq aniq 0
    return out


def act_rows(b: Book, qty: Dict[int, float], rows) -> List[int]:
    """Dalolatnoma blokida chiqadigan qatorlar: hajmi yozilgan ishlar, ularning resurslari va svodka (hajm o'chirilsa — bo'sh)."""
    svod = set(b.svod_rows)
    out = []
    for r in rows:
        info = b.by_row.get(r)
        top = info and (info["parent"] if info["kind"] == "res" else r)
        if r in svod or (top is not None and qty.get(top)):
            out.append(r)
    return out


def rest_rows(b: Book, rest: Dict[int, float], rows=None) -> List[int]:
    """«Остатка» blokida chiqadigan qatorlar: to'liq bajarilgan ish (qoldiq 0) va uning resurslari — bo'sh qoladi."""
    out = []
    for r in (rows if rows is not None else b.calc_rows):
        info = b.by_row.get(r)
        top = info and (info["parent"] if info["kind"] == "res" else r if info["kind"] in ("work", "free") else None)
        if top is None or rest.get(top, 0.0) != 0.0:
            out.append(r)
    return out


def _final(b: Book, ev) -> float:
    fc = b.final_cell()
    v = ev(*fc) if fc else None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def totals(obj: SiteObject) -> dict:
    """Shartnoma (fayldagi oxirgi «ВСЕГО»), har dalolatnoma, jami bajarilgan va qoldiq — so'mda."""
    b = book(obj)
    fc = b.final_cell()
    contract = b.values.get(fc) if fc else 0.0
    if not isinstance(contract, (int, float)) or isinstance(contract, bool):  # keshda yo'q — formulalar bilan
        contract = _final(b, b.evaluate({r: b.contract_qty(r) for r in b.tops}))
    per = all_qty(obj)
    rows = []
    for a in obj.acts.all():
        rows.append({"act": a, "sum": _final(b, b.evaluate(per.get(a.pk, {})))})
    done = sum(r["sum"] for r in rows)
    label = ""
    if fc:
        r = b.by_row.get(fc[1])
        label = next((str(v) for v in (r["vals"] if r else []) if isinstance(v, str) and v.strip()), "")
    return {"contract": contract, "acts": rows, "done": done, "left": contract - done,
            "pct": 100 * done / contract if contract else 0.0, "label": label.strip()}


# ===================== dalolatnomalar =====================
def add_act(obj: SiteObject, date: Optional[dt.date] = None) -> Act:
    n = (obj.acts.aggregate(m=Max("number"))["m"] or 0) + 1
    return Act.objects.create(obj=obj, number=n, date=date)


def delete_act(act: Act):
    """Ochiq dalolatnoma o'chadi; keyingilari bittaga suriladi (№4 → №3), raqamlar ketma-ket qoladi.
    Keyingisi yopilgan bo'lsa — o'chmaydi (yopilganning raqami o'zgarmasin)."""
    if act.closed:
        raise TexnazorError("Ёпилган далолатномани ўчириб бўлмайди — аввал қайта очинг")
    later = list(act.obj.acts.filter(number__gt=act.number).order_by("number"))
    if any(a.closed for a in later):
        raise TexnazorError(f"{act.title} дан кейинги ёпилган далолатнома бор — унинг рақами ўзгармаслиги учун "
                            "аввал уни очинг ёки охиридан бошлаб ўчиринг")
    with transaction.atomic():
        act.delete()
        for a in later:  # o'sishi tartibida: pastdagi raqam allaqachon bo'sh
            a.number -= 1
            a.save(update_fields=["number"])


def rename_act(act: Act, name: str):
    act.name = " ".join(str(name or "").split())[:120]
    act.save(update_fields=["name"])


def parse_qty(text, base: float = 0.0) -> Optional[float]:
    """«0,08», «12» yoki «60%» (ofyortadagi hajmdan). Bo'sh — 0 (yozuv o'chadi); noto'g'ri — ``None``."""
    s = str(text or "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not s:
        return 0.0
    pct = s.endswith("%")
    try:
        v = float(s.rstrip("%"))
    except ValueError:
        return None
    if pct:
        v = base * v / 100
    return v if v == v and abs(v) < 1e15 else None


def set_qty(act: Act, row: int, qty: float):
    if act.closed:
        raise TexnazorError(f"{act.title} ёпилган — ҳажмни ўзгартириш учун уни қайта очинг")
    b = book(act.obj)
    if row not in b.by_row or b.by_row[row]["kind"] not in ("work", "free"):
        raise TexnazorError("Бу қатор иш эмас — ҳажм фақат иш ва алоҳида ресурс қаторига ёзилади")
    if qty:
        base = b.contract_qty(row)
        others = sum(ActQty.objects.filter(act__obj=act.obj, row=row).exclude(act=act).values_list("qty", flat=True))
        left = base - others
        if base and (qty * base < 0 or abs(qty) > abs(left) + max(1e-9, abs(base) * 1e-9)):
            raise TexnazorError(f"Қолдиқ {_q(left)} — {_q(qty)} ёзиб бўлмайди (офёртада {_q(base)}, бошқа далолатномаларда {_q(others)})")
        ActQty.objects.update_or_create(act=act, row=row, defaults={"qty": qty})
    else:
        ActQty.objects.filter(act=act, row=row).delete()


def _q(v: float) -> str:
    return f"{v:,.6f}".rstrip("0").rstrip(".").replace(",", " ").replace(".", ",")


def fill(act: Act, rows: List[int], pct: float) -> int:
    """Belgilangan ishlar jami ``pct`` % bajarilgan bo'lsin: yetmagan qismi shu dalolatnomaga yoziladi (oldingi
    dalolatnomalar o'zgarmaydi, keyingilari bilan birga ofyortadagi hajmdan oshmaydi). Nechta qator ko'rib chiqildi."""
    if act.closed:
        raise TexnazorError(f"{act.title} ёпилган — ҳажмни ўзгартириш учун уни қайта очинг")
    pct = max(0.0, min(100.0, float(pct)))
    b = book(act.obj)
    rows = [r for r in dict.fromkeys(rows) if r in b.by_row and b.by_row[r]["kind"] in ("work", "free")]
    before, after = {}, {}
    for row, qty, n in (ActQty.objects.filter(act__obj=act.obj, row__in=rows).exclude(act=act)
                        .values_list("row", "qty", "act__number")):
        side = before if n < act.number else after
        side[row] = side.get(row, 0.0) + qty
    with transaction.atomic():
        for row in rows:
            base = b.contract_qty(row)
            sg = -1 if base < 0 else 1  # ayiriladigan (manfiy) qator — hamma hajmlari manfiy
            want = base * pct / 100 - before.get(row, 0.0)
            want = sg * min(max(sg * want, 0.0), sg * (base - before.get(row, 0.0) - after.get(row, 0.0)))
            if abs(want) > 1e-12:
                ActQty.objects.update_or_create(act=act, row=row, defaults={"qty": want})
            else:
                ActQty.objects.filter(act=act, row=row).delete()
    return len(rows)


def changed_rows(b: Book, row: int) -> List[int]:
    """Hajm o'zgarsa qayta chiziladigan qatorlar: ishning o'zi, resurslari va svodka."""
    return [row] + b.children.get(row, []) + b.svod_rows


def change_set(obj: SiteObject, act: Act, row: int) -> dict:
    """Hajm saqlangach brauzerga: shu dalolatnoma va «Остатка» bloklarining o'zgargan qatorlari va jami summalar."""
    b = book(obj)
    per = all_qty(obj)
    rows = changed_rows(b, row)
    q = per.get(act.pk, {})
    ev = b.evaluate(q)
    rq = rest_qty(b, per)
    rest = b.evaluate(rq)
    return {"act": act.number, "rows": rows, "v": b.block(ev, act_rows(b, q, rows)), "rest": b.block(rest, rest_rows(b, rq, rows)),
            "qty": per.get(act.pk, {}).get(row, 0.0), "sum": _final(b, ev), "rest_sum": _final(b, rest),
            "done": {row: sum(q.get(row, 0.0) for q in per.values())}}
