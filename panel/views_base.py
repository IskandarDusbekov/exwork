"""Панел → «Ресурс базаси»: йиғилган кодлар ва нархлар, манбалар, қидирув, таҳрирлаш. Кўриш — ``is_staff``,
ўзгартириш — фақат ``is_superuser`` (ҳаммаси журналга ёзилади). Ҳозирча синов учун — ҳаммасини ўчириш мумкин."""
import re
import uuid
from collections import Counter

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from jobs.models import Batch, Job, Project
from jobs.services import UploadError, _check_upload, _save
from resbase import normbook, resfile, services
from resbase.models import CATEGORIES, FLAG_LABELS, QUARTERS, BaseConfig, Observation, Resource, Source, WorkItem
from resbase.regions import REGION_LABELS, REGIONS

from . import conf
from .audit import log as audit
from .models import Event
from .views import _ctx, staff_required, superuser_required


def _bctx(request, **kw):
    """«Ресурс базаси» саҳифалари учун: умумий контекст + текширувдаги нархлар сони (таб белгиси)."""
    if "n_offers" not in kw:
        kw["n_offers"] = len(_pending_works({(k, str(w)) for k, w in Source.objects.values_list("kind", "work_id")}))
    return _ctx(request, "base", n_review=Observation.objects.filter(review=Observation.Review.PENDING).count(), **kw)


def _howto_ctx() -> dict:
    """«База қандай йиғилади» схемаси учун жонли рақамлар."""
    files = list(Source.objects.filter(kind=Source.Kind.FILE).values_list("stats", flat=True))
    return {
        "n_res_files": sum(1 for st in files if (st or {}).get("sheets")),
        "n_lrv_files": sum(1 for st in files if (st or {}).get("lrv_sheets")),
        "n_smeta": Source.objects.exclude(kind=Source.Kind.FILE).count(),
        "n_resources": Resource.objects.count(), "n_obs": Observation.objects.count(),
        "n_periods": Observation.objects.values("source__region", "source__year", "source__quarter").distinct().count(),
        "n_norms": WorkItem.objects.values("shifr").distinct().count(), "n_works": WorkItem.objects.count(),
        "n_books": WorkItem.objects.values("book").distinct().count(),
    }


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
    return render(request, "panel/base_overview.html", _bctx(
        request, cfg=cfg, periods=periods, table=table,
        n_resources=Resource.objects.count(), n_obs=Observation.objects.count(), n_sources=Source.objects.count(),
        n_norms=WorkItem.objects.values("shifr").distinct().count(),
        n_unknown=Source.objects.filter(Q(year__isnull=True) | Q(quarter__isnull=True) | Q(region="")).count(),
        n_pending=Observation.objects.filter(review=Observation.Review.PENDING).count(), **_form_ctx()))


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
    obs = list(res.observations.select_related("source", "source__owner")
               .order_by("-source__year", "-source__quarter", "price"))
    medians = services.resource_medians(res)
    works = services.resource_works(res.code)
    return render(request, "panel/base_resource.html", _bctx(
        request, res=res, obs=obs, medians=medians, names=res.names.all()[:30], works=works,
        latest=next((m for m in medians if m["median"]), None), n_pending=sum(1 for o in obs if o.review == "pending"),
        cat_short=_CAT_SHORT, region_labels=REGION_LABELS, **_form_ctx()))


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
    # нарх ва иш сонлари — алоҳида подсўровда (иккала жадвални бир JOIN'да санаш қаторларни кўпайтириб юборади)
    from django.db.models import IntegerField, OuterRef, Subquery
    from resbase.models import WorkItem as _W

    def _cnt(model):
        return Subquery(model.objects.filter(source=OuterRef("pk")).order_by().values("source")
                        .annotate(c=Count("id")).values("c"), output_field=IntegerField())
    qs = Source.objects.select_related("owner").annotate(n=_cnt(Observation), w=_cnt(_W)).order_by("-created")
    if flt == "unknown":
        qs = qs.filter(Q(year__isnull=True) | Q(quarter__isnull=True) | Q(region=""))
    elif flt in ("file", "smeta"):
        qs = qs.filter(kind=Source.Kind.FILE) if flt == "file" else qs.exclude(kind=Source.Kind.FILE)
    added = {(k, str(w)) for k, w in Source.objects.values_list("kind", "work_id")}
    pending = _pending_works(added)
    cfg = BaseConfig.get()
    return render(request, "panel/base_sources.html", _bctx(
        request, page=Paginator(qs, 40).get_page(request.GET.get("page")), f=flt, pending=pending, n_offers=len(pending),
        n_all=Source.objects.count(), n_file=Source.objects.filter(kind=Source.Kind.FILE).count(),
        n_smeta=Source.objects.exclude(kind=Source.Kind.FILE).count(),
        n_unknown=Source.objects.filter(Q(year__isnull=True) | Q(quarter__isnull=True) | Q(region="")).count(),
        r=cfg.region, y=cfg.year, q=cfg.quarter, region_labels=REGION_LABELS,
        skip_labels=services.FILE_SKIP_LABELS | services.SKIP_LABELS, **_form_ctx()))


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
    if kind not in Source.Kind.values or kind == Source.Kind.FILE:
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


SKIP_HELP = {  # «нега олинмади» — оддий тилда
    "duplicate": "Шу сметада шу код яна учраган — бир сметадан бир код бир марта олинади (масалан, «Затраты труда» ҳар иш остида).",
    "code_conflict": "Смета­чи битта кодни бир неча бошқа ресурсга берган (номи ҳам, нархи ҳам бошқа) — қайси бири ҳақиқий "
                     "код эканини билиб бўлмайди, шу коднинг бирорта нархи олинмайди.",
    "not_exact": "Локалдаги ресурс RES билан аниқ мос келмаган (ўхшаш ёки топилмаган) — нархи ишончсиз, олинмайди.",
    "no_code": "Кодсиз ресурс (маҳаллий материал ёки иш сарлавҳаси) — ҳозирча олинмайди.",
    "zero_price": "Нархи 0 ёки йўқ.",
    "no_price": "Нархи матн («--») — олинмайди.",
    "total": "ИТОГО / ВСЕГО қатори.",
    "header": "Такрорий сарлавҳа.",
    "bad_unit": "Бирлиги сон — устун сурилган қатор.",
}


@staff_required
def base_source_detail(request, pk):
    """Манба: нима ўқилди, нечта нарх ва иш олинди, нимаси нега олинмади — файллар бўйича."""
    s = get_object_or_404(Source.objects.select_related("owner", "added_by"), pk=pk)
    st = s.stats or {}
    skipped = sorted(((k, v, SKIP_HELP.get(k, k)) for k, v in (st.get("skipped") or {}).items()), key=lambda t: -t[1])
    books = sorted(((k, v, services.book_label(k)) for k, v in (st.get("books") or {}).items()),
                   key=lambda t: normbook.book_sort(t[0]))
    pair = list(services._group(s).exclude(pk=s.pk)) if s.pair_key else []
    return render(request, "panel/base_source.html", _bctx(
        request, s=s, st=st, skipped=skipped, books=books, pair=pair, detail=st.get("detail") or [],
        n_obs=s.observations.count(), n_works=s.works.count(), skip_labels=services.FILE_SKIP_LABELS | services.SKIP_LABELS,
        region_labels=REGION_LABELS))


@require_POST
@superuser_required
def base_add_selected(request):
    """Админ танлаган нархланган сметалар (таклифлар) — бир давр билан базага киритилади."""
    region, year, quarter = _period(request.POST)
    items = [i.partition(":") for i in request.POST.getlist("items")]
    if not items:
        messages.success(request, "Ҳеч нарса танланмади.")
        return redirect("panel:base_sources")
    if not (region and year and quarter):
        messages.success(request, "Вилоят, йил ва чоракни танланг — нархлар шу даврга ёзилади.")
        return redirect("panel:base_sources")
    n = taken = 0
    for kind, _, wid in items:
        if kind not in (Source.Kind.FOLDER, Source.Kind.OBJECT, Source.Kind.SMETA):
            continue
        try:
            s = services.add_source(kind, wid, region=region, year=year, quarter=quarter, user=request.user)
        except (Project.DoesNotExist, Batch.DoesNotExist, Job.DoesNotExist, ValidationError, ValueError):
            continue
        n += 1
        taken += s.stats.get("taken", 0)
    label = services.period_label({"region": region, "year": year, "quarter": quarter})
    audit(request, Event.Kind.ADMIN_BASE, detail=f"танланган сметалар базага киритилди: {n} та, {taken} та нарх, {label}")
    messages.success(request, f"{n} та смета базага киритилди: {taken} та нарх ({label}).")
    return redirect("panel:base_sources")


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
        services.set_period(s, *_period(request.POST))
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


# ===================== RES файл юклаш =====================
_RESULTS = "resbase_upload_results"  # сессияда: охирги юклаш натижалари (PRG)
_PENDING = "resbase_upload_pending"  # сессияда: шу даврда такрор чиққан, қарор кутаётган файллар {token: {...}}


def _dup_info(sources) -> list:
    return [{"id": s.pk, "title": s.title, "period": f"{s.get_region_display() or '—'} · {s.period_label}",
             "date": s.created.strftime("%d.%m.%Y"), "n": s.stats.get("taken", 0)} for s in sources]


def _ingest(request, f, region, year, quarter) -> dict:
    """Битта юкланган файл: сақлаш, ўқиш, такрорни текшириш, базага ёзиш (такрор бўлса — қарор кутади)."""
    out = {"name": f.name}
    try:
        ext = _check_upload(f, f.name)
    except UploadError as e:
        return {**out, "status": "error", "msg": str(e)}
    rel = f"{services.UPLOAD_DIR}/{uuid.uuid4().hex}{ext}"
    path = services._media(rel)
    _save(f, path)
    try:
        parsed = resfile.parse(path)
    except Exception as e:  # noqa: BLE001 — бузуқ ёки ўқиб бўлмайдиган файл
        path.unlink(missing_ok=True)
        return {**out, "status": "error", "msg": f"файлни ўқиб бўлмади: {e}"}
    taken, skipped = services.rows_from_file(parsed)
    ignored = [f"«{s}» — {why}" for s, why in parsed.ignored]
    if not taken and not parsed.works:
        path.unlink(missing_ok=True)
        return {**out, "status": "empty", "ignored": ignored, "skipped": dict(skipped),
                "msg": "кодли нархли ресурс ҳам, норма шифрли иш ҳам топилмади — бу RES ёки ЛРВ файлими?"}
    h = resfile.file_hash(path)
    same, other = services.file_duplicates(h, parsed.fingerprint, region, year, quarter)
    out.update(ignored=ignored, other=_dup_info(other), n=len(taken), skipped=dict(skipped), works=len(parsed.works),
               books=sorted(Counter(w.book for w in parsed.works).items(), key=lambda b: normbook.book_sort(b[0])))
    if same:
        token = uuid.uuid4().hex
        pending = request.session.get(_PENDING, {})
        pending[token] = {"path": rel, "name": f.name, "region": region, "year": year, "quarter": quarter,
                          "same": _dup_info(same), "exact": any(s.file_hash == h for s in same)}
        request.session[_PENDING] = pending
        return {**out, "status": "duplicate", "same": _dup_info(same)}
    s = services.add_file(rel, f.name, parsed, region=region, year=year, quarter=quarter, file_hash=h, user=request.user)
    audit(request, Event.Kind.ADMIN_BASE, detail=f"RES файл юкланди: {f.name} — {s.stats['taken']} та нарх, "
          f"{s.get_region_display()}, {s.period_label}")
    return {**out, "status": "added", "id": s.pk, "n": s.stats["taken"]}


@staff_required
def base_upload(request):
    """Вилоят, йил ва чоракни танлаб, RES Excel файлларни тўғридан-тўғри базага юклаш. Натижа ва такрорлар —
    «Маълумотлар» саҳифасида."""
    if request.method != "POST":
        return redirect("panel:base_data")
    if not request.user.is_superuser:
        raise PermissionDenied("Фақат суперфойдаланувчи")
    region, year, quarter = _period(request.POST)
    files = request.FILES.getlist("files")
    if not (region and year and quarter):
        messages.success(request, "Аввал вилоят, йил ва чоракни танланг.")
    elif not files:
        messages.success(request, "Файл танланмади.")
    else:
        results = [_ingest(request, f, region, year, quarter) for f in files]
        request.session[_RESULTS] = {"period": services.period_label(
            {"region": region, "year": year, "quarter": quarter}), "items": results}
        request.session["resbase_upload_period"] = [region, year, quarter]
        return redirect(reverse("panel:base_data") + "?" + urlencode({"region": region, "year": year, "quarter": quarter}))
    return redirect("panel:base_data")


_PER_PAGE = (25, 50, 100, 200)
_CAT_SHORT = {"I": "ИҲ", "J": "М", "K": "Мт", "L": "К", "M": "У"}  # карточка ва гуруҳ белгиси
_CAT_CARD = {"I": "Иш ҳақи", "J": "Машина-механизм", "K": "Материаллар", "L": "Кабеллар", "M": "Ускуналар"}  # қисқа
_SORTS = [("", "Ресурс тури"), ("code", "Код"), ("name", "Номи"), ("price", "Нарх ↑"), ("-price", "Нарх ↓"),
          ("n", "Кўп учрагани")]


def _data_filters(get) -> dict:
    region, year, quarter = _period(get)
    src = get.get("src", "")
    cat = get.get("cat", "")
    per = int(get["per"]) if get.get("per", "").isdigit() and int(get["per"]) in _PER_PAGE else 50
    return {"region": region, "year": year, "quarter": quarter, "src": int(src) if src.isdigit() else None,
            "cat": cat if cat in dict(CATEGORIES) else "",
            "q": get.get("q", "").strip()[:100], "sort": get.get("sort", "") if get.get("sort") in dict(_SORTS) else "",
            "per": per}


def _link(f: dict, **change) -> str:
    """Жорий фильтрлар + ўзгариш → «?region=…&year=…»; бўш қийматлар ва 1-саҳифа ёзилмайди."""
    p = {**f, **change}
    if p.get("per") == 50:
        p.pop("per")
    if p.get("page") == 1:
        p.pop("page")
    return "?" + urlencode({k: v for k, v in p.items() if v not in (None, "")})


@staff_required
def base_data(request):
    """«Ресурс нархлари»: битта ресурс — битта қатор (ўрта нарх, нечта манбадан, энг арзон–қиммат). Чапда — вилоят →
    давр → файл, иш тури; тепада — файл юклаш."""
    f = _data_filters(request.GET)
    rows, counts = services.resource_rows(f)
    page = Paginator(rows, f["per"]).get_page(request.GET.get("page"))
    total_cat = sum(counts["cat"].values())
    cat_tabs = [{"key": "", "label": "Ҳаммаси", "n": total_cat, "url": _link(f, cat="")}] + [
        {"key": k, "label": _CAT_CARD[k], "title": label, "n": counts["cat"].get(k, 0), "url": _link(f, cat=k)}
        for k, label in CATEGORIES]
    tree = services.explore_tree(f)
    for node in tree:
        node["label"] = REGION_LABELS.get(node["region"], "Вилоят номаълум")
        node["url"] = _link({}, region=node["region"])
        for p in node["periods"]:
            p["label"] = f"{p['year']}-йил · {p['quarter']}-чорак" if p["year"] and p["quarter"] else "давр номаълум"
            p["url"] = _link({}, region=node["region"], year=p["year"], quarter=p["quarter"])
            for s in p.get("sources", []):
                s.url = _link({}, region=node["region"], year=p["year"], quarter=p["quarter"], src=s.pk)
    src = Source.objects.filter(pk=f["src"]).first() if f["src"] else None
    crumbs = [("Ресурс базаси", "?")]
    if f["region"]:
        crumbs.append((REGION_LABELS[f["region"]], _link({}, region=f["region"])))
    if f["year"] and f["quarter"]:
        crumbs.append((f"{f['year']}-{f['quarter']}", _link({}, region=f["region"], year=f["year"], quarter=f["quarter"])))
    if src:
        crumbs.append((src.title, ""))
    cfg = BaseConfig.get()
    r, y, q = request.session.get("resbase_upload_period") or [f["region"] or cfg.region, f["year"] or cfg.year,
                                                               f["quarter"] or cfg.quarter]
    pending = [{"token": t, **p, "period": services.period_label(p)}
               for t, p in request.session.get(_PENDING, {}).items()]
    last = request.session.pop(_RESULTS, None)
    # davr yoki fayl tanlangan bo'lsa — o'sha ustun hamma qatorda bir xil, ko'rsatilmaydi
    show_period = not (f["region"] and f["year"] and f["quarter"])
    show_src = not f["src"]
    pages = [{"n": n, "url": _link(f, page=n) if isinstance(n, int) else ""}
             for n in page.paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1)]
    return render(request, "panel/base_data.html", _bctx(
        request, f=f, page=page, pages=pages, cat_tabs=cat_tabs, tree=tree, crumbs=crumbs, src=src,
        sorts=_SORTS, per_page=_PER_PAGE, r=r, y=y, q=q, pending=pending, last=last,
        show_period=show_period, show_src=show_src, mixed=not (f["region"] and f["year"] and f["quarter"]) and not f["src"],
        cat_short=_CAT_SHORT, cat_counts=counts["cat"], sort_label=dict(_SORTS).get(f["sort"], ""),
        hidden=[(k, f[k]) for k in ("region", "year", "quarter", "src", "cat") if f[k]],
        n_files=Source.objects.filter(kind=Source.Kind.FILE).count(), n_all=Resource.objects.count(),
        n_res=Resource.objects.count(), skip_labels=services.FILE_SKIP_LABELS,
        cat_labels=dict(CATEGORIES), region_labels=REGION_LABELS,
        max_mb=conf.get("max_upload_mb") // (1024 * 1024),
        prev_url=_link(f, page=page.previous_page_number()) if page.has_previous() else "",
        next_url=_link(f, page=page.next_page_number()) if page.has_next() else "",
        clear_url=_link({}, region=f["region"], year=f["year"], quarter=f["quarter"], src=f["src"]),
        **_form_ctx()))


def _book_tabs(f: dict, counts: dict) -> list:
    """Иш тури (тўплам) чиплари: «Ҳаммаси», ҳар тўплам ва «аниқланмаган» (ЛРВ'си йўқ ёки кодда ишлатилмаган)."""
    keys = sorted((k for k in counts if k != "-"), key=normbook.book_sort)
    tabs = [{"key": "", "label": "Ҳаммаси", "n": None, "url": _link(f, book="")}]
    tabs += [{"key": k, "label": k, "title": services.book_label(k), "n": counts[k], "url": _link(f, book=k)} for k in keys]
    if counts.get("-"):
        tabs.append({"key": "-", "label": "аниқланмаган", "title": "Шу файлнинг (ёки жуфтининг) ЛРВ'си юкланмаган "
                     "ёки бу код ундаги ишларда йўқ", "n": counts["-"], "url": _link(f, book="-")})
    return tabs if keys else []


@staff_required
def base_norms(request):
    """Нормалар (ЛРВ ишлари): тўплам (иш тури) → шифр. Бир шифр бир неча файлда учраса — бир қатор, сонлари билан."""
    f = _data_filters(request.GET)
    rows, books = services.norms(f)
    page = Paginator(rows, f["per"]).get_page(request.GET.get("page"))
    period = {k: f[k] for k in ("region", "year", "quarter")}
    for r in page:
        r["url"] = reverse("panel:base_norm", args=[r["shifr"]]) + _link(period)
    pages = [{"n": n, "url": _link(f, page=n) if isinstance(n, int) else ""}
             for n in page.paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1)]
    return render(request, "panel/base_norms.html", _bctx(
        request, f=f, page=page, pages=pages, n_norms=sum(books.values()),
        per_page=_PER_PAGE, region_labels=REGION_LABELS, hidden=[],
        prev_url=_link(f, page=page.previous_page_number()) if page.has_previous() else "",
        next_url=_link(f, page=page.next_page_number()) if page.has_next() else "", **_form_ctx()))


HOW_STEPS = ["Файл юкланади", "Файл тури аниқланади", "RES: нархлар ўқилади", "ЛРВ: ишлар ва нормалар",
             "Ўрта нарх қандай топилади", "Иш тури ва нормалар", "Такрор файл", "Натижа: смета нархланади"]


@staff_required
def base_how(request):
    """«Қандай ишлайди»: база қандай йиғилишини қадамма-қадам, анимация билан кўрсатади (жонли рақамлар билан)."""
    return render(request, "panel/base_how.html", _bctx(request, steps=HOW_STEPS, hw=_howto_ctx(),
                                                        e01=services.book_label("Е01")))


@staff_required
def base_books(request):
    """Тўплам (иш тури) номлари: стандарт ном (тизимда ёзилган) ва админ ёзгани. Бўш қолдирилса — стандарт."""
    from resbase.models import BookName
    if request.method == "POST":
        if not request.user.is_superuser:
            raise PermissionDenied("Фақат суперфойдаланувчи")
        names = {k[5:]: v for k, v in request.POST.items() if k.startswith("name_")}
        names = {k: v for k, v in names.items() if re.fullmatch(r"[ЕЦ]\d{2}|" + normbook.TRANSPORT, k)}
        n = services.save_book_names(names, user=request.user)
        if n:
            audit(request, Event.Kind.ADMIN_BASE, detail=f"тўплам номлари ўзгарди: {n} та")
        messages.success(request, f"Сақланди: {n} та ном ўзгарди." if n else "Ўзгариш йўқ.")
        return redirect("panel:base_books")
    used = dict(WorkItem.objects.values_list("book").annotate(n=Count("shifr", distinct=True)).order_by())
    custom = {b.key: b for b in BookName.objects.select_related("updated_by")}
    rows = [{"key": k, "name": custom[k].name if k in custom else "", "by": custom.get(k), "n": used.get(k, 0)}
            for k in sorted(set(used) | set(custom), key=normbook.book_sort)]
    return render(request, "panel/base_books.html", _bctx(request, rows=rows, n_custom=len(custom)))


@staff_required
def base_norm(request, shifr):
    f = _data_filters(request.GET)
    d = services.norm_detail(shifr, f)
    if not d:
        raise Http404
    return render(request, "panel/base_norm.html", _bctx(
        request, d=d, f=f, book_label=services.book_label(d["book"]), region_labels=REGION_LABELS,
        back_url=reverse("panel:base_norms") + _link({k: f[k] for k in ("region", "year", "quarter")})))


@require_POST
@superuser_required
def base_upload_pending(request, token, action):
    """Такрор файл бўйича қарор: «алмаштириш» — эскиси (шу даврдаги) ўчади, янгиси ёзилади; «ўтказиб юбориш»."""
    pending = request.session.get(_PENDING, {})
    p = pending.pop(token, None)
    request.session[_PENDING] = pending
    if p is None or action not in ("replace", "skip"):
        messages.success(request, "Бу файл бўйича қарор аллақачон қабул қилинган.")
        return redirect("panel:base_data")
    path = services._media(p["path"])
    if action == "skip":
        path.unlink(missing_ok=True)
        messages.success(request, f"«{p['name']}» сақланмади (такрор).")
        return redirect("panel:base_data")
    try:
        parsed = resfile.parse(path)
    except Exception as e:  # noqa: BLE001 — файл ўчирилган ёки бузилган
        path.unlink(missing_ok=True)
        messages.success(request, f"«{p['name']}» ни қайта ўқиб бўлмади: {e}")
        return redirect("panel:base_data")
    h = resfile.file_hash(path)
    same, _ = services.file_duplicates(h, parsed.fingerprint, p["region"], p["year"], p["quarter"])
    old = sum(s.observations.count() for s in same)
    s = services.add_file(p["path"], p["name"], parsed, region=p["region"], year=p["year"], quarter=p["quarter"],
                          file_hash=h, user=request.user, replace=same)
    audit(request, Event.Kind.ADMIN_BASE, detail=f"RES файл алмаштирилди: {p['name']} — эски {len(same)} та манба "
          f"({old} та нарх) ўрнига {s.stats['taken']} та нарх, {s.get_region_display()}, {s.period_label}")
    messages.success(request, f"«{p['name']}» алмаштирилди: {s.stats['taken']} та нарх.")
    return redirect("panel:base_data")


def work_base_ctx(kind, work) -> dict:
    """Панелдаги иш саҳифаси учун: базага қўшилганми, бўлмаса — вилоят таклифи."""
    source = Source.objects.filter(kind=kind, work_id=work.pk).first()
    ctx = {"base_source": source, "base_region_labels": REGION_LABELS,
           "base_ok": source is not None or bool(services.jobs_of(kind, work)), **_form_ctx()}
    if source is None and ctx["base_ok"]:
        ctx["base_hint"] = services.region_suggestion(kind, work)
    return ctx
