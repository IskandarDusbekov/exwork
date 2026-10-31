"""Панел: техназор — объектлар, ойлар, ким қачон нима қилди (ҳажм ёзиш, ойни ёпиш, Ф-2 / Ф-3, тузатишлар),
«Менинг офёрталарим». Кўриш — ``is_staff``; файл юклаб олиш — фақат суперфойдаланувчи (журналга ёзилади)."""
from datetime import timedelta

from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.http import content_disposition_header

from texnazor import f2, f3, services
from texnazor.models import Correction, Done, MyOfyorta, Period, SiteObject

from . import data
from .audit import log as audit
from .models import Event
from .views import _ctx, staff_required, superuser_required

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _tn_events():
    return Event.objects.filter(kind=Event.Kind.TEXNAZOR).select_related("actor")


def _progress(o) -> dict:
    """Шартнома (пудратчи, ҚҚС билан), бошидан бажарилган ва фоиз — охирги ой бўйича."""
    last = o.periods.order_by("-year", "-month").first()
    contract = (o.contract_totals or {}).get("contractor", 0.0)
    if not last or o.status != SiteObject.Status.READY:
        return {"contract": contract, "done": 0.0, "left": contract, "pct": 0.0, "last": last}
    done = services.period_totals(last)["cum"]["contractor"]
    return {"contract": contract, "done": done, "left": contract - done, "pct": 100 * done / contract if contract else 0.0,
            "last": last}


def object_rows(qs) -> list:
    """Объектлар жадвали: ойлар, ёзилган ҳажмлар (ҳафтада нечта), тузатишлар, охирги ҳаракат."""
    objs = list(qs.select_related("owner", "made_from"))
    ids = [o.pk for o in objs]
    week = timezone.now() - timedelta(days=7)
    months = {r["obj"]: r for r in Period.objects.filter(obj__in=ids).values("obj").annotate(
        n=Count("id"), closed=Count("id", filter=Q(closed=True)))}
    done = {r["period__obj"]: r for r in Done.objects.filter(period__obj__in=ids).values("period__obj").annotate(
        n=Count("id"), week=Count("id", filter=Q(updated__gte=week)), last=Max("updated"))}
    fixes = dict(Correction.objects.filter(obj__in=ids, reverted__isnull=True).values("obj").annotate(n=Count("id"))
                 .values_list("obj", "n"))
    events = dict(_tn_events().filter(work_id__in=ids).values("work_id").annotate(last=Max("created"))
                  .values_list("work_id", "last"))
    rows = []
    for o in objs:
        d = done.get(o.pk, {})
        last = max((t for t in (d.get("last"), events.get(o.pk), o.created) if t), default=None)
        rows.append({"o": o, **_progress(o), "months": months.get(o.pk, {}).get("n", 0),
                     "closed": months.get(o.pk, {}).get("closed", 0), "done_rows": d.get("n", 0),
                     "done_week": d.get("week", 0), "fixes": fixes.get(o.pk, 0), "active": last,
                     "warnings": len(o.warnings or [])})
    return rows


@staff_required
def texnazor(request):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    qs = SiteObject.objects.order_by("-created")
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(owner__email__icontains=q) | Q(owner__first_name__icontains=q)
                       | Q(owner__last_name__icontains=q) | Q(contractor__icontains=q) | Q(customer__icontains=q))
    if status in SiteObject.Status.values:
        qs = qs.filter(status=status)
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    now = timezone.now()
    day, week = now - timedelta(days=1), now - timedelta(days=7)
    all_objs = SiteObject.objects.all()
    return render(request, "panel/texnazor.html", _ctx(
        request, "texnazor", page=page, rows=object_rows(SiteObject.objects.filter(pk__in=[o.pk for o in page]).order_by("-created")),
        q=q, status=status, statuses=SiteObject.Status.choices,
        c={"objects": all_objs.count(), "ready": all_objs.filter(status=SiteObject.Status.READY).count(),
           "parsing": all_objs.filter(status=SiteObject.Status.PARSING).count(),
           "failed": all_objs.filter(status=SiteObject.Status.FAILED).count(),
           "users": all_objs.values("owner").distinct().count(),
           "months": Period.objects.count(), "open_months": Period.objects.filter(closed=False).count(),
           "done_day": Done.objects.filter(updated__gte=day).count(), "done_week": Done.objects.filter(updated__gte=week).count(),
           "actions_day": _tn_events().filter(created__gte=day).count(),
           "my": MyOfyorta.objects.count()},
        days=data.per_day(Done.objects.filter(updated__isnull=False), "updated", days=30),
        events=_tn_events()[:40],
        docs=MyOfyorta.objects.select_related("owner", "project").defer("problems", "structure")[:30],
    ))


def _object(obj_id) -> SiteObject:
    return get_object_or_404(SiteObject.objects.select_related("owner", "made_from"), pk=obj_id)


@staff_required
def texnazor_object(request, obj_id):
    o = _object(obj_id)
    periods = list(o.periods.all())
    by_period = {r["period"]: r for r in Done.objects.filter(period__obj=o).values("period").annotate(
        n=Count("id"), last=Max("updated"))}
    months = []
    for p in periods:
        t = services.period_totals(p) if o.status == SiteObject.Status.READY else None
        this = t["this"]["contractor"] if t else 0.0
        months.append({"p": p, "rows": by_period.get(p.pk, {}).get("n", 0), "last": by_period.get(p.pk, {}).get("last"),
                       "this": this, "cum": t["cum"]["contractor"] if t else 0.0,
                       "diff": (p.paid - this) if p.paid is not None else None})
    audit(request, Event.Kind.ADMIN_VIEW, target=o.owner, work=o, work_kind="texnazor", detail=f"техназор: {o.name[:200]}")
    return render(request, "panel/texnazor_object.html", _ctx(
        request, "texnazor", o=o, **_progress(o), months=months,
        fixes=o.corrections.select_related("author", "item", "period", "reverted_by")[:50],
        events=_tn_events().filter(work_id=o.pk)[:60],
        recent=Done.objects.filter(period__obj=o, updated__isnull=False).select_related("item", "period")
        .order_by("-updated")[:30],
        info_missing=services.f3_missing(o) if o.status == SiteObject.Status.READY else [],
        src=o.abs(o.source_path) if o.source_path and o.abs(o.source_path).is_file() else None,
    ))


@superuser_required
def texnazor_file(request, obj_id, what, period_id=None):
    """Асл офёрта, Ф-2 ёки Ф-3 (шу ой) — журналга ёзилади."""
    o = _object(obj_id)
    if what == "src":
        path = o.abs(o.source_path) if o.source_path else None
        if not path or not path.is_file():
            raise Http404("Файл топилмади")
        audit(request, Event.Kind.ADMIN_DOWNLOAD, target=o.owner, work=o, work_kind="texnazor", detail=f"техназор офёртаси: {o.name[:200]}")
        return FileResponse(open(path, "rb"), as_attachment=True, filename=o.source_name or path.name)
    if what not in ("f2", "f3") or o.status != SiteObject.Status.READY:
        raise Http404
    p = get_object_or_404(Period, pk=period_id, obj=o)
    mod = f2 if what == "f2" else f3
    audit(request, Event.Kind.ADMIN_DOWNLOAD, target=o.owner, work=o, work_kind="texnazor",
          detail=f"техназор {what.upper().replace('F', 'Ф-')}: {o.name[:150]}, {p.title}")
    resp = HttpResponse(mod.build(p), content_type=XLSX)
    resp["Content-Disposition"] = content_disposition_header(True, mod.file_name(p))
    return resp


@superuser_required
def texnazor_my_file(request, doc_id):
    doc = get_object_or_404(MyOfyorta, pk=doc_id)
    if doc.status != MyOfyorta.Status.READY or not doc.file.is_file():
        raise Http404("Офёрта тайёр эмас")
    audit(request, Event.Kind.ADMIN_DOWNLOAD, target=doc.owner, work=doc, work_kind="tn_my", detail=f"менинг офёртам: {doc.name[:200]}")
    return FileResponse(open(doc.file, "rb"), as_attachment=True, filename=f"Офёрта {doc.name[:150]}.xlsx")
