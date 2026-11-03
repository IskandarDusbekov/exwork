"""Texnazor — dalolatnomalar tartibi sahifalari: list tanlash, ofyorta «fayldagidek», №1, №2 … va «Остатка»,
hajm yozish, Excel («Хисобот» va Ф-2 №N)."""
import datetime as dt
import hashlib
import json
import re
import time

from django.contrib import messages
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST

from . import services_acts as sa
from . import services, tasks
from .formula import col_letter
from .sheet import list_sheets
from .models import Act, SiteObject
from .services import TexnazorError
from .views import _obj, audit


def _to(o, view=None):
    url = redirect("texnazor:object", obj_id=o.pk).url
    return redirect(url + (f"?view={view}" if view else ""))


def choose(request, o: SiteObject):
    """Yuklangan fayldagi listlar: tizim taklif qilgani belgilangan, foydalanuvchi tasdiqlaydi yoki boshqasini tanlaydi."""
    guess = next((s["name"] for s in o.sheets if s.get("guess")), "")
    if o.sheets and any(s.get("header") and "res" not in s for s in o.sheets):  # eski yuklash — batafsil ma'lumotsiz
        try:
            o.sheets = list_sheets(sa._xlsx(o))
            o.save(update_fields=["sheets"])
        except Exception:  # noqa: BLE001 — sahifa eski ma'lumot bilan ham ochiladi
            pass
    return render(request, "texnazor/choose.html", {"nav": "texnazor", "o": o, "sheets": o.sheets, "guess": guess})


@require_POST
def choose_sheet(request, obj_id):
    o = _obj(request, obj_id)
    name = request.POST.get("sheet", "")
    if o.status != SiteObject.Status.CHOOSE or not any(s["name"] == name for s in o.sheets):
        messages.error(request, "Листни танланг")
        return _to(o)
    header = next(s["header"] for s in o.sheets if s["name"] == name)
    if o.sheet != name and (o.layout or {}).get("manual"):  # boshqa list — oldingi listda belgilangani unga to'g'ri kelmaydi
        o.layout = dict(o.layout, manual=None)
    o.sheet, o.error = name[:255], ""
    # sarlavhasi topilmagan list — o'qishga urinmay, darhol foydalanuvchi belgilaydi
    o.status = SiteObject.Status.PARSING if header else SiteObject.Status.MARK
    o.error = "" if header else sa.NO_HEADER
    o.progress = {"step": "Офёрта ўқилмоқда"}
    o.save(update_fields=["sheet", "status", "error", "progress", "layout"])
    audit(request, o, f"офёрта листи танланди: «{name}»")
    if header:
        tasks.start(o.pk)
    return _to(o)


@require_POST
def change_sheet(request, obj_id):
    """Boshqa listni tanlash — faqat hali hajm yozilmagan bo'lsa."""
    o = _obj(request, obj_id)
    if sa.ActQty.objects.filter(act__obj=o).exists():
        messages.error(request, "Далолатномаларда ҳажм ёзилган — листни алмаштириб бўлмайди")
        return _to(o)
    if not o.sheets:
        o.sheet, o.status = "", SiteObject.Status.PARSING
        o.save(update_fields=["sheet", "status"])
        tasks.start(o.pk)
        return _to(o)
    o.status = SiteObject.Status.CHOOSE
    o.save(update_fields=["status"])
    return _to(o)


def detail(request, o: SiteObject):
    if o.status == SiteObject.Status.CHOOSE:
        return choose(request, o)
    if o.status == SiteObject.Status.MARK:
        return redirect("texnazor:mark", obj_id=o.pk)
    if sa.stale(o):
        sa.reread(o)
        return _to(o)
    acts = sa.acts(o)
    # kirganda — «Далолатнома»: oxirgi ochiq dalolatnoma va Остатка; «Хисобот» — hammasi, oxirida Остатка
    view = request.GET.get("view") if request.GET.get("view") in ("ofyorta", "act", "report") else None
    view = view or ("act" if acts else "ofyorta")
    t = sa.totals(o)
    has_qty = sa.ActQty.objects.filter(act__obj=o).exists()
    return render(request, "texnazor/acts.html", {
        "nav": "texnazor", "o": o, "acts": acts, "totals": t, "view": view, "blk": view != "ofyorta" and bool(acts),
        "has_qty": has_qty,
        "warnings": [w for w in (o.warnings or []) if w.get("level") != "error"], "check": (o.layout or {}).get("check"),
        "prop_fix": next((w for w in (o.warnings or []) if w.get("fix") == "prop_res"), None),
        "xlsx_est": _xlsx_est(o, "report", len(acts)), "f2_est": _xlsx_est(o, "f2", len(acts)),
        "fix": _fix_ctx(o),
        "today": dt.date.today(),
        "info": services.f3_settings(o), "info_missing": services.f3_missing(o),
        "active": next((a for a in reversed(acts) if not a.closed), None)})


def _fix_ctx(o: SiteObject) -> dict:
    """Tuzatish oynasi: ustunlar ro'yxati, hozirgi tanlov (tizim topgani yoki qo'lda ko'rsatilgan)."""
    lay = o.layout or {}
    fields = [("qty", "Ҳажм (иш миқдори)", "иш қаторидаги сон: 0,08 / 12"),
              ("total", "Ресурс жами («по проектным данным»)", "ҳажм устуни ўзи бўлса — шуни танланг"),
              ("price", "Нарх (бир бирлик)", "сўм / бирлик"),
              ("amount", "Сумма (ҳажм × нарх)", "иш / ресурс қаторининг пули")]
    return {"cols": sa.columns(o), "fields": [{"key": k, "title": t, "hint": h, "cur": lay.get(k, 0)} for k, t, h in fields],
            "final": lay.get("final", 0), "memo": lay.get("memo"),
            "manual": {k: v for k, v in (lay.get("manual") or {}).items() if k not in sa.KEEP_KEYS}}  # yig'ilgan / сводка — «qo'lda belgilangan» emas


@require_POST
def layout_check(request, obj_id):
    """Saqlamasdan sinash: shu ustunlar bilan bo'sh / yarim / to'liq hajm sinovi (JSON)."""
    o = _obj(request, obj_id)
    try:
        manual = sa.parse_manual(request.POST, o)
    except TexnazorError as e:
        return JsonResponse({"error": str(e)}, status=400)
    c = sa.preview_check(o, manual)
    return JsonResponse({"ok": c["ok"], "full": c["full"], "empty": c["empty"], "file": c["file"],
                         "half": 100 * c["half"] / c["full"] if c["full"] else None,
                         "fails": [w["title"] for w in c["fails"]]})


@require_POST
def layout_save(request, obj_id):
    """Ustunlarni saqlash (yoki «avtomatikka qaytarish»): fayl qayta o'qiladi, hajmlar saqlanadi, shablon eslab qolinadi."""
    o = _obj(request, obj_id)
    reset = bool(request.POST.get("reset"))
    old = (o.layout or {}).get("manual") or {}
    try:
        manual = {} if reset else sa.parse_manual(request.POST, o)
    except TexnazorError as e:
        messages.error(request, str(e))
        return _to(o)
    if reset:  # «avtomatikka» — belgilar ketadi, foydalanuvchi yig'gan qatorlar qoladi
        manual = {k: old[k] for k in sa.KEEP_KEYS if old.get(k)}
    else:  # belgilash sahifasida ko'rsatilgan sarlavha / ishlar chegarasi shu oyna bilan o'chib ketmasin
        manual = {k: v for k, v in old.items() if k not in ("qty", "total", "price", "amount", "final")} | manual
    sa.set_manual(o, manual)
    audit(request, o, "устунлар автоматик аниқлашга қайтарилди" if reset else "устунлар қўлда белгиланди")
    return _to(o)


def data(request, obj_id):
    """Butun list (JSON): qatorlar fayldagidek, har dalolatnoma va «Остатка» bloki (faqat bo'sh bo'lmagan qatorlar)."""
    o = _obj(request, obj_id)
    b = sa.book(o)
    per = sa.all_qty(o)
    acts = []
    for a in o.acts.all():
        q = per.get(a.pk, {})
        ev = b.evaluate(q)
        rows = sorted(set(q) | {c for r in q for c in b.children.get(r, ())} | set(b.svod_rows))
        acts.append({"n": a.number, "id": a.pk, "closed": a.closed, "name": a.name, "date": a.date.strftime("%d.%m.%Y") if a.date else "",
                     "qty": {str(k): v for k, v in q.items()}, "v": _rounded(b.block(ev, rows)), "sum": sa._final(b, ev)})
    rest_q = sa.rest_qty(b, per)
    rest = b.evaluate(rest_q)
    lay = b.lay
    return JsonResponse({
        "layout": {k: v for k, v in (o.layout or {}).items() if k not in sa.LAYOUT_META},
        "letters": [col_letter(i) for i in range(max(lay.width, lay.block_end + 1))],
        "rows": [[r["row"], r["kind"], r["num"], r["parent"], r["vals"]] for r in b.rows],
        "contract": {str(r): b.contract_qty(r) for r in b.tops},
        "acts": acts, "rest": _rounded(b.block(rest, sa.rest_rows(b, rest_q))), "rest_sum": sa._final(b, rest),
        "final": list(b.final_cell() or []),
        "look": sa.look(o),
    })


def _rounded(block: dict) -> dict:
    """JSON kichikroq bo'lsin: 10 xona aniqlik yetarli (summa tiyinigacha to'g'ri qoladi)."""
    out = {}
    for r, vals in block.items():
        out[str(r)] = [float(f"{v:.10g}") if isinstance(v, float) else v for v in vals]
    return out


@require_POST
def set_qty(request, obj_id):
    """JSON: ``{"act": 2, "row": 13, "qty": "0,08"}`` -> o'zgargan qatorlar (shu dalolatnoma va «Остатка»)."""
    o = _obj(request, obj_id)
    try:
        d = json.loads(request.body or b"{}")
        act = get_object_or_404(Act, obj=o, number=int(d["act"]))
        qty = sa.parse_qty(d.get("qty"), sa.book(o).contract_qty(int(d["row"])))
        if qty is None:
            raise TexnazorError("Ҳажм сон ёки фоиз бўлиши керак (масалан: 0,08 ёки 60%)")
        sa.set_qty(act, int(d["row"]), qty)
    except TexnazorError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"ok": False, "error": "нотўғри сўров"}, status=400)
    res = sa.change_set(o, act, int(d["row"]))
    res["v"], res["rest"] = _rounded(res["v"]), _rounded(res["rest"])
    return JsonResponse({"ok": True, **res})


@require_POST
def act_fill(request, obj_id):
    """JSON: ``{"act": 3, "rows": [13, 23], "pct": 100}`` — belgilangan ishlar jami shuncha % bajarilgan bo'lsin."""
    o = _obj(request, obj_id)
    try:
        d = json.loads(request.body or b"{}")
        act = get_object_or_404(Act, obj=o, number=int(d["act"]))
        rows = [int(x) for x in d.get("rows") or []][:20000]
        if not rows:
            raise TexnazorError("Аввал ишларни белгиланг")
        pct = float(str(d.get("pct", "100")).replace(",", ".").replace("%", ""))
        if not 0 <= pct <= 100:
            raise TexnazorError("0 дан 100 гача фоиз ёзинг")
        n = sa.fill(act, rows, pct)
    except TexnazorError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"ok": False, "error": "нотўғри сўров"}, status=400)
    audit(request, o, f"{act.title}: {n} та иш жами {pct:g}% бажарилди деб белгиланди")
    return JsonResponse({"ok": True, "changed": n})


def _date(text):
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d").date() if text else None
    except ValueError:
        raise TexnazorError("Сана нотўғри")


@require_POST
def act_add(request, obj_id):
    o = _obj(request, obj_id)
    try:
        a = sa.add_act(o, _date(request.POST.get("date")))
    except TexnazorError as e:
        messages.error(request, str(e))
        return _to(o)
    audit(request, o, f"далолатнома {a.title} очилди")
    return _to(o, request.POST.get("view") if request.POST.get("view") in ("act", "report") else "act")


@require_POST
def act_edit(request, obj_id, number):
    """Sana, nom, yopish / qayta ochish, o'chirish (ochiq bo'lsa; keyingilari bittaga suriladi)."""
    o = _obj(request, obj_id)
    a = get_object_or_404(Act, obj=o, number=number)
    what = request.POST.get("do")
    try:
        if what == "close":
            a.closed = not a.closed
            a.save(update_fields=["closed"])
            audit(request, o, f"{a.title} " + ("ёпилди" if a.closed else "қайта очилди"))
        elif what == "date":
            a.date = _date(request.POST.get("date"))
            a.save(update_fields=["date"])
        elif what == "delete":
            title = a.label
            sa.delete_act(a)
            audit(request, o, f"далолатнома {title} ўчирилди")
            messages.success(request, f"Далолатнома {title} ўчирилди")
        elif what == "name":
            old = a.label
            sa.rename_act(a, request.POST.get("name", ""))
            audit(request, o, f"далолатнома {old} номи: «{a.name}»")
    except TexnazorError as e:
        messages.error(request, str(e))
    return _to(o, request.POST.get("view"))


def _xlsx(content: bytes, name: str) -> HttpResponse:
    r = HttpResponse(content, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    r["Content-Disposition"] = content_disposition_header(True, name)
    return r


def _xlsx_est(o: SiteObject, kind: str, n_acts: int) -> int:
    """Excel taxminan necha soniyada yasaladi (yuklash tugmasidagi progress uchun): shu obyektda oxirgi marta
    ketgani; hali yasalmagan bo'lsa — list qatorlari va bloklar soni bo'yicha."""
    secs = cache.get(f"tn-xlsx-secs:{o.pk}:{kind}")
    if secs is None:
        rows = next((s.get("rows") or 0 for s in o.sheets or () if s.get("name") == o.sheet), 0)
        secs = rows * (2 + n_acts + 1) / 5500 if kind == "report" else rows / 2600
    return max(2, round(secs))


def _built(o: SiteObject, kind: str, build) -> bytes:
    """Katta ofyortada Excel bir necha soniya (sekin serverda — o'nlab soniya) yasaladi. Natija holat bo'yicha keshda:
    hajmlar, dalolatnomalar va o'qilgan fayl o'zgarmasa — qayta yasalmaydi. Tugma qayta-qayta bosilsa, ikkinchi so'rov
    birinchisini kutadi (har biri alohida yasab, serverni band qilmaydi)."""
    acts = [(a.pk, a.number, a.label, str(a.date), a.closed) for a in o.acts.all()]
    from .hisobot import VERSION
    state = repr((VERSION, kind, str(o.pk), o.sheet, o.layout.get("stamp"), o.layout.get("reader"), o.layout.get("manual"),
                  acts, sorted((k, sorted(v.items())) for k, v in sa.all_qty(o).items())))
    digest = hashlib.sha1(state.encode()).hexdigest()
    key = "tn-xlsx:" + digest
    # diskda ham saqlanadi (obyekt papkasida): holat o'zgarmaguncha qayta yuklash serverni band qilmaydi — kesh
    # tozalansa yoki server qayta ishga tushsa ham; har tur uchun faqat oxirgisi turadi
    slug = re.sub(r"[^\w-]+", "_", "report" if kind.startswith("report") else kind)[:60]
    folder = o.abs("xlsx")
    path = folder / f"{slug}--{digest}.xlsx"

    def saved():
        try:
            return path.read_bytes()
        except OSError:
            return None
    data = saved()
    if data is not None:
        return data
    if not cache.add(key + ":busy", 1, 180):
        for _ in range(240):  # 2 daqiqagacha — birinchi so'rov tugatsin
            time.sleep(0.5)
            data = saved()
            if data is not None:
                return data
            if cache.get(key + ":busy") is None:
                break  # birinchisi xato bilan tugadi — o'zimiz yasaymiz
    try:
        t0 = time.monotonic()
        data = build()
        cache.set(f"tn-xlsx-secs:{o.pk}:{kind.split(':')[0]}", time.monotonic() - t0, 30 * 86400)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for old in folder.glob(f"{slug}--*.xlsx"):
                old.unlink(missing_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        except OSError:
            pass  # disk band / ruxsat yo'q — fayl baribir beriladi
    finally:
        cache.delete(key + ":busy")
    return data


def report_download(request, obj_id):
    from . import hisobot
    o = _obj(request, obj_id)
    coll = {k for k in request.GET.get("coll", "").split(",") if k}  # saytda yig'ilgan bloklar
    data = _built(o, "report:" + ",".join(sorted(coll)), lambda: hisobot.build_report(o, coll))
    return _xlsx(data, f"Офёрта (нархланган) — {o.name}"[:150] + ".xlsx")


def f2_download(request, obj_id, number):
    from . import hisobot
    o = _obj(request, obj_id)
    a = get_object_or_404(Act, obj=o, number=number)
    return _xlsx(_built(o, f"f2:{a.pk}", lambda: hisobot.build_f2(o, a)), f"Ф-2 {a.title} — {o.name}"[:150] + ".xlsx")


# ===================== qo'lda belgilash =====================
def _can_mark(o: SiteObject) -> bool:
    return bool(o.sheet) and o.status in (SiteObject.Status.MARK, SiteObject.Status.READY, SiteObject.Status.FAILED)


def mark(request, obj_id):
    """Tizim jadvalni tushunmadi (yoki foydalanuvchi o'zi tuzatmoqchi): listning boshi va oxiri ko'rsatiladi —
    sarlavha, birinchi / oxirgi ish, ustunlar va jami qator bosib belgilanadi."""
    o = _obj(request, obj_id)
    if not _can_mark(o):
        return _to(o)
    has_qty = sa.ActQty.objects.filter(act__obj=o).exists()
    return render(request, "texnazor/mark.html", {"nav": "texnazor", "o": o, "reason": o.error if o.status != SiteObject.Status.READY else "",
                                                  "has_qty": has_qty, "several": len(o.sheets or []) > 1})


def mark_rows(request, obj_id):
    """JSON: birinchi so'rov — bosh, oxir, tizim taklifi; ``from`` bilan — keyingi 50 qator."""
    o = _obj(request, obj_id)
    if not _can_mark(o):
        return JsonResponse({"error": "Белгилаб бўлмайди"}, status=400)
    try:
        if request.GET.get("from"):
            return JsonResponse({"rows": sa.mark_rows(o, int(request.GET["from"]))})
        return JsonResponse(sa.mark_data(o))
    except (ValueError, TexnazorError, sa.SheetError) as e:
        return JsonResponse({"error": str(e) or "Листни очиб бўлмади"}, status=400)


@require_POST
def mark_save(request, obj_id):
    o = _obj(request, obj_id)
    if not _can_mark(o):
        return JsonResponse({"error": "Белгилаб бўлмайди"}, status=400)
    try:
        marks = sa.parse_marks(request.POST, o)
    except TexnazorError as e:
        return JsonResponse({"error": str(e)}, status=400)
    sa.set_marks(o, marks)
    audit(request, o, f"жадвал қўлда белгиланди: сарлавҳа {marks['header']}-қатор, ишлар {marks['start']}"
                      f"–{marks.get('end') or 'охиригача'}")
    return JsonResponse({"ok": True, "url": redirect("texnazor:object", obj_id=o.pk).url})


@require_POST
def retry(request, obj_id):
    o = _obj(request, obj_id)
    if o.status == SiteObject.Status.FAILED:
        sa.retry(o)
        audit(request, o, "офёртани ўқиш қайта уринилди")
    return _to(o)


@require_POST
def hide_rows(request, obj_id):
    """«Keraksiz» qatorlarni yig'ish (``from``–``to``) yoki qaytarish (``remove`` — oraliq boshi): fayl qayta o'qiladi."""
    o = _obj(request, obj_id)
    if o.status != SiteObject.Status.READY:
        return JsonResponse({"error": "Офёрта ҳали тайёр эмас"}, status=400)
    key = "svod" if request.POST.get("kind") == "svod" else "hide"
    what = "сводка" if key == "svod" else "кераксиз"
    try:
        if request.POST.get("remove"):
            r = int(request.POST["remove"])
            sa.set_hide(o, remove=r, key=key)
            audit(request, o, f"{what} белгиси олинди ({r}-қатор)")
        else:
            a, b = int(request.POST.get("from", "")), int(request.POST.get("to", ""))
            sa.set_hide(o, add=[a, b], key=key)
            audit(request, o, f"{what} деб белгиланди: {min(a, b)}–{max(a, b)}-қаторлар")
    except ValueError:
        return JsonResponse({"error": "Қаторлар нотўғри"}, status=400)
    except TexnazorError as e:
        return JsonResponse({"error": str(e)}, status=400)
    return JsonResponse({"ok": True})


@require_POST
def hide_col(request, obj_id):
    """Keraksiz ustunni yig'ish (``on=1``) yoki ochish — faqat ko'rinish, fayl qayta o'qilmaydi (JSON)."""
    o = _obj(request, obj_id)
    if o.status != SiteObject.Status.READY:
        return JsonResponse({"error": "Офёрта ҳали тайёр эмас"}, status=400)
    try:
        cols = sa.set_hide_col(o, int(request.POST.get("col", "")), request.POST.get("on") == "1")
    except ValueError:
        return JsonResponse({"error": "Устун нотўғри"}, status=400)
    except TexnazorError as e:
        return JsonResponse({"error": str(e)}, status=400)
    return JsonResponse({"ok": True, "cols": cols})


@require_POST
def prop_res(request, obj_id):
    """Boshqa qatordagi hajmga ko'paytirilgan resurslarni o'z ishi hajmiga mutanosib qilish / bekor qilish."""
    o = _obj(request, obj_id)
    on = request.POST.get("on") == "1"
    key = "relink" if request.POST.get("mode") == "relink" else "prop_res"
    sa.set_prop_res(o, on, key)
    audit(request, o, ("ресурслар формуласи ўз иши ҳажмига уланди" if key == "relink" else
                       "ресурслар ўз иши ҳажмига мутаносиб қилинди") if on else "ресурслар тузатиши бекор қилинди — файлдагидек")
    return _to(o)
