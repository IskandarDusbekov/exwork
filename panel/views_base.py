"""Панел → «Ресурс базаси»: йиғилган кодлар ва нархлар, манбалар, қидирув, таҳрирлаш. Кўриш — ``is_staff``,
ўзгартириш — фақат ``is_superuser`` (ҳаммаси журналга ёзилади). Ҳозирча синов учун — ҳаммасини ўчириш мумкин."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from jobs.models import Batch, Job, Project
from resbase import services
from resbase.models import CATEGORIES, FLAG_LABELS, QUARTERS, BaseConfig, Observation, Resource, Source, counted_q
from resbase.regions import REGION_LABELS, REGIONS

from .audit import log as audit
from .models import Event
from .views import _ctx, staff_required, superuser_required


def _bctx(request, **kw):
    """«Ресурс базаси» саҳифалари учун: умумий контекст + текширувдаги нархлар сони (таб белгиси)."""
    return _ctx(request, "base", n_review=Observation.objects.filter(review=Observation.Review.PENDING).count(), **kw)


def _in_panel(url):
    """«next» faqat panel ichidagi sahifa bo'lsa — boshqa saytga yoki panel tashqarisiga qaytarilmaydi."""
    return url.startswith(reverse("panel:overview"))


def _years():
    y = timezone.now().year
    return list(range(y + 1, 2017, -1))


def _period(data):
    """Формадан (viloyat, yil, chorak) — бўш = номаълум."""
    p = services.parse_period(data)
    return p["region"], p["year"], p["quarter"]


def _form_ctx():
    return {"regions": REGIONS, "years": _years(), "quarters": QUARTERS, "categories": CATEGORIES}


# ===================== умумий кўриниш =====================
@staff_required
def base_overview(request):
    cfg = BaseConfig.get()
    periods, cells = services.matrix()
    regions_used = sorted({r for r, _, _ in cells}, key=lambda r: [k for k, _ in REGIONS].index(r) if r in REGION_LABELS else 99)
    table = [{"region": r, "label": REGION_LABELS.get(r, "Номаълум вилоят"),
              "cells": [cells.get((r, y, q)) for y, q in periods]} for r in regions_used]
    priced = Resource.objects.filter(counted_q("observations__")).distinct().count()
    return render(request, "panel/base_overview.html", _bctx(
        request, cfg=cfg, periods=periods, table=table,
        n_resources=Resource.objects.count(), n_priced=priced, n_obs=Observation.objects.count(),
        n_sources=Source.objects.count(), n_unknown=Source.objects.filter(Q(year__isnull=True) | Q(quarter__isnull=True)
                                                                          | Q(region="")).count(),
        n_manual=Observation.objects.filter(manual=True).count(), n_excluded=Observation.objects.filter(excluded=True).count(),
        n_pending=Observation.objects.filter(review=Observation.Review.PENDING).count(),
        cat_labels=dict(CATEGORIES), by_category=dict(Resource.objects.values_list("category").annotate(n=Count("id"))),
        **_form_ctx()))


@require_POST
@superuser_required
def base_toggle(request):
    cfg = BaseConfig.get()
    cfg.enabled = request.POST.get("enabled") == "1"
    cfg.updated_by = request.user
    cfg.save()
    audit(request, Event.Kind.ADMIN_BASE, detail="базадан нархлаш " + ("ёқилди" if cfg.enabled else "ўчирилди"))
    return redirect("panel:base")


@require_POST
@superuser_required
def base_default(request):
    cfg = BaseConfig.get()
    cfg.region, cfg.year, cfg.quarter = _period(request.POST)
    cfg.updated_by = request.user
    cfg.save()
    audit(request, Event.Kind.ADMIN_BASE, detail=f"сукут бўйича давр: {services.period_label(cfg.period) or 'танланмаган'}")
    return redirect("panel:base")


@require_POST
@superuser_required
def base_wipe(request):
    n = Observation.objects.count()
    services.wipe_all()
    audit(request, Event.Kind.ADMIN_BASE, detail=f"база тўлиқ тозаланди ({n} та нарх)")
    messages.success(request, "База тозаланди.")
    return redirect("panel:base")


# ===================== қидирув ва ресурс =====================
@staff_required
def base_search(request):
    q = request.GET.get("q", "").strip()
    cat = request.GET.get("cat", "")
    found = Resource.objects.all()
    if q:
        code = services.norm_code(q)
        text = Q()
        for v in {q, q.upper(), q.lower(), q.capitalize()}:  # SQLite кирилл ҳарфларида катта-кичикни фарқлайди
            text |= Q(name__icontains=v) | Q(names__name__icontains=v)
        found = found.filter(Q(code=code) | text if code else text)
    if cat in dict(CATEGORIES):
        found = found.filter(category=cat)
    # аввал топилганлар (номлар жадвали билан бирлашганда такрорланади), кейин санаш — сонлар кўпайиб кетмасин
    qs = Resource.objects.filter(pk__in=found.values("pk")).annotate(
        n_obs=Count("observations", filter=Q(observations__excluded=False)),
        n_src=Count("observations__source", distinct=True))
    qs = qs.order_by("-n_obs", "code") if not q else qs.order_by("code")
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "panel/base_search.html", _bctx(request, page=page, q=q, cat=cat, **_form_ctx()))


@staff_required
def base_resource(request, code):
    res = get_object_or_404(Resource, code=code)
    obs = res.observations.select_related("source", "source__owner").order_by("-source__year", "-source__quarter", "price")
    return render(request, "panel/base_resource.html", _bctx(
        request, res=res, obs=obs, medians=services.resource_medians(res), names=res.names.all()[:30],
        region_labels=REGION_LABELS, **_form_ctx()))


@require_POST
@superuser_required
def base_resource_edit(request, code):
    res = get_object_or_404(Resource, code=code)
    name = " ".join(request.POST.get("name", "").split())[:500]
    unit = request.POST.get("unit", "").strip()[:40]
    cat = request.POST.get("category", "")
    if name:
        res.name = name
    if unit:
        res.unit, res.unit_key = unit, services.unit_key(unit)
    res.category = cat if cat in dict(CATEGORIES) else ""
    res.edited = True
    res.save()
    services.recheck([res.pk])  # асосий ном ёки бирлик ўзгарди — шубҳалилар қайта белгиланади
    audit(request, Event.Kind.ADMIN_BASE, detail=f"ресурс {res.code} таҳрирланди: {res.name} [{res.unit}] {res.category}")
    return redirect("panel:base_resource", code=res.code)


@require_POST
@superuser_required
def base_resource_delete(request, code):
    res = get_object_or_404(Resource, code=code)
    audit(request, Event.Kind.ADMIN_BASE, detail=f"ресурс {res.code} ўчирилди ({res.observations.count()} та нарх)")
    res.delete()
    services.bump()
    return redirect("panel:base_search")


@require_POST
@superuser_required
def base_obs(request, pk, action):
    o = get_object_or_404(Observation.objects.select_related("resource"), pk=pk)
    code = o.resource.code
    if action == "exclude":
        o.excluded = not o.excluded
        if not o.excluded and o.review == Observation.Review.REJECTED:
            o.review = Observation.Review.NONE  # қайтарилди — қайта текширилади
        o.save(update_fields=["excluded", "review"])
    elif action in ("accept", "reject"):
        services.decide([o.pk], accept=action == "accept")
    elif action == "delete":
        o.delete()
    else:
        raise Http404
    services.recheck([o.resource_id])
    services.bump()
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх {code}: {action} ({o.price})")
    return redirect("panel:base_resource", code=code)


# ===================== қамров =====================
@staff_required
def base_coverage(request):
    """База танланган сметани шу вилоят ва даврда қанчалик нархлай олади."""
    jobs = (Job.objects.filter(status=Job.Status.DONE).select_related("batch", "batch__project")
            .defer("result").order_by("-created")[:300])
    job = report = None
    r, y, q = "", None, None
    if request.GET.get("job"):
        try:
            job = Job.objects.filter(status=Job.Status.DONE).select_related("batch").get(pk=request.GET["job"])
        except (Job.DoesNotExist, ValueError, ValidationError):
            raise Http404
        if "region" in request.GET:
            r, y, q = _period(request.GET)
        else:  # биринчи очилганда — смета (ёки унинг объект/папкаси) базага қўшилган давр
            src = _source_of(job)
            if src:
                r, y, q = src.region, src.year, src.quarter
        report = services.coverage(job, r, y, q, own=request.GET.get("own") == "1")
    return render(request, "panel/base_coverage.html", _bctx(
        request, jobs=jobs, job=job, report=report, r=r, y=y, q=q, own=request.GET.get("own") == "1",
        region_labels=REGION_LABELS, **_form_ctx()))


def _source_of(job):
    keys = [("smeta", job.pk)]
    if job.batch_id:
        keys.append(("object", job.batch_id))
        if job.batch.project_id:
            keys.append(("folder", job.batch.project_id))
    for kind, wid in keys:
        s = Source.objects.filter(kind=kind, work_id=wid).first()
        if s:
            return s
    return None


# ===================== текширув навбати =====================
@staff_required
def base_review(request):
    """Шубҳали нархлар: бошқа ном, бошқа бирлик ёки медианадан кескин фарқ — қабул ёки рад қилинмагунча
    медианага кирмайди."""
    flt = request.GET.get("f", "")
    pending = Observation.objects.filter(review=Observation.Review.PENDING)
    counts = {f: pending.filter(flags__contains=f).count() for f in FLAG_LABELS}
    qs = pending.filter(flags__contains=flt) if flt in FLAG_LABELS else pending
    qs = qs.select_related("resource", "source").order_by("resource__code", "price")
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    tables = {}
    for o in page:  # шу вилоят ва даврнинг медианаси (текширувдагилар кирмайди) — солиштириш учун
        cell = (o.source.region, o.source.year, o.source.quarter)
        if cell not in tables:
            tables[cell] = services.price_table(*cell)
        o.cell_median = (tables[cell].get(o.resource.code) or (None, 0))
    return render(request, "panel/base_review.html", _bctx(
        request, page=page, f=flt, counts=counts, total=pending.count(), flag_labels=FLAG_LABELS,
        ratio=services.PRICE_RATIO, region_labels=REGION_LABELS))


@require_POST
@superuser_required
def base_review_decide(request):
    one = request.POST.get("one", "")
    if one:
        pk, _, action = one.partition(":")
        ids = [pk]
    else:
        ids, action = request.POST.getlist("ids"), request.POST.get("action")
    ids = [int(i) for i in ids if str(i).isdigit()]
    if action not in ("accept", "reject") or not ids:
        messages.success(request, "Ҳеч нарса танланмади.")
    else:
        n = services.decide(ids, accept=action == "accept")
        audit(request, Event.Kind.ADMIN_BASE, detail=f"текширув: {n} та нарх {'қабул қилинди' if action == 'accept' else 'рад этилди'}")
        messages.success(request, f"{n} та нарх {'қабул қилинди' if action == 'accept' else 'рад этилди'}.")
    nxt = request.POST.get("next", "")
    return redirect(nxt if _in_panel(nxt) else "panel:base_review")


@require_POST
@superuser_required
def base_recheck(request):
    n = services.recheck()
    audit(request, Event.Kind.ADMIN_BASE, detail=f"база қайта текширилди: {n} та нарх белгиси ўзгарди")
    messages.success(request, f"Қайта текширилди: {n} та нархнинг белгиси ўзгарди.")
    return redirect("panel:base_review")


# ===================== манбалар =====================
@staff_required
def base_sources(request):
    flt = request.GET.get("f", "")
    qs = Source.objects.select_related("owner").annotate(n=Count("observations")).order_by("-created")
    if flt == "unknown":
        qs = qs.filter(Q(year__isnull=True) | Q(quarter__isnull=True) | Q(region=""))
    elif flt in REGION_LABELS:
        qs = qs.filter(region=flt)
    added = {(k, str(w)) for k, w in Source.objects.values_list("kind", "work_id")}
    pending = _pending_works(added)
    return render(request, "panel/base_sources.html", _bctx(
        request, page=Paginator(qs, 40).get_page(request.GET.get("page")), f=flt, pending=pending,
        region_labels=REGION_LABELS, skip_labels=services.SKIP_LABELS, **_form_ctx()))


def _pending_works(added) -> list:
    """Нархланган, лекин ҳали базага қўшилмаган ишлар (папкалар, алоҳида объект ва сметалар)."""
    out = []
    for p in Project.objects.filter(parts__jobs__status=Job.Status.DONE).distinct().select_related("owner"):
        if ("folder", str(p.id)) not in added:
            out.append(("folder", p))
    for b in Batch.objects.filter(project__isnull=True, jobs__status=Job.Status.DONE).distinct().select_related("owner"):
        if ("object", str(b.id)) not in added:
            out.append(("object", b))
    for j in Job.objects.filter(batch__isnull=True, status=Job.Status.DONE).select_related("owner").defer("result"):
        if ("smeta", str(j.id)) not in added:
            out.append(("smeta", j))
    return out


@require_POST
@superuser_required
def base_add(request, kind, work_id):
    if kind not in Source.Kind.values:
        raise Http404
    region, year, quarter = _period(request.POST)
    try:
        source = services.add_source(kind, work_id, region=region, year=year, quarter=quarter, user=request.user)
    except (Project.DoesNotExist, Batch.DoesNotExist, Job.DoesNotExist):
        raise Http404
    audit(request, Event.Kind.ADMIN_BASE, work_kind=kind, detail=f"базага қўшилди: {source.title} — "
          f"{source.stats.get('taken', 0)} та нарх, {source.get_region_display() or 'вилоят номаълум'}, {source.period_label}")
    messages.success(request, f"«{source.title}» базага қўшилди: {source.stats.get('taken', 0)} та нарх.")
    nxt = request.POST.get("next", "")
    return redirect(nxt if _in_panel(nxt) else "panel:base_sources")


@require_POST
@superuser_required
def base_add_all(request):
    added = {(k, str(w)) for k, w in Source.objects.values_list("kind", "work_id")}
    n = taken = 0
    for kind, w in _pending_works(added):
        s = services.add_source(kind, w.pk, user=request.user)  # вилоят — матндан, давр — номаълум
        n += 1
        taken += s.stats.get("taken", 0)
    audit(request, Event.Kind.ADMIN_BASE, detail=f"ҳаммаси базага қўшилди: {n} та иш, {taken} та нарх")
    messages.success(request, f"{n} та иш қўшилди, {taken} та нарх. Давр номаълум — ҳар бирини белгиланг.")
    return redirect("panel:base_sources")


@require_POST
@superuser_required
def base_source(request, pk, action):
    s = get_object_or_404(Source, pk=pk)
    if action == "edit":
        s.region, s.year, s.quarter = _period(request.POST)
        s.save(update_fields=["region", "year", "quarter"])
        services.bump()
        detail = f"манба даври ўзгарди: {s.title} — {s.get_region_display() or '—'}, {s.period_label}"
    elif action == "reharvest":
        services.harvest(s)
        detail = f"манба қайта йиғилди: {s.title} — {s.stats.get('taken', 0)} та нарх"
    elif action == "delete":
        detail = f"манба ўчирилди: {s.title} ({s.observations.count()} та нарх)"
        services.delete_source(s)
    else:
        raise Http404
    audit(request, Event.Kind.ADMIN_BASE, detail=detail)
    nxt = request.POST.get("next", "")
    return redirect(nxt if _in_panel(nxt) else "panel:base_sources")


def work_base_ctx(kind, work) -> dict:
    """Панелдаги иш саҳифаси учун: базага қўшилганми, бўлмаса — вилоят таклифи."""
    source = Source.objects.filter(kind=kind, work_id=work.pk).first()
    ctx = {"base_source": source, "base_region_labels": REGION_LABELS,
           "base_ok": source is not None or bool(services.jobs_of(kind, work)), **_form_ctx()}
    if source is None and ctx["base_ok"]:
        ctx["base_hint"] = services.region_suggestion(kind, work)
    return ctx
