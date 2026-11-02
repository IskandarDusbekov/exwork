"""Texnazor — dalolatnomalar tartibi sahifalari: list tanlash, ofyorta «fayldagidek», №1, №2 … va «Остатка»,
hajm yozish, Excel («Хисобот» va Ф-2 №N)."""
import datetime as dt
import json

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST

from . import services_acts as sa
from . import services, tasks
from .formula import col_letter
from .models import Act, SiteObject
from .services import TexnazorError
from .views import _obj, audit


def _to(o, view=None):
    url = redirect("texnazor:object", obj_id=o.pk).url
    return redirect(url + (f"?view={view}" if view else ""))


def choose(request, o: SiteObject):
    """Yuklangan fayldagi listlar: tizim taklif qilgani belgilangan, foydalanuvchi tasdiqlaydi yoki boshqasini tanlaydi."""
    guess = next((s["name"] for s in o.sheets if s.get("guess")), "")
    return render(request, "texnazor/choose.html", {"nav": "texnazor", "o": o, "sheets": o.sheets, "guess": guess})


@require_POST
def choose_sheet(request, obj_id):
    o = _obj(request, obj_id)
    name = request.POST.get("sheet", "")
    if o.status != SiteObject.Status.CHOOSE or not any(s["name"] == name for s in o.sheets):
        messages.error(request, "Листни танланг")
        return _to(o)
    o.sheet, o.status, o.error = name[:255], SiteObject.Status.PARSING, ""
    o.progress = {"step": "Офёрта ўқилмоқда"}
    o.save(update_fields=["sheet", "status", "error", "progress"])
    audit(request, o, f"офёрта листи танланди: «{name}»")
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
        "warnings": o.warnings or [], "today": dt.date.today(),
        "info": services.f3_settings(o), "info_missing": services.f3_missing(o),
        "active": next((a for a in reversed(acts) if not a.closed), None)})


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
        "layout": {k: v for k, v in (o.layout or {}).items() if k not in ("stamp", "reader")},
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


def report_download(request, obj_id):
    from . import hisobot
    o = _obj(request, obj_id)
    coll = {k for k in request.GET.get("coll", "").split(",") if k}  # saytda yig'ilgan bloklar
    return _xlsx(hisobot.build_report(o, coll), f"Офёрта (нархланган) — {o.name}"[:150] + ".xlsx")


def f2_download(request, obj_id, number):
    from . import hisobot
    o = _obj(request, obj_id)
    a = get_object_or_404(Act, obj=o, number=number)
    return _xlsx(hisobot.build_f2(o, a), f"Ф-2 {a.title} — {o.name}"[:150] + ".xlsx")
