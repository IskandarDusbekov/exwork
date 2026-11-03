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

from .models import Act, ActQty, Done, LayoutMemo, RawRow, SiteObject
from .sheet import COL_KEYS, READER, ROW_KEYS, Book, SheetError, hide_ranges, list_sheets, mark_guess, read, sheet_values
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
            if not obj.sheets:
                raise SheetError("Файлда бирорта ҳам лист йўқ")
            if len(obj.sheets) == 1 and not obj.sheets[0]["header"]:  # tanlash yo'q — darhol belgilashga
                obj.sheet = obj.sheets[0]["name"][:255]
                obj.status, obj.error = SiteObject.Status.MARK, NO_HEADER
                obj.save(update_fields=["sheets", "sheet", "status", "error"])
                return obj
            obj.status, obj.error = SiteObject.Status.CHOOSE, ""
            obj.save(update_fields=["sheets", "status", "error"])
            return obj
        try:
            import_raw(obj, xlsx, progress)
        except SheetError as e:  # jadvalni tushunmadi — foydalanuvchi o'zi belgilaydi (to'xtab qolmaydi)
            obj.status, obj.error = SiteObject.Status.MARK, str(e)
            obj.save(update_fields=["status", "error"])
    except Exception as e:  # noqa: BLE001 — sabab sahifada ko'rinadi
        obj.status = SiteObject.Status.FAILED
        obj.error = str(e) if isinstance(e, (SheetError, TexnazorError)) else f"{type(e).__name__}: {e}"
        obj.save(update_fields=["status", "error"])
    return obj


NO_HEADER = "Тизим бу листда офёрта жадвалининг сарлавҳасини («Наименование работ…») топа олмади"


def import_raw(obj: SiteObject, xlsx, progress=None):
    _step(obj, "Офёрта ўқилмоқда", beat=progress)
    manual = (obj.layout or {}).get("manual")  # foydalanuvchi tuzatish oynasida ko'rsatgani (qayta o'qishda saqlanadi)
    memo_used = []

    def memo(sig):
        m = LayoutMemo.objects.filter(owner=obj.owner_id, signature=sig).values_list("mapping", flat=True).first()
        m = {k: v for k, v in (m or {}).items() if k in COL_KEYS}  # eski yozuvdagi «final» — boshqa faylning qatori
        memo_used.append(bool(m))
        return m or None
    grid = read(xlsx, obj.sheet, progress=lambda d, t: _step(obj, "Офёрта ўқилмоқда", d, t, progress),
                manual=manual, memo=memo)
    if manual and manual.get("header") and grid.signature:  # belgilash sahifasidan: ustunlar shu shablonga eslab qolinadi
        cols = {k: manual[k] for k in COL_KEYS if k in manual}
        LayoutMemo.objects.update_or_create(owner=obj.owner, signature=grid.signature, defaults={"mapping": cols})
    _step(obj, "Кўриниши (ранглар, кенгликлар) ўқилмоқда", beat=progress)
    look = read_style(xlsx, obj.sheet, max(grid.layout.width, grid.layout.block_end + 1), len(grid.rows))
    _step(obj, "Базага ёзилмоқда", 0, len(grid.rows), progress)
    with transaction.atomic():
        old_acts = obj.acts.exists()
        obj.raw_rows.all().delete()
        RawRow.objects.bulk_create([RawRow(obj=obj, row=r["row"], kind=r["kind"], num=r["num"][:20], parent=r["parent"],
                                           vals=r["vals"], fmls={str(k): v for k, v in r["fmls"].items()})
                                    for r in grid.rows], batch_size=1000)
        bk = Book(grid.layout.as_dict(), grid.rows)
        _step(obj, "Ҳисоб текширилмоқда", beat=progress)
        check = bk.self_check()  # bo'sh ≈ 0, yarim ≈ 50%, to'liq ≈ fayldagi jami
        obj.layout = dict(grid.layout.as_dict(), stamp=time.time(), reader=READER, signature=grid.signature,
                          manual=grid.manual, memo=any(memo_used),
                          check={k: v for k, v in check.items() if k != "fails"} | {"fails": check["fails"]})
        obj.warnings = [dict(w, level="error") for w in check["fails"]] + grid.warnings + bk.total_mismatch() + bk.fixed_money()
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
LAYOUT_META = ("stamp", "reader", "check", "signature", "manual", "memo")  # ``obj.layout`` dagi ``Layout`` maydoni bo'lmaganlar
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
    lay = {k: v for k, v in (obj.layout or {}).items() if k not in LAYOUT_META}
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
    return b.grand(ev)


def totals(obj: SiteObject) -> dict:
    """Shartnoma (to'liq hajm bilan qayta hisoblangan «ВСЕГО» — dalolatnomalar bilan bitta hisob, 100% da qoldiq
    aniq 0), har dalolatnoma, jami bajarilgan va qoldiq — so'mda."""
    b = book(obj)
    fc = b.final_cell()
    contract = b.contract_total()
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


# ===================== tuzatish oynasi: ustunlar va jami qatorni foydalanuvchi ko'rsatadi =====================
def columns(obj: SiteObject) -> List[dict]:
    """Tanlash uchun ustunlar: harfi va sarlavhadagi matni («E · Количество / на ед. измерения»)."""
    from .formula import col_letter
    b = book(obj)
    lay = b.lay
    heads = [r["vals"] for r in b.rows if lay.header <= r["row"] <= lay.header_end]
    out = []
    for j in range(max(lay.width, lay.block_end + 1)):
        words = [" ".join(str(v[j]).split()) for v in heads if j < len(v) and v[j] not in (None, "")]
        out.append({"i": j, "letter": col_letter(j), "label": " / ".join(dict.fromkeys(words))[:60]})
    return out


def parse_manual(data, obj: SiteObject) -> dict:
    """Formadan: ustun indekslari (0 dan) va jami qator; noto'g'risi — ``TexnazorError``."""
    b = book(obj)
    width = max(b.lay.width, b.lay.block_end + 1)
    out = {}
    for k in ("qty", "total", "price", "amount", "final"):
        raw = str(data.get(k, "")).strip()
        if raw == "":
            continue
        try:
            v = int(raw)
        except ValueError:
            raise TexnazorError("Устун ёки қатор нотўғри")
        if k == "final":
            if v and v not in b.by_row:
                raise TexnazorError(f"{v}-қатор листда йўқ")
            if v:
                out[k] = v
        elif 0 <= v < width:
            out[k] = v
        else:
            raise TexnazorError("Устун нотўғри")
    if "qty" in out and "amount" in out and out["qty"] == out["amount"]:
        raise TexnazorError("Ҳажм ва сумма бир устун бўлолмайди")
    return out


def preview_check(obj: SiteObject, manual: dict) -> dict:
    """Saqlamasdan sinab ko'rish: saqlangan qatorlar ustida shu ustunlar bilan o'z-o'zini tekshirish (tez, taxminiy —
    saqlangach fayl to'liq qayta o'qiladi)."""
    b = book(obj)
    lay = dict(b.lay.as_dict(), final=0) | manual
    return Book(lay, b.rows).self_check()


def set_manual(obj: SiteObject, manual: dict):
    """Saqlash: shu obyekt qayta o'qiladi (hajmlar saqlanadi) va shablon eslab qolinadi (keyingi shunday faylga)."""
    sig = (obj.layout or {}).get("signature")
    obj.layout = dict(obj.layout or {}, manual=manual or None)
    obj.save(update_fields=["layout"])
    if sig:
        cols = {k: v for k, v in (manual or {}).items() if k in COL_KEYS}  # qatorlar va yig'ilganlar — faqat shu faylniki
        if cols:
            LayoutMemo.objects.update_or_create(owner=obj.owner, signature=sig, defaults={"mapping": cols})
        else:
            LayoutMemo.objects.filter(owner=obj.owner, signature=sig).delete()
    reread(obj)


# ===================== qo'lda belgilash: sarlavha, ishlar boshi / oxiri, ustunlar =====================
MARK_PAGE = 50
_peek: Dict[tuple, List[tuple]] = {}


def _values(obj: SiteObject) -> List[tuple]:
    """Listning qiymatlari (xotirada — sahifa bir necha marta so'raydi; katta listni har safar ochmaslik uchun)."""
    x = _xlsx(obj)
    key = (str(x), x.stat().st_mtime, obj.sheet)
    with _lock:
        hit = _peek.get(key)
    if hit is None:
        hit = sheet_values(x, obj.sheet)
        with _lock:
            if len(_peek) > 4:
                _peek.clear()
            _peek[key] = hit
    return hit


def _look(obj: SiteObject, n: int, w: int) -> dict:
    """Belgilash jadvali Excel'dagidek ko'rinishi uchun listning uslublari (xotirada; o'qib bo'lmasa — oddiy jadval)."""
    x = _xlsx(obj)
    key = ("look", str(x), x.stat().st_mtime, obj.sheet, w)
    with _lock:
        hit = _peek.get(key)
    if hit is None:
        try:
            hit = read_style(x, obj.sheet, w, n)
        except Exception:  # noqa: BLE001 — ko'rinish ixtiyoriy, belgilashga xalaqit bermasin
            hit = {}
        with _lock:
            _peek[key] = hit
    return hit


def _width(rows: List[tuple]) -> int:
    return max((max((j for j, v in enumerate(r) if v is not None), default=-1) for r in rows), default=-1) + 1


def _short(v):
    return v[:120] if isinstance(v, str) else v


def mark_rows(obj: SiteObject, start: int, count: int = MARK_PAGE) -> List[list]:
    """[[qator (1 dan), [qiymatlar]], …] — ``start`` (0 dan) dan ``count`` ta."""
    rows = _values(obj)
    w = _width(rows)
    return [[i + 1, [_short(v) for v in rows[i][:w]]] for i in range(max(0, start), min(start + count, len(rows)))]


def mark_data(obj: SiteObject) -> dict:
    """Belgilash sahifasi: listning boshi va oxiri (50 tadan), ustun harflari, tizim topgani va avval belgilangani."""
    from .formula import col_letter
    rows = _values(obj)
    n, w = len(rows), _width(rows)
    lay = obj.layout or {}
    marks = lay.get("manual") or {}
    if not marks.get("header") and obj.raw_rows.exists():  # o'qilgan obyekt: hozir qanday tushunilgani
        marks = {k: lay.get(k, 0) for k in COL_KEYS + ROW_KEYS} | {k: lay.get(k) or [] for k in KEEP_KEYS}
        kinds = list(obj.raw_rows.filter(kind__in=("work", "free", "res")).values_list("row", flat=True))
        marks["start"] = marks["start"] or min(kinds, default=0)  # oxirgi ish — tizim o'zi topadi (svodkalar bilan)
    return {"total": n, "width": w, "letters": [col_letter(j) for j in range(w)], "page": MARK_PAGE,
            "top": mark_rows(obj, 0), "tail": mark_rows(obj, max(0, n - MARK_PAGE)),
            "guess": mark_guess(rows), "marks": marks, "sheet": obj.sheet, "look": _look(obj, n, w)}


def parse_marks(data, obj: SiteObject) -> dict:
    """Formadan: qatorlar (1 dan) va ustunlar (0 dan); noto'g'risi — ``TexnazorError`` (tushunarli matn bilan)."""
    rows = _values(obj)
    n, w = len(rows), _width(rows)

    def num(k):
        raw = str(data.get(k, "")).strip()
        if raw == "":
            return None
        try:
            return int(raw)
        except ValueError:
            raise TexnazorError("Қатор ёки устун нотўғри")
    out = {}
    for k in ROW_KEYS:
        v = num(k)
        if v:
            if not 1 <= v <= n:
                raise TexnazorError(f"{v}-қатор листда йўқ (листда {n} та қатор)")
            out[k] = v
    for k in COL_KEYS:
        v = num(k)
        if v is not None and v >= 0:
            if v >= max(w, 1):
                raise TexnazorError("Устун нотўғри")
            out[k] = v
    need = [("header", "сарлавҳа қаторини"), ("start", "биринчи иш қаторини"), ("name", "«Номи» устунини"),
            ("qty", "«Ҳажм» устунини"), ("amount", "«Сумма» устунини")]
    miss = [t for k, t in need if k not in out]
    if miss:
        raise TexnazorError("Белгиланг: " + ", ".join(miss))
    out.setdefault("num", 0)
    out["header_end"] = max(out.get("header_end", out["header"]), out["header"])
    if out["header_end"] - out["header"] > 6:
        raise TexnazorError("Сарлавҳа 7 қатордан узун бўлмайди — сарлавҳанинг биринчи ва охирги қаторини босинг")
    if out["start"] <= out["header_end"]:
        raise TexnazorError("Биринчи иш сарлавҳадан пастда бўлиши керак")
    if out.get("end") and out["end"] < out["start"]:
        raise TexnazorError("Охирги иш биринчи ишдан юқорида бўлолмайди")
    if out.get("final") and out["final"] < out["start"]:
        raise TexnazorError("Жами қатори ишлардан пастда бўлиши керак")
    used = [out[k] for k in ("num", "name", "qty", "amount")]
    if len(set(used)) < len(used):
        raise TexnazorError("№, Номи, Ҳажм ва Сумма — тўрт хил устун бўлиши керак")
    old = (obj.layout or {}).get("manual") or {}
    for k in ("hide", "svod"):  # yig'ilganlar va «сводка» deganlar saqlanadi
        v = hide_ranges(old.get(k) or (obj.layout or {}).get(k), out["header_end"])
        if v:
            out[k] = v
    for k in ("hide_cols", "prop_res", "relink"):
        if old.get(k):
            out[k] = old[k]
    return out


def set_marks(obj: SiteObject, marks: dict):
    """Saqlash: list belgilanganidek qayta o'qiladi (fonda); yozilgan hajmlar saqlanadi (Excel qatori bo'yicha)."""
    from . import tasks
    obj.layout = dict(obj.layout or {}, manual=marks)
    obj.status, obj.error = SiteObject.Status.PARSING, ""
    obj.progress = {"step": "Офёрта белгиланганидек ўқилмоқда"}
    obj.save(update_fields=["layout", "status", "error", "progress"])
    tasks.start(obj.pk)


def retry(obj: SiteObject):
    """Xatodan keyin qayta urinish: list tanlangan bo'lsa — o'qish, bo'lmasa — listlarni qaytadan ko'rish."""
    from . import tasks
    obj.status, obj.error = SiteObject.Status.PARSING, ""
    obj.progress = {"step": "Қайта уринилмоқда"}
    obj.save(update_fields=["status", "error", "progress"])
    tasks.start(obj.pk)


# foydalanuvchi belgilari — qayta belgilash / «avtomatikka qaytarish» da saqlanadi (faqat shu faylniki)
KEEP_KEYS = ("hide", "svod", "hide_cols", "prop_res", "relink")


def set_hide(obj: SiteObject, add: Optional[List[int]] = None, remove: Optional[int] = None, key: str = "hide") -> int:
    """Qatorlar oralig'i: ``hide`` — «keraksiz» (ish ham, svodka ham emas, saytda va Excel'da yig'ilgan);
    ``svod`` — «сводка» (bo'lim jami: ish emas, formulasi hisoblanadi). Fayl qayta o'qiladi (hajmlar saqlanadi).
    Qaytaradi: shunday qatorlar soni."""
    assert key in ("hide", "svod")
    lay = obj.layout or {}
    manual = dict(lay.get("manual") or {})
    cur = [list(x) for x in (manual.get(key) or lay.get(key) or [])]
    if add:
        a, b = min(add), max(add)
        last = obj.raw_rows.aggregate(m=Max("row"))["m"] or 0
        if a <= lay.get("header_end", 0):
            raise TexnazorError("Сарлавҳа қаторларини йиғиб бўлмайди" if key == "hide" else "Сарлавҳа қатори сводка бўлолмайди")
        if b > last:
            raise TexnazorError(f"Листда {last} та қатор бор")
        done = ActQty.objects.filter(act__obj=obj, row__gte=a, row__lte=b).exclude(qty=0).values_list("row", flat=True).first()
        if done:
            raise TexnazorError(f"{done}-қаторга далолатномада ҳажм ёзилган — аввал уни ўчиринг")
        cur.append([a, b])
    if remove:
        cur = [x for x in cur if not x[0] <= remove <= x[1]]
    manual[key] = hide_ranges(cur, lay.get("header_end", 0))
    set_marks(obj, manual)
    return sum(b - a + 1 for a, b in manual[key])


def set_hide_col(obj: SiteObject, col: int, on: bool) -> List[int]:
    """Keraksiz ustunni yig'ish / ochish — faqat ko'rinish (saytda va Excel hisobotda); fayl qayta o'qilmaydi."""
    lay = dict(obj.layout or {})
    if not 0 <= col < max(lay.get("width", 0), lay.get("block_end", 0) + 1):
        raise TexnazorError("Устун нотўғри")
    if on and col == lay.get("qty"):
        raise TexnazorError("Ҳажм устунини йиғиб бўлмайди — далолатнома шунга ёзилади")
    manual = dict(lay.get("manual") or {})
    cols = set(manual.get("hide_cols") or lay.get("hide_cols") or [])
    cols = sorted(cols | {col} if on else cols - {col})
    manual["hide_cols"] = cols
    lay.update(manual=manual, hide_cols=cols)
    obj.layout = lay
    obj.save(update_fields=["layout"])
    return cols


def set_prop_res(obj: SiteObject, on: bool, key: str = "prop_res"):
    """Boshqa qatordagi hajmga ko'paytirilgan resurslar (foydalanuvchi tasdig'i bilan, bittasi tanlanadi):
    «prop_res» — dalolatnomada o'z ishi hajmiga mutanosib, ofyorta jami fayldagidek; «relink» — formulaning o'zi
    tuzatiladi (ofyorta jami ham o'zgaradi). Fayl qayta o'qiladi (hajmlar saqlanadi)."""
    if key not in ("prop_res", "relink"):
        raise TexnazorError("Тузатиш тури нотўғри")
    manual = dict((obj.layout or {}).get("manual") or {})
    manual.pop("prop_res", None)
    manual.pop("relink", None)
    if on:
        manual[key] = True
    obj.layout = dict(obj.layout or {}, manual=manual or None)
    obj.save(update_fields=["layout"])
    reread(obj)
