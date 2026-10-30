"""Texnazor sahifalari: obyektlar ro'yxati, obyekt qo'shish (ofyorta yuklash), obyekt — oylar va hajmlar.

Obyekt sahifasi og'ir emas: avval faqat bo'limlar (yig'indilari bilan), ishlar — bo'lim ochilganda (alohida
so'rov), resurslar — ish ochilganda. Hajm yozilsa faqat shu qator saqlanadi va jami summalar qaytariladi."""
import json

from django.contrib import messages
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import services, tasks
from .calc import STEPS
from .models import Correction, Item, Period, Section, SiteObject
from .ofyorta import CATEGORY_LABELS

SEARCH_LIMIT = 200


def _obj(request, obj_id) -> SiteObject:
    return get_object_or_404(SiteObject, pk=obj_id, owner=request.user)


def _period(obj, request, data=None) -> Period:
    pid = (data or {}).get("period") or request.GET.get("p") or request.POST.get("period")
    qs = obj.periods.all()
    if pid:
        return get_object_or_404(qs, pk=pid)
    p = qs.filter(closed=False).order_by("-year", "-month").first() or qs.order_by("-year", "-month").first()
    if p is None:
        raise Http404("Ой йўқ")
    return p


def index(request):
    return render(request, "texnazor/index.html", {"nav": "texnazor", "cards": services.object_cards(request.user)})


def create(request):
    error = None
    if request.method == "POST":
        f = request.FILES.get("ofyorta")
        try:
            if not f:
                raise services.TexnazorError("Офёрта файлини танланг")
            obj = services.create_object(request.user, f, **{k: request.POST.get(k, "").strip()
                                                            for k in ("name", "address", "customer", "contractor", "contract")})
            tasks.start(obj.pk)
            return redirect("texnazor:object", obj_id=obj.pk)
        except services.TexnazorError as e:
            error = str(e)
    return render(request, "texnazor/create.html", {"nav": "texnazor", "error": error, "data": request.POST})


def state(request, obj_id):
    """O'qish jarayoni (sahifa so'rab turadi)."""
    o = _obj(request, obj_id)
    return JsonResponse({"status": o.status, "error": o.error, "progress": o.progress})


def detail(request, obj_id):
    o = _obj(request, obj_id)
    if o.status != SiteObject.Status.READY:
        return render(request, "texnazor/loading.html", {"nav": "texnazor", "o": o})
    periods = list(o.periods.all())
    period = None
    if periods:
        try:
            period = _period(o, request)
        except Http404:
            period = periods[-1]
    ctx = {"nav": "texnazor", "o": o, "periods": periods, "period": period, "categories": CATEGORY_LABELS,
           "next": services.next_month(o), "months": Period.MONTHS, "warnings": services.warnings(o),
           "corrections": _history(o)}
    if period:
        totals = services.period_totals(period)
        sections = o.sections.filter(items_count__gt=0)
        section = sections.filter(pk=request.GET.get("s") or 0).first() or sections.first()
        ctx.update(totals=totals, tree=services.section_tree(o, services.section_stats(period)), pay=services.payments(o),
                   period_info=services.period_info(period),
                   steps=[(k, label, totals["contract"].get(k, 0.0), totals["before"][k], totals["this"][k],
                           totals["cum"][k], totals["left"][k]) for k, label in STEPS])
        if section:
            ctx.update(_panel(o, period, section))
    return render(request, "texnazor/object.html", ctx)


def _panel(o, period, section) -> dict:
    """O'ng tomondagi bo'lim: ishlari, raqamlari, qaysi blokda va oldingi / keyingi bo'lim."""
    earlier = [p for p in o.periods.all() if p.key < period.key]  # «Аввал» устуни шу ойлар йиғиндиси
    return {"section": section, "rows": _section_rows(period, section), "stats": _section_stats(period, section),
            "where": services.section_nav(o, section), "warn": _warn(o), "fixes": services.latest_corrections(o),
            "earlier": earlier}


def _history(o) -> list:
    """Ofyorta tuzatishlari tarixi (yangisi tepada) — qaysi blok / bo'limda ekani bilan."""
    paths = services.section_paths(o)
    out = list(o.corrections.select_related("item", "author", "period", "reverted_by")[:300])
    last = services.latest_corrections(o)  # bekor qilsa bo'ladigani — har ishning oxirgi amaldagisi
    for c in out:
        c.where = paths.get(c.item.section_id)
        c.can_revert = c.item_id in last and last[c.item_id].pk == c.pk
    return out


def _warn(o) -> dict:
    """Xatosi bor ishlar: qatorida «⚠» belgisi (ish -> qisqa izoh)."""
    return {w["item"]: w["title"] for w in services.warnings(o) if w["item"]}


def section_items(request, obj_id, section_id):
    o = _obj(request, obj_id)
    period = _period(o, request)
    section = get_object_or_404(Section, pk=section_id, obj=o)
    return render(request, "texnazor/_panel.html", {"o": o, "period": period, **_panel(o, period, section)})


def _section_rows(period, section):
    return services.item_rows(period, section.items.filter(kind__in=services.ITEM_KINDS).prefetch_related("resources"))


def _section_stats(period, section) -> dict:
    """Bo'lim raqamlari (hajm saqlangandan keyin mundarija va sarlavhada yangilanadi)."""
    st = services.section_stats(period).get(section.pk) if section else None
    return services.section_pct(st or {"contract": 0.0, "before": 0.0, "this": 0.0}) | {"id": section.pk if section else None}


def search(request, obj_id):
    o = _obj(request, obj_id)
    period = _period(o, request)
    q = request.GET.get("q", "").strip()
    ctx = {"o": o, "period": period, "search": q}
    if len(q) < 2:
        return render(request, "texnazor/_panel.html", {**ctx, "rows": [], "hint": "Камида 2 та ҳарф ёзинг"})
    cond = Q(search__contains=services.search_key(q)) | Q(num=q)
    if q.isdigit():  # Excel qatori bo'yicha ham (resurs qatori bo'lsa — uning ishi)
        hit = o.items.filter(excel_row=int(q)).values_list("pk", "parent_id").first()
        if hit:
            cond |= Q(pk=hit[1] or hit[0])
    items = o.items.filter(kind__in=services.ITEM_KINDS).filter(cond).prefetch_related("resources")[:SEARCH_LIMIT]
    rows = services.item_rows(period, items)
    where = services.section_paths(o)  # qidiruvda — qaysi blok / bo'limdan
    for r in rows:
        r["where"] = where.get(r["item"].section_id)
    return render(request, "texnazor/_panel.html", {**ctx, "rows": rows, "limited": len(rows) >= SEARCH_LIMIT, "warn": _warn(o),
                                                    "fixes": services.latest_corrections(o)})


def _summary(period):
    t = services.period_totals(period)
    return {"this": t["this"]["contractor"], "cum": t["cum"]["contractor"], "left": t["left"]["contractor"],
            "contract": t["contract"].get("contractor", 0.0), "html": render_to_string("texnazor/_summary.html", {
                "period": period, "steps": [(k, label, t["contract"].get(k, 0.0), t["before"][k], t["this"][k],
                                             t["cum"][k], t["left"][k]) for k, label in STEPS]})}


@require_POST
def set_done(request, obj_id):
    """JSON: ``{"period": 1, "item": 5, "value": "35" | "60%" | ""}`` -> qator va jami summalar."""
    o = _obj(request, obj_id)
    try:
        data = json.loads(request.body or b"{}")
        period = _period(o, request, data)
        item = get_object_or_404(Item, pk=int(data["item"]), obj=o)
        services.set_done(period, item, data.get("value"))
    except services.TexnazorError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"ok": False, "error": "нотўғри сўров"}, status=400)
    row = services.item_rows(period, Item.objects.filter(pk=item.pk).prefetch_related("resources"))[0]
    html = render_to_string("texnazor/_item_row.html", {"r": row, "period": period, "o": o, "warn": _warn(o),
                                                        "fixes": services.latest_corrections(o, [item.pk])})
    return JsonResponse({"ok": True, "row": html, "section": _section_stats(period, item.section),
                         "summary": _summary(period)})


@require_POST
def correct(request, obj_id, item_id):
    """JSON: ``{"period": 1, "value": "0,4", "basis": "Қўшимча келишув №2", "note": ""}`` — shartnoma hajmini tuzatish."""
    o = _obj(request, obj_id)
    item = get_object_or_404(Item, pk=item_id, obj=o)
    try:
        data = json.loads(request.body or b"{}")
        period = o.periods.filter(pk=data.get("period") or 0).first()
        c = services.correct_item(request.user, item, period, data.get("value"), data.get("basis"), data.get("note", ""))
    except services.TexnazorError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "нотўғри сўров"}, status=400)
    return JsonResponse({"ok": True, "id": c.pk})


@require_POST
def set_paid(request, obj_id, period_id):
    """Ф-3 bo'yicha haqiqatda to'langan summa (bo'sh — o'chiriladi)."""
    o = _obj(request, obj_id)
    p = get_object_or_404(Period, pk=period_id, obj=o)
    text = request.POST.get("paid", "").replace(" ", "").replace(" ", "").replace(",", ".").strip()
    try:
        p.paid = float(text) if text else None
    except ValueError:
        messages.error(request, f"Тўланган сумма нотўғри: «{request.POST.get('paid')}»")
        return _to(o, p)
    p.save(update_fields=["paid"])
    return _to(o, p)


@require_POST
def fill_items(request, obj_id):
    """JSON: ``{"period": 1, "items": [5, 6], "pct": 100}`` — tanlangan ishlar jami shuncha % bajarilgan bo'lsin."""
    o = _obj(request, obj_id)
    try:
        data = json.loads(request.body or b"{}")
        period = _period(o, request, data)
        ids = [int(x) for x in data.get("items") or []][:5000]
        if not ids:
            raise services.TexnazorError("Аввал ишларни белгиланг")
        items = list(o.items.filter(pk__in=ids, kind__in=services.ITEM_KINDS))
        n = services.fill_items(period, items, float(str(data.get("pct", "100")).replace(",", ".").replace("%", "")))
    except services.TexnazorError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "фоиз нотўғри"}, status=400)
    stats = services.section_stats(period)
    secs = [services.section_pct(stats.get(sid) or {"contract": 0.0, "before": 0.0, "this": 0.0}) | {"id": sid}
            for sid in {it.section_id for it in items if it.section_id}]
    return JsonResponse({"ok": True, "changed": n, "sections": secs, "summary": _summary(period)})


@require_POST
def fill_section(request, obj_id, section_id):
    o = _obj(request, obj_id)
    section = get_object_or_404(Section, pk=section_id, obj=o)
    try:
        data = json.loads(request.body or b"{}")
        period = _period(o, request, data)
        n = services.fill_section(period, section, float(str(data.get("pct", "100")).replace(",", ".")))
    except services.TexnazorError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "фоиз нотўғри"}, status=400)
    html = render_to_string("texnazor/_panel.html", {"o": o, "period": period, **_panel(o, period, section)})
    return JsonResponse({"ok": True, "changed": n, "panel": html, "section": _section_stats(period, section),
                         "summary": _summary(period)})


def _to(o, period=None):
    url = reverse("texnazor:object", args=[o.pk])
    return redirect(f"{url}?p={period.pk}" if period else url)


@require_POST
def add_period(request, obj_id):
    o = _obj(request, obj_id)
    try:
        p = services.add_period(o, int(request.POST.get("year") or 0), int(request.POST.get("month") or 0))
    except (services.TexnazorError, ValueError) as e:
        messages.error(request, str(e))
        return _to(o)
    return _to(o, p)


@require_POST
def close_period(request, obj_id, period_id):
    o = _obj(request, obj_id)
    p = get_object_or_404(Period, pk=period_id, obj=o)
    p.closed = request.POST.get("closed") == "1"
    p.save(update_fields=["closed"])
    return _to(o, p)


@require_POST
def delete(request, obj_id):
    """Obyektni o'chirish. Tayyor obyekt — ikkinchi bosqichda «ЎЧИРИШ» deb yozilgandagina (o'qilmagan — darhol)."""
    o = _obj(request, obj_id)
    if o.status == SiteObject.Status.READY and not services._confirmed(request.POST.get("confirm"), services.DELETE_WORD):
        messages.error(request, f"Объект ўчирилмади: тасдиқлаш учун «{services.DELETE_WORD}» деб ёзинг")
        return _to(o)
    services.delete_object(o)
    return redirect("texnazor:index")


@require_POST
def delete_period(request, obj_id, period_id):
    o = _obj(request, obj_id)
    p = get_object_or_404(Period, pk=period_id, obj=o)
    try:
        services.delete_period(p, request.POST.get("confirm"))
    except services.TexnazorError as e:
        messages.error(request, str(e))
        return _to(o, p)
    return _to(o)


@require_POST
def revert_correction(request, obj_id, correction_id):
    o = _obj(request, obj_id)
    c = get_object_or_404(Correction, pk=correction_id, obj=o)
    try:
        services.revert_correction(request.user, c, json.loads(request.body or b"{}").get("confirm"))
    except services.TexnazorError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "нотўғри сўров"}, status=400)
    return JsonResponse({"ok": True, "item": c.item_id, "section": c.item.section_id})
