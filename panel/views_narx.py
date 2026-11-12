"""Панел → «Нарх базаси (янги)»: миллий кодсиз, номи + бирлиги бўйича; ҳар ресурснинг паспорти (ҳар нарх қаердан,
ким киритди) ва паст / ўрта / юқори нарх. Эски «Ресурс базаси» алоҳида туради. Кўриш — ``is_staff``,
ўзгартириш — ``is_superuser`` (журналга ёзилади)."""
import json
from datetime import date

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, F as models_F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from jobs.models import Batch as JobBatch, Job, Project
from narxbaza import services
from narxbaza.models import CATEGORIES, CATEGORY_LABELS, Batch, Item, Price, Stat
from resbase.regions import REGION_LABELS, REGIONS

from .audit import log as audit
from .models import Event
from .views import _ctx, staff_required, superuser_required

KINDS = ("folder", "object", "smeta")


def _nctx(request, ntab, **kw):
    from narxbaza.models import LEVELS, Config
    from resbase.models import BaseConfig
    y = timezone.now().year
    return _ctx(request, "narx", ntab=ntab, n_pending=len(services.pending_works()), regions=REGIONS,
                categories=CATEGORIES, region_labels=REGION_LABELS, today=timezone.localdate(), ncfg=Config.get(),
                levels=LEVELS, bcfg=BaseConfig.get(), base_years=list(range(y + 1, 2017, -1)), **kw)


def _day(v):
    try:
        d = date.fromisoformat((v or "").strip())
    except ValueError:
        return None
    return d if date(2000, 1, 1) <= d <= timezone.localdate() else None


def _region(v):
    return v if v in REGION_LABELS else ""


def _back(request, default):
    nxt = request.POST.get("next", "")
    return redirect(nxt if nxt.startswith(reverse("panel:narx")) else default)


def _years():
    return sorted(set(Stat.objects.values_list("year", flat=True)), reverse=True)


# ===================== ресурслар рўйхати =====================
SORTS = [("", "Тоифалар бўйича"), ("name", "Номи (А–Я)"), ("cheap", "Арзонидан"), ("dear", "Қимматидан"),
         ("most", "Нархи кўпидан"), ("new", "Янги қўшилганлар")]
PER_PAGE = (25, 50, 100, 200)
FLAGS = [("multi", "Бир неча нархли"), ("single", "Битта нархли"), ("outlier", "Четда нархи бор"),
         ("manual", "Қўлда киритилгани бор"), ("file", "Файлдан келгани бор")]


def _list_filters(g) -> dict:
    per = int(g["per"]) if g.get("per", "").isdigit() and int(g["per"]) in PER_PAGE else 50
    return {"q": g.get("q", "").strip()[:100], "cat": g.get("cat", "") if g.get("cat", "") in CATEGORY_LABELS else "",
            "region": _region(g.get("region", "")), "year": int(g["year"]) if g.get("year", "").isdigit() else None,
            "sort": g.get("sort", "") if g.get("sort", "") in dict(SORTS) else "", "per": per,
            "flag": g.get("flag", "") if g.get("flag", "") in dict(FLAGS) else ""}


def _url(f, **change) -> str:
    from django.utils.http import urlencode
    d = {**f, **change}
    d.pop("page", None)
    return "?" + urlencode({k: v for k, v in d.items() if v and not (k == "per" and v == 50)})


SHORT = {"I": "Иш ҳақи", "J": "Машина-механизм", "K": "Материал", "M": "Ускуна", "L": "Кабель", "O": "Бошқа"}


def _cat_cards(f, cat_n, cover) -> list:
    """Тоифа карточкалари: ресурслар (жорий қидирув/фильтр бўйича), нархлар ва база нархлай олиш фоизи."""
    prices = {k: p for k, _, _, p in services.category_stats()}
    cov = {k: pct for k, _, t, c, pct in cover["by_cat"]}
    return [{"k": k, "label": SHORT[k], "full": label, "n": cat_n.get(k, 0), "prices": prices.get(k, 0),
             "cover": cov.get(k), "url": _url(f, cat="" if f["cat"] == k else k)} for k, label in CATEGORIES]


@staff_required
def narx_list(request):
    from django.db.models import OuterRef, Subquery
    f = _list_filters(request.GET)
    qs = Item.objects.annotate(n=Count("prices"))
    for w in f["q"].split():
        qs = qs.filter(Q(name__icontains=w) | Q(key__icontains=services.fold_name(services.norm_name(w))))
    st = Stat.objects.filter(item=OuterRef("pk"))
    if f["region"]:
        st = st.filter(region=f["region"])
    if f["year"]:
        st = st.filter(year=f["year"])
    if f["region"] or f["year"]:
        qs = qs.filter(pk__in=Stat.objects.filter(**{k: v for k, v in (("region", f["region"]), ("year", f["year"])) if v})
                       .values("item_id"))
    if f["flag"] == "multi":
        qs = qs.filter(pk__in=Stat.objects.exclude(low=models_F("high")).values("item_id"))
    elif f["flag"] == "single":
        qs = qs.exclude(pk__in=Stat.objects.exclude(low=models_F("high")).values("item_id"))
    elif f["flag"] == "outlier":
        qs = qs.filter(prices__outlier=True, prices__excluded=False).distinct()
    elif f["flag"] in ("manual", "file"):
        qs = qs.filter(prices__batch__kind=f["flag"]).distinct()
    cat_n = dict(qs.order_by().values_list("category").annotate(c=Count("id", distinct=True)))
    all_n = sum(cat_n.values())
    if f["cat"]:
        qs = qs.filter(category=f["cat"])
    qs = qs.annotate(mid=Subquery(st.order_by("-year", "-n").values("mid")[:1]))
    order = {"": ("category", "name"), "name": ("name",), "cheap": ("mid", "name"), "dear": ("-mid", "name"),
             "most": ("-n", "name"), "new": ("-created",)}[f["sort"]]
    if f["sort"] == "cheap":
        from django.db.models import F
        qs = qs.order_by(F("mid").asc(nulls_last=True), "name")
    elif f["sort"] == "dear":
        from django.db.models import F
        qs = qs.order_by(F("mid").desc(nulls_last=True), "name")
    else:
        qs = qs.order_by(*order)
    page = Paginator(qs, f["per"]).get_page(request.GET.get("page"))
    rows = list(page)
    best = services.best_stat([i.pk for i in rows], f["region"], f["year"])
    for i in rows:
        i.st = best.get(i.pk)
    # чап фильтр: вилоятлар ва йиллар (шу тоифа/қидирув бўйича эмас — бутун база)
    reg_n = dict(Stat.objects.values_list("region").annotate(c=Count("item", distinct=True)))
    year_qs = Stat.objects.filter(region=f["region"]) if f["region"] else Stat.objects.all()
    years = list(year_qs.values_list("year").annotate(c=Count("item", distinct=True)).order_by("-year"))
    cover = services.coverage()
    pages = []
    if page.has_other_pages():
        last = 0
        for n in page.paginator.page_range:
            if n in (1, page.paginator.num_pages) or abs(n - page.number) <= 2:
                if last and n - last > 1:
                    pages.append({"n": None})
                pages.append({"n": n, "url": _url(f) + f"&page={n}"})
                last = n
    return render(request, "panel/narx_list.html", _nctx(
        request, "list", f=f, page=page, rows=rows, counts=services.counts(), sorts=SORTS, per_page=PER_PAGE,
        flags=FLAGS, pages=pages, url_all=_url(f, cat=""), clear_url=reverse("panel:narx"),
        cat_cards=_cat_cards(f, cat_n, cover), all_n=all_n,
        region_opts=[(k, label, reg_n.get(k, 0), _url(f, region="" if f["region"] == k else k, year=""))
                     for k, label in REGIONS if reg_n.get(k)],
        year_opts=[(y, n, _url(f, year="" if f["year"] == y else y)) for y, n in years],
        flag_opts=[(k, label, _url(f, flag="" if f["flag"] == k else k)) for k, label in FLAGS],
        region_any=_url(f, region="", year=""), cover=cover,
        file_kinds=services.FILE_KINDS, sort_label=dict(SORTS)[f["sort"]]))


# ===================== паспорт =====================
@staff_required
def narx_item(request, pk):
    it = get_object_or_404(Item, pk=pk)
    prices = list(it.prices.select_related("batch", "batch__owner", "batch__added_by").order_by("-date", "-id"))
    stats = list(it.stats.all())
    variants = sorted({(p.name, p.unit) for p in prices if (p.name, p.unit) != (it.name, it.unit)})
    return render(request, "panel/narx_item.html", _nctx(
        request, "list", it=it, prices=prices, stats=stats, variants=variants,
        n_live=sum(1 for p in prices if not p.excluded and not p.outlier),
        last_region=prices[0].region if prices else ""))


@require_POST
@superuser_required
def narx_item_edit(request, pk):
    it = get_object_or_404(Item, pk=pk)
    name = request.POST.get("name", "").strip()[:500]
    cat = request.POST.get("category", "")
    if name:
        it.name = name
    if cat in CATEGORY_LABELS:
        it.category = cat
    it.edited = True
    it.save()
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: ресурс тузатилди — {it.name} ({it.get_category_display()})")
    messages.success(request, "Сақланди.")
    return redirect("panel:narx_item", pk=pk)


@require_POST
@superuser_required
def narx_price(request, pk, action):
    p = get_object_or_404(Price, pk=pk)
    if action not in ("exclude", "include"):
        raise Http404
    services.set_excluded(p, action == "exclude", request.POST.get("note", "").strip())
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: {p.item.name} — {p.price} "
          f"{'истисно қилинди' if action == 'exclude' else 'қайтарилди'}")
    return _back(request, reverse("panel:narx_item", args=[p.item_id]))


@require_POST
@superuser_required
def narx_add(request):
    """Қўлда нарх: мавжуд ресурсга (item) ёки янги ресурс (номи, бирлиги, тоифаси)."""
    P = request.POST
    item = Item.objects.filter(pk=P.get("item")).first() if P.get("item", "").isdigit() else None
    price, qty = services._dec(P.get("price", "").replace(" ", "").replace(",", ".")), \
        services._dec(P.get("qty", "").replace(" ", "").replace(",", ".")) if P.get("qty") else None
    region, day = _region(P.get("region")), _day(P.get("date"))
    name, unit = P.get("name", "").strip(), P.get("unit", "").strip()
    back = reverse("panel:narx_item", args=[item.pk]) if item else reverse("panel:narx")
    if price is None or price <= 0 or not region or not day or (item is None and not (name and unit)):
        messages.error(request, "Нарх, вилоят ва санани (бугундан кейин эмас) киритинг; янги ресурсга — номи ва бирлиги ҳам.")
        return redirect(back)
    p = services.add_manual(name=name, unit=unit, category=P.get("category", "O"), price=price, qty=qty, region=region,
                            day=day, note=P.get("note", "").strip(), user=request.user, item=item)
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: қўлда — {p.item.name}: {p.price}, {REGION_LABELS[region]}, {day}")
    messages.success(request, f"«{p.item.name}» га нарх қўшилди.")
    return redirect("panel:narx_item", pk=p.item_id)


# ===================== йиғиш (тасдиқлаш) =====================
@staff_required
def narx_collect(request):
    pending = services.pending_works()
    for kind, w in pending:
        w.kind, w.kind_label = kind, {"folder": "Папка", "object": "Объект", "smeta": "Смета"}[kind]
        w.title = services.title_of(kind, w)
    return render(request, "panel/narx_collect.html", _nctx(request, "collect", pending=[w for _, w in pending]))


def _work_or_404(kind, work_id):
    if kind not in KINDS:
        raise Http404
    try:
        return services.work_of(kind, work_id)
    except (Project.DoesNotExist, JobBatch.DoesNotExist, Job.DoesNotExist):
        raise Http404


@staff_required
def narx_review(request, kind, work_id):
    """Смета­чи ресурсларини кўриб тасдиқлаш: аниқ нархланганлари белгиланган, тахминийлари — йўқ."""
    work = _work_or_404(kind, work_id)
    jobs = services.jobs_of(kind, work)
    rows = services.rows_from_jobs(jobs)
    if request.method == "POST":
        if not request.user.is_superuser:
            raise Http404
        region, day = _region(request.POST.get("region")), _day(request.POST.get("date"))
        if request.POST.get("sig") != services.signature(rows):
            messages.error(request, "Смета шу орада қайта нархланган — рўйхат янгиланди, қайта белгиланг.")
            return redirect(request.path)
        if not region or not day:
            messages.error(request, "Вилоят ва санани танланг (сана бугундан кейин бўлмасин).")
            return redirect(request.path)
        chosen = _picked(request, rows)
        if not chosen:
            messages.error(request, "Битта ҳам нарх белгиланмади.")
            return redirect(request.path)
        b = services.add_batch(kind, work, chosen, region=region, day=day, user=request.user,
                               note=request.POST.get("note", "").strip()[:300])
        audit(request, Event.Kind.ADMIN_BASE, work_kind=kind, detail=f"нарх базаси: «{b.title}» — {len(chosen)} та нарх, "
              f"{REGION_LABELS[region]}, {day}")
        messages.success(request, f"«{b.title}» киритилди: {len(chosen)} та нарх, {b.stats.get('new', 0)} та янги ресурс.")
        return redirect("panel:narx_batch", pk=b.pk)
    region, hint = services.old.region_suggestion(kind, work)
    return render(request, "panel/narx_review.html", _review_ctx(
        request, rows, title=services.title_of(kind, work), subtitle=f"{len(jobs)} та смета", owner=work.owner,
        region=region, hint=hint, already=Batch.objects.filter(kind=kind, work_id=work.pk).first(), upload=False))


def _picked(request, rows) -> list:
    """Белгиланган қаторлар (JS «pick» — рақамлар рўйхати); JS ишламаса — фақат аниқлари."""
    try:
        pick = set(json.loads(request.POST.get("pick") or "null"))
    except (TypeError, ValueError):
        pick = None
    return [r for r in rows if (r["i"] in pick if pick is not None else r["sure"])]


def _review_ctx(request, rows, **kw) -> dict:
    known = set(Item.objects.filter(key__in={r["key"] for r in rows}).values_list("key", flat=True))
    for r in rows:
        r["known"] = r["key"] in known
        r["category_label"] = CATEGORY_LABELS[r["category"]]
    cats = {k: sum(1 for r in rows if r["category"] == k) for k in CATEGORY_LABELS}
    return _nctx(request, "list" if kw.get("upload") else "collect", rows=rows, sig=services.signature(rows),
                 n_sure=sum(1 for r in rows if r["sure"]), n_new=sum(1 for r in rows if not r["known"]),
                 cat_split=[(k, CATEGORY_LABELS[k], n) for k, n in cats.items() if n], **kw)


# ===================== киритилган тўпламлар =====================
@staff_required
def narx_batches(request):
    qs = Batch.objects.select_related("owner", "added_by").annotate(n=Count("prices")).order_by("-created")
    k = request.GET.get("k", "")
    if k == "manual":
        qs = qs.filter(kind=Batch.Kind.MANUAL)
    elif k == "work":
        qs = qs.exclude(kind=Batch.Kind.MANUAL)
    return render(request, "panel/narx_batches.html", _nctx(
        request, "batches", page=Paginator(qs, 40).get_page(request.GET.get("page")), k=k))


@staff_required
def narx_batch(request, pk):
    b = get_object_or_404(Batch.objects.select_related("owner", "added_by"), pk=pk)
    prices = list(b.prices.select_related("item").order_by("item__name"))
    return render(request, "panel/narx_batch.html", _nctx(request, "batches", b=b, prices=prices))


@require_POST
@superuser_required
def narx_batch_delete(request, pk):
    b = get_object_or_404(Batch, pk=pk)
    title, n = b.title, b.prices.count()
    services.delete_batch(b)
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: тўплам ўчирилди — «{title}», {n} та нарх")
    messages.success(request, f"«{title}» ўчирилди ({n} та нарх). Нархлар қайта ҳисобланди.")
    return redirect("panel:narx_batches")


@staff_required
def narx_how(request):
    return render(request, "panel/narx_how.html", _nctx(request, "how"))


# ===================== файлдан қўшиш =====================
UPLOAD_DIR = "narxbaza"  # MEDIA_ROOT ичида: юкланган файл нусхалари ва кўриб чиқиш учун JSON


def _udir():
    from pathlib import Path
    from django.conf import settings
    d = Path(settings.MEDIA_ROOT) / UPLOAD_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


@require_POST
@superuser_required
def narx_upload(request):
    import uuid
    from jobs.services import UploadError, _check_upload, _save
    f, kind = request.FILES.get("file"), request.POST.get("kind", "")
    if not f or kind not in dict(services.FILE_KINDS):
        messages.error(request, "Файл ва унинг турини танланг.")
        return redirect("panel:narx")
    try:
        ext = _check_upload(f, f.name)
    except UploadError as e:
        messages.error(request, str(e))
        return redirect("panel:narx")
    token = uuid.uuid4().hex
    path = _udir() / f"{token}{ext}"
    _save(f, path)
    try:
        rows = services.parse_file(path, kind, f.name, request.POST.get("category", "K"))
    except Exception as e:  # noqa: BLE001 — бузуқ файл
        path.unlink(missing_ok=True)
        messages.error(request, f"Файлни ўқиб бўлмади: {e}")
        return redirect("panel:narx")
    if not rows:
        path.unlink(missing_ok=True)
        messages.error(request, "Файлда нархли ресурс топилмади." + (
            " Оддий Excel'да сарлавҳа қатори бўлсин: «Наименование» (номи), «Ед. изм.» (бирлиги), «Цена» (нархи)."
            if kind == "excel" else " RES файлда нархли ресурс жадвали бўлиши керак."))
        return redirect("panel:narx")
    data = {"file": f.name, "kind": kind, "path": f"{UPLOAD_DIR}/{path.name}",
            "rows": [{**r, "price": str(r["price"]), "qty": str(r["qty"])} for r in rows]}
    (_udir() / f"{token}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return redirect("panel:narx_upload_review", token=token)


def _pending_upload(token):
    if not token.isalnum():
        raise Http404
    p = _udir() / f"{token}.json"
    if not p.is_file():
        raise Http404("Юклаш топилмади ёки эскирган — файлни қайта юкланг")
    data = json.loads(p.read_text(encoding="utf-8"))
    from decimal import Decimal
    for r in data["rows"]:
        r["price"], r["qty"] = Decimal(r["price"]), Decimal(r["qty"])
    return p, data


@superuser_required
def narx_upload_review(request, token):
    from django.conf import settings
    from pathlib import Path
    p, data = _pending_upload(token)
    rows = data["rows"]
    if request.method == "POST":
        if request.POST.get("action") == "cancel":
            (Path(settings.MEDIA_ROOT) / data["path"]).unlink(missing_ok=True)
            p.unlink(missing_ok=True)
            messages.success(request, "Бекор қилинди — ҳеч нарса қўшилмади.")
            return redirect("panel:narx")
        region, day = _region(request.POST.get("region")), _day(request.POST.get("date"))
        if not region or not day:
            messages.error(request, "Вилоят ва санани танланг (сана бугундан кейин бўлмасин).")
            return redirect(request.path)
        chosen = _picked(request, rows)
        if not chosen:
            messages.error(request, "Битта ҳам нарх белгиланмади.")
            return redirect(request.path)
        b = services.add_batch(Batch.Kind.FILE, None, chosen, region=region, day=day, user=request.user,
                               note=request.POST.get("note", "").strip()[:300], title=data["file"], file_path=data["path"])
        p.unlink(missing_ok=True)
        audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: файл «{b.title}» — {len(chosen)} та нарх, "
              f"{REGION_LABELS[region]}, {day}")
        messages.success(request, f"«{b.title}» киритилди: {len(chosen)} та нарх, {b.stats.get('new', 0)} та янги ресурс.")
        return redirect("panel:narx_batch", pk=b.pk)
    return render(request, "panel/narx_review.html", _review_ctx(
        request, rows, title=data["file"], subtitle=dict(services.FILE_KINDS)[data["kind"]].split(":")[0],
        owner=request.user, region="", hint="", already=None, upload=True))


@staff_required
def narx_batch_file(request, pk):
    from django.conf import settings
    from django.http import FileResponse
    from pathlib import Path
    b = get_object_or_404(Batch, pk=pk)
    path = (Path(settings.MEDIA_ROOT) / b.file_path) if b.file_path else None
    if not path or not path.is_file():
        raise Http404("Файл сақланмаган")
    return FileResponse(open(path, "rb"), as_attachment=True, filename=b.title)


@require_POST
@superuser_required
def narx_item_delete(request, pk):
    it = get_object_or_404(Item, pk=pk)
    name, n = it.name, it.prices.count()
    services.delete_item(it)
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: ресурс ўчирилди — {name} ({n} та нарх)")
    messages.success(request, f"«{name}» ва унинг {n} та нархи ўчирилди.")
    return redirect("panel:narx")


@require_POST
@superuser_required
def narx_config(request):
    """Смета­чи танламаса — қайси вилоят, йил ва нарх (паст / ўрта / юқори). Нархлашда доим шу база ишлайди."""
    from narxbaza.models import LEVEL_LABELS, Config
    from resbase.models import BaseConfig
    from resbase.services import parse_period, period_label
    cfg = Config.get()
    if "enabled" in request.POST:  # ёқиш / ўчириш тугмаси
        cfg.enabled = request.POST["enabled"] == "1"
        cfg.updated_by = request.user
        cfg.save()
        audit(request, Event.Kind.ADMIN_BASE, detail="нарх базасидан нархлаш " + ("ёқилди" if cfg.enabled else "ўчирилди"))
        messages.success(request, "Базадан нархлаш ёқилди." if cfg.enabled else
                         "Базадан нархлаш ўчирилди — сметалар фақат RES файл билан нархланади.")
        return _back(request, reverse("panel:narx"))
    if request.POST.get("level") in LEVEL_LABELS:
        cfg.level = request.POST["level"]
    cfg.updated_by = request.user
    cfg.save()
    if "region" in request.POST or "year" in request.POST:
        p = parse_period(request.POST)
        bc = BaseConfig.get()
        bc.region, bc.year, bc.quarter = p["region"], p["year"], None
        bc.updated_by = request.user
        bc.save()
    label = period_label(BaseConfig.get().period) or "вилоят ва йил танланмаган"
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси сукути: {label}, {LEVEL_LABELS[cfg.level].lower()}")
    messages.success(request, f"Сақланди: смета­чи танламаса — {label}, {LEVEL_LABELS[cfg.level].lower()}.")
    return _back(request, reverse("panel:narx"))
