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
from .models import Item, Period, Section, SiteObject
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
    objs = list(SiteObject.objects.filter(owner=request.user))
    cards = []
    for o in objs:
        last = o.periods.order_by("-year", "-month").first()
        t = services.period_totals(last) if last and o.status == SiteObject.Status.READY else None
        contract = (o.contract_totals or {}).get("contractor", 0.0)
        done = t["cum"]["contractor"] if t else 0.0
        cards.append({"o": o, "last": last, "contract": contract, "done": done, "left": contract - done,
                      "pct": 100 * done / contract if contract else 0.0, "months": o.periods.count()})
    return render(request, "texnazor/index.html", {"nav": "texnazor", "cards": cards})


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
           "next": services.next_month(o), "months": Period.MONTHS}
    if period:
        totals = services.period_totals(period)
        stats = services.section_stats(period)
        sections = []
        for s in o.sections.all():
            st = stats.get(s.pk)
            if not s.items_count:
                sections.append({"s": s, "group": True})
                continue
            st = st or {"contract": 0.0, "before": 0.0, "this": 0.0}
            cum = st["before"] + st["this"]
            sections.append({"s": s, **st, "cum": cum, "pct": 100 * cum / st["contract"] if st["contract"] else 0.0})
        ctx.update(totals=totals, sections=sections,
                   steps=[(k, label, totals["contract"].get(k, 0.0), totals["before"][k], totals["this"][k],
                           totals["cum"][k], totals["left"][k]) for k, label in STEPS])
    return render(request, "texnazor/object.html", ctx)


def section_items(request, obj_id, section_id):
    o = _obj(request, obj_id)
    period = _period(o, request)
    section = get_object_or_404(Section, pk=section_id, obj=o)
    rows = services.item_rows(period, section.items.filter(kind__in=services.ITEM_KINDS).prefetch_related("resources"))
    return render(request, "texnazor/_items.html", {"o": o, "period": period, "rows": rows})


def search(request, obj_id):
    o = _obj(request, obj_id)
    period = _period(o, request)
    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return render(request, "texnazor/_items.html", {"o": o, "period": period, "rows": [], "hint": "Камида 2 та белги ёзинг"})
    items = (o.items.filter(kind__in=services.ITEM_KINDS)
             .filter(Q(search__contains=services.search_key(q)) | Q(num=q))[:SEARCH_LIMIT])
    rows = services.item_rows(period, items)
    return render(request, "texnazor/_items.html", {"o": o, "period": period, "rows": rows, "search": True,
                                                    "limited": len(rows) >= SEARCH_LIMIT})


def resources(request, obj_id, item_id):
    o = _obj(request, obj_id)
    item = get_object_or_404(Item, pk=item_id, obj=o)
    return render(request, "texnazor/_resources.html", {"item": item, "resources": item.resources.all(),
                                                        "categories": CATEGORY_LABELS})


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
    row = services.item_rows(period, [item])[0]
    html = render_to_string("texnazor/_item_row.html", {"r": row, "period": period, "o": o})
    return JsonResponse({"ok": True, "row": html, "summary": _summary(period)})


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
    return JsonResponse({"ok": True, "changed": n, "summary": _summary(period)})


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
    services.delete_object(_obj(request, obj_id))
    return redirect("texnazor:index")
