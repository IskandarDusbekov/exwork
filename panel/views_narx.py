"""Панел → «Нарх базаси (янги)»: миллий кодсиз, номи + бирлиги бўйича; ҳар ресурснинг паспорти (ҳар нарх қаердан,
ким киритди) ва паст / ўрта / юқори нарх. Эски «Ресурс базаси» алоҳида туради. Кўриш — ``is_staff``,
ўзгартириш — ``is_superuser`` (журналга ёзилади)."""
import json
import os
from pathlib import Path
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
from narxbaza.models import CATEGORIES, CATEGORY_LABELS, Alias, Batch, Item, Log, Price, Stat
from resbase.regions import REGION_LABELS, REGIONS

from .audit import log as audit
from .models import Event
from .views import _ctx, staff_required, superuser_required

KINDS = ("folder", "object", "smeta")


def _nctx(request, ntab, **kw):
    from narxbaza.models import LEVELS, Config
    from resbase.models import BaseConfig
    y = timezone.now().year
    from narxbaza.models import Intake
    n_up = Intake.objects.filter(kind="file", status__in=(Intake.Status.OPEN, Intake.Status.READING,
                                                          Intake.Status.FAILED)).count()
    return _ctx(request, "narx", ntab=ntab, n_pending=len(services.pending_works()), regions=REGIONS, n_up=n_up,
                n_trash=sum(services.trash_counts().values()),
                n_check=sum(services.review_counts().values()),
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


GUTTER = {"I": "Иш ҳақи", "J": "Машина", "K": "Материал", "M": "Ускуна", "L": "Кабель", "O": "Бошқа"}  # Excel қатори ёнида
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
    qs = services.live_items().annotate(n=Count("prices", filter=Q(prices__batch__deleted_at=None)))
    for w in f["q"].split():
        qs = qs.filter(Q(name__icontains=w) | Q(key__icontains=services.name_core(w)))
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
        qs = qs.filter(pk__in=Price.objects.filter(outlier=True, excluded=False).values("item_id"))
    elif f["flag"] in ("manual", "file"):
        qs = qs.filter(pk__in=Price.objects.filter(batch__kind=f["flag"]).values("item_id"))
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
    it = Item.objects.filter(pk=pk).first()
    if it is None:  # бирлаштирилиб ўчган — эски ҳавола қўшилган ресурсга олиб боради
        to = services.merged_into(pk) if not Item.all_objects.filter(pk=pk).exists() else None
        if to is None:
            raise Http404
        messages.info(request, f"Бу ресурс «{to.name}» га бирлаштирилган — шу ерга ўтказилдингиз.")
        return redirect("panel:narx_item", pk=to.pk)
    prices = list(it.prices.select_related("batch", "batch__owner", "batch__added_by").order_by("-date", "-id"))
    stats = list(it.stats.all())
    aliases = list(it.aliases.select_related("added_by"))
    known = {(a.name, a.unit) for a in aliases} | {(it.name, it.unit)}
    variants = sorted({(p.name, p.unit) for p in prices if (p.name, p.unit) not in known})
    mq = request.GET.get("mq", "").strip()[:100]
    similar = services.similar_items(it, mq) if request.user.is_superuser else []
    if similar:
        counts = dict(Price.objects.filter(item__in=similar).values_list("item_id").annotate(c=Count("id")))
        for c in similar:
            c.n = counts.get(c.pk, 0)
    dyn = services.dynamics(stats)
    from narxbaza.models import LEVEL_LABELS, Config
    lv = Config.get().level
    for s in dyn["latest"]:  # нархлашда шу вилоят+йил учун олинадиган нарх (services.price_map билан бир хил)
        v = getattr(s, lv)
        s.pick = v if v is not None else (s.mid if s.mid is not None else s.low)
        s.pick_level = lv if v is not None else ("mid" if s.mid is not None else "low")
        s.example = s.pick * 10
    # айнан бир хил нархлар (қиймати, вилояти, йили, ҳолати) — битта карта, қолганлари «яна N та жойда» ичида
    groups = {}
    for p in prices:
        p.n_files = len(p.places or [])
        p.used = services.counted(p)
        k = (p.price, p.region, p.year, p.excluded, p.outlier, p.pin, p.batch.kind == "manual")
        if k in groups:
            groups[k].same.append(p)
            groups[k].n_all += p.n_files or 1
        else:
            p.same, p.n_all = [], p.n_files or 1
            groups[k] = p
    n_prices = len(prices)
    prices = list(groups.values())
    return render(request, "panel/narx_item.html", _nctx(
        request, "list", n_prices=n_prices, delete_word=DELETE_WORD, it=it, prices=prices, stats=stats, variants=variants, aliases=aliases, mq=mq,
        dyn=dyn, level=lv, level_label=LEVEL_LABELS[lv], compact=True, n_names=len(aliases) + len(variants),
        similar=similar, logs=list(it.logs.select_related("user", "price")[:100]),
        n_live=sum(1 + len(p.same) for p in prices if p.used),
        pin_choices=list(services.PIN_LABELS.items()),
        last_region=prices[0].region if prices else ""))


@require_POST
@superuser_required
def narx_item_edit(request, pk):
    it = get_object_or_404(Item, pk=pk)
    try:
        changed = services.edit_item(it, name=request.POST.get("name", ""), category=request.POST.get("category", ""),
                                     user=request.user)
    except services.Conflict as e:
        messages.error(request, f"«{e.other.name}» ({e.other.unit}) номли ресурс базада бор. Иккаласи бир ресурс "
                                f"бўлса — пастдаги «Бирлаштириш» бўлимидан қўшинг.")
        return redirect(reverse("panel:narx_item", args=[pk]) + f"?mq={e.other.name[:60]}#merge")
    if changed:
        audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: ресурс тузатилди — {it.name} ({it.get_category_display()})")
        messages.success(request, "Сақланди.")
    return redirect("panel:narx_item", pk=pk)


def _num(v):
    return services._dec((v or "").replace(" ", "").replace("\xa0", "").replace(",", "."))


@require_POST
@superuser_required
def narx_price_edit(request, pk):
    """Нархни тузатиш (нархи, сони, вилояти, санаси) — сабаби шарт, эски қиймат тарихда қолади."""
    p = get_object_or_404(Price.objects.select_related("item"), pk=pk)
    P = request.POST
    price, region, day, reason = _num(P.get("price")), _region(P.get("region")), _day(P.get("date")), \
        P.get("reason", "").strip()[:300]
    qty = _num(P.get("qty")) if P.get("qty", "").strip() else None
    back = reverse("panel:narx_item", args=[p.item_id])
    if price is None or price <= 0 or not region or not day or (qty is not None and qty < 0):
        messages.error(request, "Нарх (0 дан катта), вилоят ва санани (бугундан кейин эмас) тўғри киритинг.")
        return _back(request, back)
    if not reason:
        messages.error(request, "Нега тузатилаётганини ёзинг — тарихда сақланади.")
        return _back(request, back)
    old = p.price
    if services.edit_price(p, price=price, qty=qty, region=region, day=day, reason=reason, user=request.user):
        audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: нарх тузатилди — {p.item.name}: "
              f"{services.fmt(old)} → {services.fmt(p.price)} ({reason})")
        messages.success(request, "Нарх тузатилди, паст / ўрта / юқори қайта ҳисобланди.")
    else:
        messages.success(request, "Ҳеч нарса ўзгармади.")
    return _back(request, back)


@require_POST
@superuser_required
def narx_item_merge(request, pk):
    """Бошқа ресурсни (``source``) шу ресурсга қўшиш."""
    it = get_object_or_404(Item, pk=pk)
    src = get_object_or_404(Item, pk=request.POST.get("source") if request.POST.get("source", "").isdigit() else 0)
    title = f"{src.name} ({src.unit})"
    try:
        n = services.merge_items(it, src, request.user)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("panel:narx_item", pk=pk)
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: «{title}» → «{it.name}» га қўшилди, {n} та нарх")
    messages.success(request, f"«{title}» → «{it.name}»: {n} та нарх қўшилди. Унинг номи «бошқа номи» бўлиб қолди — "
                              f"шу ном билан келган нархлар энди шу ерга тушади.")
    return _back(request, reverse("panel:narx_item", args=[pk]))


@require_POST
@superuser_required
def narx_alias_delete(request, pk, alias):
    al = get_object_or_404(Alias, pk=alias, item_id=pk)
    services.remove_alias(al, request.user)
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: бошқа номи олиб ташланди — {al.name}")
    messages.success(request, f"«{al.name}» олиб ташланди: шу ном билан келадиган янги нархлар алоҳида ресурс бўлади.")
    return redirect("panel:narx_item", pk=pk)


@require_POST
@superuser_required
def narx_price(request, pk, action):
    p = get_object_or_404(Price, pk=pk)
    back = _back(request, reverse("panel:narx_item", args=[p.item_id]))
    if action == "pin":
        pin = request.POST.get("pin", "")
        services.set_pin(p, pin, user=request.user)
        audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: {p.item.name} — {p.price} "
              + (f"«{services.PIN_LABELS[pin]}» нарх қилиб белгиланди" if pin in services.PIN_LABELS else "белгиси олинди"))
        messages.success(request, f"{services.fmt(p.price)} сўм — {services.PIN_LABELS[pin]} нарх қилиб белгиланди."
                         if pin in services.PIN_LABELS else "Белги олинди — нарх яна умумий қоида бўйича ҳисобланади.")
        return back
    group = list(services.twins(p)) if request.POST.get("one") != "1" else [p]
    if action == "delete":
        if services.counted(p) and _norm_word(request.POST.get("confirm")) != _norm_word(DELETE_WORD):
            messages.error(request, "Бу нарх ҳисобда ишлатилади — ўчириш учун тасдиқлаш сўзини ёзинг.")
            return back
        n = services.delete_prices(group, user=request.user)
        audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: {p.item.name} — {p.price} ўчирилди ({n} та)")
        messages.success(request, f"{services.fmt(p.price)} сўм нарх ўчирилди" + (f" ({n} та жойдан)." if n > 1 else "."))
        return back
    if action not in ("exclude", "include"):
        raise Http404
    for x in group:
        services.set_excluded(x, action == "exclude", request.POST.get("note", "").strip(), user=request.user)
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: {p.item.name} — {p.price} "
          f"{'ҳисобдан чиқарилди' if action == 'exclude' else 'ҳисобга қайтарилди'}")
    return _back(request, reverse("panel:narx_item", args=[p.item_id]))


DELETE_WORD = "ЎЧИРИШ"


def _norm_word(v) -> str:
    return " ".join(str(v or "").upper().replace("Ё", "Е").split())


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
    from narxbaza.models import Intake
    from narxbaza import intake as I
    opened = list(Intake.objects.filter(status__in=(Intake.Status.OPEN, Intake.Status.READING, Intake.Status.FAILED))
                  .select_related("owner", "created_by"))
    by_work = {(x.kind, str(x.work_id)): x for x in opened if x.work_id}
    for _, w in pending:
        w.intake = by_work.get((w.kind, str(w.pk)))
    for x in opened:
        x.c = I.counts(x)
    return render(request, "panel/narx_collect.html", _nctx(request, "collect", pending=[w for _, w in pending],
                                                            opened=opened))


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
    if request.user.is_superuser:  # ҳамма қатор аввал текширув навбатига (Excel кўриниши)
        from narxbaza import intake as I
        return redirect("panel:narx_intake", pk=I.open_for_work(kind, work, request.user).pk)
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
    keys = {r["key"] for r in rows}
    known = set(Item.objects.filter(key__in=keys).values_list("key", flat=True)) | \
        set(Alias.objects.filter(key__in=keys, item__deleted_at=None).values_list("key", flat=True))
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
    qs = Batch.objects.select_related("owner", "added_by").annotate(
        n=Count("prices", filter=Q(prices__item__deleted_at=None))).order_by("-created")
    k = request.GET.get("k", "")
    if k == "manual":
        qs = qs.filter(kind=Batch.Kind.MANUAL)
    elif k == "work":
        qs = qs.exclude(kind=Batch.Kind.MANUAL)
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    _batch_names(page.object_list)
    return render(request, "panel/narx_batches.html", _nctx(request, "batches", page=page, k=k))


def _batch_names(batches):
    """Юкланган файлдан тўпламлар: файл номи, ичидаги папкаси ва қайси архивдан (``b.fname``, ``b.fdir``, ``b.farch``)."""
    from narxbaza.models import Intake
    batches = list(batches)
    want = {b.pk for b in batches if b.kind == Batch.Kind.FILE}
    up = {}
    if want:
        for title, files in Intake.objects.filter(kind="file").values_list("title", "files"):
            for f in files or []:
                if f.get("batch") in want:
                    up[f["batch"]] = title
    for b in batches:
        d = _fname(b.title, up.get(b.pk, "")) if b.kind == Batch.Kind.FILE else {"fname": b.title, "fdir": "", "farch": ""}
        b.fname, b.fdir, b.farch, b.bst = d["fname"], d["fdir"], d["farch"], _bst(b.stats)


@staff_required
def narx_batch(request, pk):
    b = get_object_or_404(Batch.objects.select_related("owner", "added_by"), pk=pk)
    prices = list(b.prices.select_related("item").order_by("item__name"))
    _batch_names([b])
    return render(request, "panel/narx_batch.html", _nctx(request, "batches", b=b, prices=prices,
                                                          logs=list(b.logs.select_related("user")[:50])))


@require_POST
@superuser_required
def narx_batch_edit(request, pk):
    b = get_object_or_404(Batch, pk=pk)
    region, day = _region(request.POST.get("region")), _day(request.POST.get("date"))
    if not region or not day:
        messages.error(request, "Вилоят ва санани тўғри танланг (сана бугундан кейин бўлмасин).")
        return redirect("panel:narx_batch", pk=pk)
    if services.edit_batch(b, region=region, day=day, note=request.POST.get("note", "").strip(), user=request.user):
        audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: тўплам тузатилди — «{b.title}», "
              f"{REGION_LABELS[region]}, {day}")
        messages.success(request, "Тўплам тузатилди, нархлар қайта ҳисобланди.")
    return redirect("panel:narx_batch", pk=pk)


@require_POST
@superuser_required
def narx_batch_delete(request, pk):
    """Тўпламни саватчага (бутунлай ўчириш — саватчадан)."""
    b = get_object_or_404(Batch, pk=pk)
    n = b.prices.count()
    services.trash_batch(b, request.user)
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: тўплам саватчага — «{b.title}», {n} та нарх")
    messages.success(request, f"«{b.title}» саватчага ташланди ({n} та нарх ҳисобдан чиқди). Керак бўлса — "
                              f"«Саватча» дан тиклайсиз.")
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


MAX_UPLOAD_FILES = 400


def _upload_reply(request, *, error=None, url=None):
    """Модал файлларни XHR билан юборади (юкланиш фоизи кўринади) — жавоб JSON; JS'сиз — одатий redirect."""
    from django.http import JsonResponse
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"error": error} if error else {"url": url}, status=400 if error else 200)
    if error:
        messages.error(request, error)
        return redirect("panel:narx")
    return redirect(url)


@require_POST
@superuser_required
def narx_upload(request):
    """Файл(лар) → текширув навбати (базага ҳеч нарса тушмайди): битта Excel, бир нечта Excel, бутун папка ёки
    ZIP / RAR / 7z архив (ичма-ич ҳам). Бу ерда файллар фақат сақланади — архивни очиш, .xls ни ўгириш ва ўқиш
    орқа фонда (``intake.read_upload``), саҳифа жараённи кўрсатиб туради."""
    import shutil
    import uuid
    from pathlib import PurePosixPath
    from jobs.archives import EXCEL_EXT, is_archive, safe_part
    from jobs.services import UploadError, _check_upload, _save
    from narxbaza import intake as I
    from panel import conf
    files, kind = request.FILES.getlist("file"), request.POST.get("kind", "")
    paths = request.POST.getlist("paths")
    if not files or kind not in dict(services.FILE_KINDS):
        return _upload_reply(request, error="Файл ва унинг турини танланг.")
    root = _udir() / f"up_{uuid.uuid4().hex}"
    rel = lambda p: p.relative_to(_udir().parent).as_posix()  # noqa: E731
    items = []
    try:
        for n, f in enumerate(files[:MAX_UPLOAD_FILES]):
            name = (paths[n] if n < len(paths) and paths[n] else f.name).replace("\\", "/")
            show = "/".join(safe_part(x) for x in name.split("/") if x and x not in (".", "..")) or f.name
            ext = PurePosixPath(f.name).suffix.lower()
            if is_archive(f.name):
                if f.size > conf.get("max_archive_mb"):
                    raise UploadError(f"{f.name}: архив жуда катта ({f.size // (1024 * 1024)} МБ)")
            elif ext in EXCEL_EXT:
                _check_upload(f, f.name)
            else:
                continue
            saved = root / "src" / f"{n}{ext}"
            _save(f, saved)
            items.append([show, rel(saved), is_archive(f.name)])
        if not items:
            raise UploadError("Excel файл топилмади (.xls, .xlsx, .xlsm ёки улар солинган ZIP / RAR / 7z архив).")
    except UploadError as e:
        shutil.rmtree(root, ignore_errors=True)
        return _upload_reply(request, error=str(e))
    single = files[0] if len(files) == 1 else None
    title = single.name if single else (PurePosixPath(items[0][0]).parts[0] if "/" in items[0][0] else f"{len(items)} та файл")
    it = I.start_upload(items=items, root=rel(root), kind=kind, title=title, user=request.user,
                        file_path=items[0][1] if single else "", default_category=request.POST.get("category", "K"))
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: файл текширувга — «{it.title}», {len(items)} та файл")
    return _upload_reply(request, url=reverse("panel:narx_intake", args=[it.pk]))


@superuser_required
def narx_intake_status(request, pk):
    from django.http import JsonResponse
    from narxbaza.models import Intake
    from narxbaza import intake as I
    it = get_object_or_404(Intake, pk=pk)
    pr = {k: v for k, v in (it.progress or {}).items() if k in ("stage", "done", "total", "file", "error")}
    return JsonResponse({"status": it.status, "stuck": I.stuck(it), **pr})


@require_POST
@superuser_required
def narx_intake_retry(request, pk):
    """Тўхтаб қолган / хато берган ўқишни қайта бошлаш (сақланган файллардан)."""
    from narxbaza.models import Intake
    from narxbaza import intake as I
    it = get_object_or_404(Intake, pk=pk, kind="file")
    if it.status == Intake.Status.FAILED or I.stuck(it):
        pr = {k: v for k, v in (it.progress or {}).items() if k in ("src", "root", "cat")}
        it.status, it.progress = Intake.Status.READING, {**pr, "stage": "Қайта бошланди", "done": 0}
        it.save(update_fields=["status", "progress", "updated"])
        I.run(it.pk)
    return redirect("panel:narx_intake", pk=pk)


@staff_required
def narx_batch_file(request, pk):
    from django.conf import settings
    from django.http import FileResponse
    from pathlib import Path
    b = get_object_or_404(Batch.all_objects, pk=pk)
    path = (Path(settings.MEDIA_ROOT) / b.file_path) if b.file_path else None
    if not path or not path.is_file():
        raise Http404("Файл сақланмаган")
    return FileResponse(open(path, "rb"), as_attachment=True, filename=b.title)


@require_POST
@superuser_required
def narx_item_delete(request, pk):
    """Ресурсни саватчага (бутунлай ўчириш — саватчадан)."""
    it = get_object_or_404(Item, pk=pk)
    name, n = it.name, it.prices.count()
    services.trash_item(it, request.user)
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: ресурс саватчага — {name} ({n} та нарх)")
    messages.success(request, f"«{name}» саватчага ташланди ({n} та нарх ҳисобдан чиқди). Керак бўлса — "
                              f"«Саватча» дан тиклайсиз.")
    return redirect("panel:narx")


# ===================== саватча =====================
@staff_required
def narx_trash(request):
    items = list(Item.all_objects.exclude(deleted_at=None).select_related("deleted_by")
                 .annotate(n=Count("prices")).order_by("-deleted_at"))
    batches = list(Batch.all_objects.exclude(deleted_at=None).select_related("deleted_by", "owner")
                   .annotate(n=Count("prices")).order_by("-deleted_at"))
    for b in batches:
        b.blocked = bool(b.work_id) and Batch.objects.filter(kind=b.kind, work_id=b.work_id).exists()
    return render(request, "panel/narx_trash.html", _nctx(request, "trash", items=items, batches=batches,
                                                          purged=Log.objects.filter(action=Log.Action.PURGE)
                                                          .select_related("user")[:30]))


def _trashed(kind, pk):
    if kind == "item":
        return get_object_or_404(Item.all_objects.exclude(deleted_at=None), pk=pk)
    if kind == "batch":
        return get_object_or_404(Batch.all_objects.exclude(deleted_at=None), pk=pk)
    raise Http404


@require_POST
@superuser_required
def narx_trash_restore(request, kind, pk):
    obj = _trashed(kind, pk)
    try:
        if kind == "item":
            it = services.restore_item(obj, request.user)
            merged = it.pk != obj.pk
        else:
            services.restore_batch(obj, request.user)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("panel:narx_trash")
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: саватчадан тикланди — {obj}")
    if kind == "item":
        messages.success(request, f"«{it.name}» тикланди" + (" — шу номдаги ресурс бор эди, унга қўшилди." if merged else "."))
        return redirect("panel:narx_item", pk=it.pk)
    messages.success(request, f"«{obj.title}» тикланди, нархлар яна ҳисобда.")
    return redirect("panel:narx_batch", pk=obj.pk)


@require_POST
@superuser_required
def narx_trash_purge(request, kind, pk):
    obj = _trashed(kind, pk)
    title = str(obj)
    (services.purge_item if kind == "item" else services.purge_batch)(obj, request.user)
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: бутунлай ўчирилди — {title}")
    messages.success(request, f"«{title}» бутунлай ўчирилди.")
    return redirect("panel:narx_trash")


@require_POST
@superuser_required
def narx_trash_empty(request):
    n = 0
    for b in Batch.all_objects.exclude(deleted_at=None):
        services.purge_batch(b, request.user)
        n += 1
    for it in Item.all_objects.exclude(deleted_at=None):
        services.purge_item(it, request.user)
        n += 1
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: саватча бўшатилди — {n} та")
    messages.success(request, f"Саватча бўшатилди: {n} та бутунлай ўчирилди.")
    return redirect("panel:narx_trash")


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


# ===================== текшириш навбати =====================
CHECKS = [("unver", "Текширилмаган", "Сиз ҳали кўрмаган ресурслар — тоифасини текшириб тасдиқланг"),
          ("dup", "Эҳтимол икки нусха", "Номи деярли бир хил, бирлиги ва номидаги рақамлари бир хил ресурслар"),
          ("outlier", "Четда нархлар", "Шу вилоят ва йилдаги бошқа нархлардан 3 баробар фарқ — ҳисобга кирмаяпти"),
          ("cat", "Тоифаси йўқ", "«Бошқа» тоифасидаги ресурслар — тоифасини белгиланг"),
          ("stale", "Эскирган", f"{services.stale_days()} кундан бери янги нарх келмаган")]


@staff_required
def narx_check(request):
    counts = services.review_counts()
    sec = request.GET.get("s", "")
    if sec not in dict((k, 1) for k, *_ in CHECKS):
        sec = next((k for k, *_ in CHECKS if counts[k]), "dup")
    ctx = {}
    if sec == "dup":
        dq = " ".join(request.GET.get("q", "").split())[:100]
        band = request.GET.get("r", "")
        allp = services.duplicate_pairs()
        if band in DUP_BANDS:
            lo, hi = DUP_BANDS[band][1:]
            allp = [x for x in allp if lo <= x[0] <= hi]
        if dq:
            hit = set(services.live_items().filter(
                *[Q(name__icontains=w) | Q(key__icontains=services.name_core(w)) for w in dq.split()]).values_list("pk", flat=True))
            allp = [x for x in allp if x[1] in hit or x[2] in hit]
        page = Paginator(allp, 25).get_page(request.GET.get("page"))
        ids = {i for _, a, b in page for i in (a, b)}
        items = {i.pk: i for i in Item.objects.filter(pk__in=ids)}
        n = dict(Price.objects.filter(item_id__in=ids).values_list("item_id").annotate(c=Count("id")))
        best = services.best_stat(list(ids))
        for i in items.values():
            i.n, i.st = n.get(i.pk, 0), best.get(i.pk)
            i.show = None if i.st is None else (i.st.mid if i.st.mid is not None else i.st.low)
        pairs = []
        for r, a, b in page:
            if a not in items or b not in items:
                continue
            x, y = items[a], items[b]
            x.words, y.words = _word_diff(x.name, y.name)
            keep = x if (x.n, x.verified, -x.pk) >= (y.n, y.verified, -y.pk) else y
            gap = None
            if x.show and y.show:
                gap = round(abs(float(x.show) - float(y.show)) / float(min(x.show, y.show)) * 100)
            pairs.append({"r": r, "a": x, "b": y, "keep": keep, "gap": gap, "sides": ((x, y), (y, x))})
        counts_band = {k: sum(1 for x in services.duplicate_pairs() if v[1] <= x[0] <= v[2]) for k, v in DUP_BANDS.items()}
        ctx.update(pairs=pairs, dq=dq, band=band, bands=[(k, v[0], counts_band[k]) for k, v in DUP_BANDS.items()])
    elif sec == "outlier":
        page = Paginator(Price.objects.filter(outlier=True, excluded=False).select_related("item", "batch")
                         .order_by("item__name", "-date"), 30).get_page(request.GET.get("page"))
        st = {(x.item_id, x.region, x.year): x for x in Stat.objects.filter(item_id__in={p.item_id for p in page})}
        for pr in page:
            pr.st = st.get((pr.item_id, pr.region, pr.year))
            pr.times = round(float(max(pr.price, pr.st.mid or pr.st.low) / min(pr.price, pr.st.mid or pr.st.low)), 1) \
                if pr.st and pr.price and (pr.st.mid or pr.st.low) else None
    elif sec == "unver":
        qs = services.live_items().filter(verified=False).annotate(n=Count("prices"))
        ucat = request.GET.get("c", "") if request.GET.get("c", "") in CATEGORY_LABELS else ""
        uq = request.GET.get("q", "").strip()[:100]
        if ucat:
            qs = qs.filter(category=ucat)
        for w in uq.split():
            qs = qs.filter(name__icontains=w)
        page = Paginator(qs.order_by("category", "name"), 50).get_page(request.GET.get("page"))
        best = services.best_stat([i.pk for i in page])
        for i in page:
            i.st = best.get(i.pk)
        ctx.update(ucat=ucat, uq=uq, ucats=dict(services.live_items().filter(verified=False).order_by()
                                                .values_list("category").annotate(c=Count("id"))))
    elif sec == "cat":
        page = Paginator(services.live_items().filter(category="O").annotate(n=Count("prices")).order_by("name"),
                         50).get_page(request.GET.get("page"))
    else:
        page = Paginator(services.stale_items(), 50).get_page(request.GET.get("page"))
    return render(request, "panel/narx_check.html", _nctx(
        request, "check", sec=sec, page=page, checks=[(k, t, d, counts[k]) for k, t, d in CHECKS], **ctx))


DUP_BANDS = {"100": ("100%", 100, 100), "95": ("95–99%", 95, 99), "90": ("90–94%", 90, 94), "86": ("86–89%", 0, 89)}


def _word_diff(a, b):
    """Икки номнинг сўзлари: ``[(сўз, бошқами)]`` — фарқ қилган сўзлар ажратиб кўрсатилади (белгилар ҳисобга олинмайди)."""
    from difflib import SequenceMatcher
    wa, wb = a.split(), b.split()
    ka, kb = [services.name_core(w) for w in wa], [services.name_core(w) for w in wb]
    da, db = [True] * len(wa), [True] * len(wb)
    for blk in SequenceMatcher(None, ka, kb, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            da[blk.a + k] = db[blk.b + k] = False
    return list(zip(wa, da)), list(zip(wb, db))


@require_POST
@superuser_required
def narx_check_bulk(request):
    """«Эҳтимол икки нусха»да белгиланган жуфтлар: ҳаммасини кўп нархлисига бирлаштириш ёки «бошқа-бошқа»."""
    action = request.POST.get("action", "")
    pairs = []
    for v in request.POST.getlist("pair")[:200]:
        a, _, b = v.partition("-")
        if a.isdigit() and b.isdigit():
            pairs.append((int(a), int(b)))
    back = _back(request, reverse("panel:narx_check") + "?s=dup")
    if not pairs or action not in ("merge", "ignore"):
        messages.error(request, "Жуфтлар танланмаган.")
        return back
    done = 0
    if action == "ignore":
        for a, b in pairs:
            services.ignore_pair(a, b, request.user)
            done += 1
        messages.success(request, f"{done} та жуфт «бошқа-бошқа» деб белгиланди.")
        return back
    for a, b in pairs:
        items = {i.pk: i for i in Item.objects.filter(pk__in=(a, b))}
        if len(items) < 2:
            continue  # олдинги жуфтда бирлаштирилиб кетган
        n = dict(Price.objects.filter(item_id__in=(a, b)).values_list("item_id").annotate(c=Count("id")))
        x, y = sorted(items.values(), key=lambda i: (-n.get(i.pk, 0), not i.verified, i.pk))
        try:
            services.merge_items(x, y, user=request.user)
            done += 1
        except ValueError as e:
            messages.error(request, f"«{y.name[:60]}»: {e}")
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: {done} та жуфт бирлаштирилди (текширувдан)")
    messages.success(request, f"{done} та жуфт бирлаштирилди — ҳар бирида нархи кўпроғига.")
    return back


@require_POST
@superuser_required
def narx_check_ignore(request):
    a, b = request.POST.get("a", ""), request.POST.get("b", "")
    if a.isdigit() and b.isdigit():
        services.ignore_pair(int(a), int(b), request.user)
        messages.success(request, "Белгиланди: булар бошқа-бошқа ресурс — навбатда қайта кўрсатилмайди.")
    return _back(request, reverse("panel:narx_check"))


# ===================== бирданига ўзгартириш =====================
@require_POST
@superuser_required
def narx_bulk(request):
    """Рўйхатда белгиланганлар: тоифасини ўзгартириш, саватчага, битта ресурсга бирлаштириш."""
    ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()][:500]
    items = list(Item.objects.filter(pk__in=ids))
    action = request.POST.get("action", "")
    back = reverse("panel:narx")
    if not items:
        messages.error(request, "Битта ҳам ресурс белгиланмади.")
        return _back(request, back)
    if action == "category":
        cat = request.POST.get("category", "")
        if cat not in CATEGORY_LABELS:
            messages.error(request, "Тоифани танланг.")
            return _back(request, back)
        n = services.bulk_category(items, cat, request.user)
        audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: {n} та ресурс тоифаси → {CATEGORY_LABELS[cat]}")
        messages.success(request, f"{n} та ресурснинг тоифаси «{CATEGORY_LABELS[cat]}» бўлди.")
    elif action == "trash":
        n = services.bulk_trash(items, request.user)
        audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: {n} та ресурс саватчага")
        messages.success(request, f"{n} та ресурс саватчага ташланди. Керак бўлса — «Саватча» дан тиклайсиз.")
    elif action == "merge":
        target = next((i for i in items if str(i.pk) == request.POST.get("target", "")), None)
        if target is None or len(items) < 2:
            messages.error(request, "Бирлаштириш учун камида 2 та ресурс белгиланг ва асосийсини танланг.")
            return _back(request, back)
        done, moved, skipped = services.bulk_merge(target, items, request.user)
        audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: {done} та ресурс «{target.name}» га қўшилди, {moved} та нарх")
        messages.success(request, f"{done} та ресурс «{target.name}» га қўшилди ({moved} та нарх).")
        if skipped:
            messages.error(request, "Бирлиги бошқа бўлгани учун қўшилмади: " + ", ".join(f"«{i.name}» ({i.unit})" for i in skipped[:5]))
        return redirect("panel:narx_item", pk=target.pk)
    else:
        raise Http404
    return _back(request, back)


# ===================== статистика =====================
@staff_required
def narx_stats(request):
    """Вилоятлар ва йиллар бўйича: харита, рейтинг, вилоят × йил жадвали, йиллар бўйича нархлар."""
    from narxbaza import stats as S
    ys = S.years()
    y = int(request.GET["y"]) if request.GET.get("y", "").isdigit() and int(request.GET["y"]) in ys else None
    m = request.GET.get("m", "") if request.GET.get("m", "") in dict((k, 1) for k, *_ in S.METRICS) else "items"
    r = _region(request.GET.get("r", ""))
    ov = S.overview(y, m, r)
    sel = next((x for x in ov["regions"] if x["code"] == r), None)
    from django.utils.http import urlencode

    def url(**ch):
        d = {"y": y or "", "m": m if m != "items" else "", "r": r, **ch}
        return "?" + urlencode({k: v for k, v in d.items() if v})
    for x in ov["regions"]:
        x["url"] = url(r="" if x["code"] == r else x["code"])
    pr = Price.objects.filter(year=y) if y else Price.objects.all()
    kpi = {"items": services.live_items().filter(stats__year=y).distinct().count() if y else services.live_items().count(),
           "prices": pr.count(), "covered": ov["covered"], "regions": len(ov["regions"]), "years": len(ys),
           "batches": (Batch.objects.filter(date__year=y) if y else Batch.objects.all()).count()}
    return render(request, "panel/narx_stats.html", _nctx(
        request, "stats", ov=ov, kpi=kpi, y=y, m=m, sel=sel, metric=dict((k, (t, d)) for k, t, d in S.METRICS)[m],
        detail=S.region_detail(r, y, ov["ratios"]) if sel else None,
        year_opts=[("", "Ҳамма йиллар", url(y="", r=r))] + [(str(v), str(v), url(y=v)) for v in ys],
        metric_opts=[(k, t, d, url(m=k if k != "items" else "")) for k, t, d in S.METRICS],
        mx=S.matrix(), by_year=S.by_year(), clear_region=url(r="")))


# ===================== текширув навбати (Excel кўриниши) =====================
def _intake_or_404(pk):
    from narxbaza.models import Intake
    return get_object_or_404(Intake.objects.select_related("owner", "created_by", "batch"), pk=pk)


@superuser_required
def narx_intake(request, pk):
    """Тизим белгилаган тоифалар Excel кўринишида: админ кўриб тасдиқлайди, тузатади ёки рад этади."""
    from narxbaza import intake as I
    it = _intake_or_404(pk)
    if it.status in (it.Status.READING, it.Status.FAILED):
        return render(request, "panel/narx_intake_wait.html", _nctx(request, "uploads" if it.kind == "file" else "collect", it=it, pr=it.progress or {},
                                                                     stuck=I.stuck(it)))
    rows = I.rows_json(it)
    cmp = I.compare(it)
    bstats = dict(Batch.all_objects.filter(pk__in={f["batch"] for f in it.files or [] if f.get("batch")})
                  .values_list("pk", "stats"))
    files = []
    for i, f in enumerate(it.files or []):
        n = sum(1 for r in rows if r["fi"] == i)
        files.append({**f, "i": i, "n": n, "cmp": None if f.get("done") else cmp.get(i), **_fname(f["file"], it.title),
                      "bst": _bst(bstats.get(f.get("batch"))), "left": sum(1 for r in rows if r["fi"] == i and r["state"] == "new"),
                      "region_label": REGION_LABELS.get(f.get("region", ""), "")})
    bad = (it.progress or {}).get("bad") or []
    return render(request, "panel/narx_intake.html", _nctx(
        request, "uploads" if it.kind == "file" else "collect", it=it, files=files, c=I.counts(it), stale=I.stale(it), bad=bad,
        cmp_all=cmp["all"],
        file_names=[f["file"] + (f" — {f['sheet']}" if it.kind == "file" and f.get("sheet") else "") for f in files],
        file_done=[bool(f.get("done")) for f in files],
        rows_script=_json_tag(rows, "ni-rows"), base_script=_json_tag(I.base_prices({r["item"] for r in rows if r["item"]}), "ni-base"),
        cats_json=[[k, label, GUTTER[k]] for k, label in CATEGORIES]))


def _json_tag(value, eid: str):
    """``json_script`` каби, лекин кирилл ҳарфлар ``\\uXXXX`` эмас, ўзи — минглаб қаторда саҳифа икки баравар енгил."""
    from django.core.serializers.json import DjangoJSONEncoder
    from django.utils.safestring import mark_safe
    s = json.dumps(value, ensure_ascii=False, separators=(",", ":"), cls=DjangoJSONEncoder)
    s = s.replace("<", "\\u003C").replace(">", "\\u003E").replace("&", "\\u0026")
    return mark_safe(f'<script id="{eid}" type="application/json">{s}</script>')


@superuser_required
def narx_intake_sheet(request, pk, fi):
    """Битта файл / листнинг Excel кўриниши (HTML) — кешдан."""
    from django.conf import settings
    from django.http import JsonResponse
    from pathlib import Path
    from jobs import preview, xlview
    it = _intake_or_404(pk)
    files = it.files or []
    if fi >= len(files):
        raise Http404
    f = files[fi]
    try:
        if f.get("job"):
            job = get_object_or_404(Job, pk=f["job"])
            data = preview.sheet_data(job, "out", f.get("sheet") or None)
        else:
            path = Path(settings.MEDIA_ROOT) / (f.get("path") or it.view_path or it.file_path)
            if not path.is_file():
                from narxbaza import intake as I
                try:
                    path = I.restore_view(it, fi)
                except Exception:  # noqa: BLE001 — асл файл бузуқ / ўгириб бўлмади
                    path = None
                if path is None:
                    return JsonResponse({"error": "Бу файлнинг Excel нусхаси серверда йўқ ва асл юкланган файл ҳам топилмади "
                                                  "(масалан, захира нусхадан тикланган — эски нусхага файллар кирмаган). "
                                                  "Қаторлар ва қарорлар жойида; Excel кўриниши учун файлни қайта юкланг."},
                                        status=404)
            data = xlview.cached(_udir() / "preview", f"{it.pk}|{fi}|{f.get('sheet')}", path,
                                 lambda: xlview.render(path, f.get("sheet") or None))
    except preview.NotAvailable as e:
        raise Http404(str(e))
    return JsonResponse({"html": data["html"], "sheet": data.get("sheet"), "truncated": data.get("truncated")})


def _json_body(request) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        return {}


@require_POST
@superuser_required
def narx_intake_decide(request, pk):
    from django.http import JsonResponse
    from narxbaza import intake as I
    it = _intake_or_404(pk)
    d = _json_body(request)
    ids = [int(i) for i in d.get("ids") or [] if str(i).isdigit()]
    state = d.get("state") if d.get("state") in ("ok", "skip", "new") else None
    cat = d.get("cat") if d.get("cat") in CATEGORY_LABELS else None
    try:
        rows = I.decide(it, ids, state=state, category=cat, user=request.user)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=409)
    return JsonResponse({"rows": rows, "c": I.counts(it)})


@require_POST
@superuser_required
def narx_intake_known(request, pk):
    from narxbaza import intake as I
    it = _intake_or_404(pk)
    n = I.accept_known(it, request.user)
    messages.success(request, f"{n} та такрорий қатор тасдиқланди (сиз илгари тасдиқлаган ресурслар).")
    return redirect("panel:narx_intake", pk=pk)


@require_POST
@superuser_required
def narx_intake_commit(request, pk):
    from narxbaza import intake as I
    it = _intake_or_404(pk)
    region, day = _region(request.POST.get("region")), _day(request.POST.get("date"))
    if not region or not day:
        messages.error(request, "Вилоят ва санани танланг (сана бугундан кейин бўлмасин).")
        return redirect("panel:narx_intake", pk=pk)
    fis = [int(x) for x in request.POST.getlist("fi") if x.isdigit()]
    if not fis:
        messages.error(request, "Базага ёзиладиган файлларни белгиланг.")
        return redirect("panel:narx_intake", pk=pk)
    try:
        b = I.commit(it, region=region, day=day, note=request.POST.get("note", "").strip()[:300], user=request.user,
                     fis=fis)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("panel:narx_intake", pk=pk)
    it.refresh_from_db()
    if b is None:
        messages.success(request, f"{len(fis)} та файл ёпилди — ҳамма қатори рад этилган, базага ҳеч нарса ёзилмади.")
    else:
        audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: текширувдан «{b.title}» — {b.stats.get('prices', 0)} та нарх, "
              f"{REGION_LABELS[region]}, {day}")
        st = b.stats
        messages.success(request, f"«{_fname(b.title)['fname']}» базага ёзилди ({REGION_LABELS[region]}, {day:%d.%m.%Y}): "
                                  f"{st.get('items', 0)} та ресурс — {st.get('new', 0)} та янги, {st.get('existing', 0)} та базада бор "
                                  f"({st.get('changed', 0)} тасининг нархи янгиланди, {st.get('same', 0)} таси ўзгармади).")
    if it.status == it.Status.DONE and b is not None:
        return redirect("panel:narx_batch", pk=b.pk)
    return redirect("panel:narx_intake", pk=pk)


def _bst(stats) -> dict:
    """Тўплам ҳисоби: эски тўпламларда «базада бор» = ресурслар − янги (нарх ўзгариши ҳисобланмаган — ``old``)."""
    st = dict(stats or {})
    if "existing" not in st and st.get("items"):
        st.update(existing=st["items"] - st.get("new", 0), new=st.get("new", 0), old=True)
    return st


ARCHIVE_EXT = (".zip", ".rar", ".7z")


def _fname(path: str, upload: str = "") -> dict:
    """Юкланган файл йўли → ``{fname, fdir, farch}``: файл номи, ичидаги папкаси ва қайси архив / папкадан.
    «Смета (7)/Смета/1-Трасса+/r2770.xls» (архив «Смета (7).zip») → r2770.xls · Смета/1-Трасса+ · Смета (7).zip."""
    parts = [p for p in (path or "").replace("\\", "/").split("/") if p]
    if not parts:
        return {"fname": path or "", "fdir": "", "farch": ""}
    name, dirs = parts[-1], parts[:-1]
    farch = ""
    low = (upload or "").lower()
    if dirs and upload:
        stem = upload.rsplit(".", 1)[0] if low.endswith(ARCHIVE_EXT) else upload
        if dirs[0] == stem:
            dirs = dirs[1:]
            farch = upload if low.endswith(ARCHIVE_EXT) else f"{upload} (папка)"
    elif low.endswith(ARCHIVE_EXT):
        farch = upload
    return {"fname": name, "fdir": "/".join(dirs), "farch": farch}


UPLOAD_FILTERS = {"active": "Текширилаётганлар", "done": "Базага ёзилганлар", "cancelled": "Бекор қилинганлар"}


@superuser_required
def narx_uploads(request):
    """Юкланган файллар (архив, папка, Excel) — ҳаммаси: ўқилаётган, текширилаётган, базага ёзилган, бекор қилинган."""
    from narxbaza.models import Intake
    from narxbaza import intake as I
    s = request.GET.get("s", "")
    qs = Intake.objects.filter(kind="file").select_related("created_by").order_by("-created")
    tot = dict(qs.values_list("status").annotate(c=Count("id")))
    act = (Intake.Status.OPEN, Intake.Status.READING, Intake.Status.FAILED)
    if s == "active":
        qs = qs.filter(status__in=act)
    elif s == "done":
        qs = qs.filter(status=Intake.Status.DONE)
    elif s == "cancelled":
        qs = qs.filter(status=Intake.Status.CANCELLED)
    v = "files" if request.GET.get("v") == "files" else "uploads"  # сукут бўйича — юклашлар бўйича
    q = " ".join(request.GET.get("q", "").split())[:100]
    n = {"": sum(tot.values()), "active": sum(tot.get(k, 0) for k in act), "done": tot.get("done", 0),
         "cancelled": tot.get("cancelled", 0)}
    tabs = [("", "Ҳаммаси", n[""])] + [(k, label, n[k]) for k, label in UPLOAD_FILTERS.items()]
    if v == "files":
        page = Paginator(_upload_files(qs, q), 60).get_page(request.GET.get("page"))
        return render(request, "panel/narx_uploads.html", _nctx(request, "uploads", page=page, s=s, tabs=tabs, v=v, q=q,
                                                                file_kinds=services.FILE_KINDS))
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    for x in page:
        files = x.files or []
        x.c = I.counts(x)
        x.n_files, x.n_done = len(files), sum(1 for f in files if f.get("done"))
        x.batches = sorted({f["batch"] for f in files if f.get("batch")})
        x.bad = len((x.progress or {}).get("bad") or [])
        x.can_reopen = x.status == Intake.Status.CANCELLED and x.c["total"] > 0
        x.can_delete = _deletable(x)
    return render(request, "panel/narx_uploads.html", _nctx(request, "uploads", page=page, s=s, tabs=tabs, v=v, q=q,
                                                            file_kinds=services.FILE_KINDS))


def _upload_files(qs, q=""):
    """«Файллар бўйича»: ҳар юкланган файл (архив ичидагиси ҳам; лист — алоҳида) битта қатор. Юклаш ҳали ўқилмаган
    ёки бекор қилинган бўлса — юклашнинг ўзи битта қатор."""
    from narxbaza.models import IntakeRow
    ups = list(qs)
    agg = {}
    for iid, fi, state, c in (IntakeRow.objects.filter(intake__in=[x.pk for x in ups]).values_list("intake_id", "fi", "state")
                              .annotate(c=Count("id")).order_by()):
        agg.setdefault((iid, fi), {})[state] = c
    out, ql = [], q.lower()
    for x in ups:
        files = x.files or []
        if not files:
            if not ql or ql in x.title.lower():
                out.append({"up": x, "fi": None, "fname": x.title, "fdir": "", "farch": "", "sheet": "", "n": 0,
                            "del": _deletable(x)})
            continue
        for i, f in enumerate(files):
            if ql and ql not in f["file"].lower() and ql not in x.title.lower():
                continue
            c = agg.get((x.pk, i), {})
            out.append({"up": x, "fi": i, **_fname(f["file"], x.title), "sheet": f.get("sheet", ""),
                        "reopen": i == 0 and x.status == x.Status.CANCELLED, "del": i == 0 and _deletable(x),
                        "n": sum(c.values()), "ok": c.get("ok", 0), "skip": c.get("skip", 0), "new": c.get("new", 0),
                        "done": bool(f.get("done")), "batch": f.get("batch"), "region": REGION_LABELS.get(f.get("region", ""), ""),
                        "date": f.get("date", "")})
    bst = dict(Batch.all_objects.filter(pk__in={r["batch"] for r in out if r.get("batch")}).values_list("pk", "stats"))
    for r in out:
        r["bstats"] = _bst(bst.get(r.get("batch")))
    return out


@require_POST
@superuser_required
def narx_intake_reopen(request, pk):
    """Бекор қилинган юклашни қайта очиш (қарорлар сақланган — қолган жойидан давом этилади)."""
    from narxbaza.models import Intake
    it = get_object_or_404(Intake, pk=pk, kind="file", status=Intake.Status.CANCELLED)
    files = it.files or []
    it.status = Intake.Status.DONE if files and all(f.get("done") for f in files) else Intake.Status.OPEN
    it.save(update_fields=["status", "updated"])
    messages.success(request, f"«{it.title}» қайта очилди.")
    return redirect("panel:narx_intake", pk=pk)


def _deletable(it) -> bool:
    """Базага ҳеч нарса ёзилмаган юклаш ўчирилади (ёзилган нархлар — тўпламларда, улар «Киритилганлар»дан ўчирилади)."""
    from narxbaza.models import Intake
    return it.kind == "file" and it.status != Intake.Status.DONE and not any(f.get("done") for f in it.files or [])


@require_POST
@superuser_required
def narx_upload_delete(request, pk):
    """Керак бўлмаган юклашни бутунлай ўчириш: навбат, қаторлар ва серверда сақланган файллари."""
    import shutil
    from django.conf import settings
    from narxbaza.models import Intake
    it = get_object_or_404(Intake, pk=pk, kind="file")
    if not _deletable(it):
        messages.error(request, f"«{it.title}» ўчирилмади: файлларидан бири базага ёзилган. Ёзилган нархларни "
                                "«Киритилганлар»да тўпламни саватчага ташлаб олиб ташланг.")
        return redirect(request.POST.get("next") or "panel:narx_uploads")
    root = Path(settings.MEDIA_ROOT).resolve()
    rel = (it.progress or {}).get("root") or (Path(it.file_path).parent.parent.as_posix() if it.file_path else "")
    d = (root / rel).resolve() if rel else None
    title = it.title
    it.delete()
    if d is not None and d != root and (root / UPLOAD_DIR).resolve() in d.parents and d.name.startswith("up_"):
        shutil.rmtree(d, ignore_errors=True)
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: юклаш ўчирилди — «{title[:150]}»")
    messages.success(request, f"«{title}» ўчирилди (базага ҳеч нарса ёзилмаган эди).")
    return redirect(request.POST.get("next") if (request.POST.get("next") or "").startswith("/") else "panel:narx_uploads")


@require_POST
@superuser_required
def narx_intake_cancel(request, pk):
    from narxbaza import intake as I
    it = _intake_or_404(pk)
    I.cancel(it)
    messages.success(request, "Текширув бекор қилинди — базага ҳеч нарса ёзилмади.")
    if request.POST.get("reopen") and it.work_id:
        return redirect("panel:narx_review", kind=it.kind, work_id=it.work_id)
    return redirect("panel:narx_uploads" if it.kind == "file" else "panel:narx_collect")


# ===================== базадаги текширилмаган ресурслар =====================
@require_POST
@superuser_required
def narx_verify(request):
    """Текширилмаган ресурслар: ҳар бирининг тоифаси (``c<id>``) — белгиланганлари (``ids``) тасдиқланади."""
    from narxbaza import intake as I
    ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()][:500]
    items = list(Item.objects.filter(pk__in=ids))
    n = I.verify_items([(i, request.POST.get(f"c{i.pk}", i.category)) for i in items], request.user)
    if n:
        audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: {n} та ресурс текширилди")
    messages.success(request, f"{n} та ресурс тасдиқланди.")
    return _back(request, reverse("panel:narx_check") + "?s=unver")


# ===================== захира нусха =====================
@superuser_required
def narx_backup(request):
    from narxbaza import backup as B
    return render(request, "panel/narx_backup.html", _nctx(request, "list", counts=B.counts(), saved=B.saved()[:20],
                                                           n_saved=len(B.saved()), delete_word=DELETE_WORD,
                                                           files_size=sum((B._media() / r).stat().st_size for r in B._media_files()),
                                                           rules=B.MERGE_RULES, compact=1))


@superuser_required
def narx_backup_download(request):
    """Тўлиқ нусха (.zip) — ҳозирги ҳолат."""
    import tempfile
    import time
    from django.http import FileResponse
    from narxbaza import backup as B
    tmp = B.backups_dir() / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    for old in tmp.glob("*.zip"):  # олдинги юклаб олинганлар
        if time.time() - old.stat().st_mtime > 3600:
            old.unlink(missing_ok=True)
    stamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M")
    fd, name = tempfile.mkstemp(suffix=".zip", dir=tmp)
    os.close(fd)
    full = request.GET.get("files") != "0"
    B.export(Path(name), with_files=full)
    audit(request, Event.Kind.ADMIN_BASE, detail="нарх базаси: " + ("тўлиқ нусха" if full else "нусха (файлларсиз)") + " юклаб олинди")
    return FileResponse(open(name, "rb"), as_attachment=True,
                        filename=f"narx-bazasi_{stamp}{'' if full else '_faylsiz'}.zip")


@superuser_required
def narx_backup_saved(request, name):
    """Серверда сақланган нусха (тиклашдан олдин автоматик олинган)."""
    from django.http import FileResponse, Http404
    from narxbaza import backup as B
    p = B.backups_dir() / name
    if "/" in name or "\\" in name or not name.endswith(".zip") or not p.is_file():
        raise Http404
    return FileResponse(open(p, "rb"), as_attachment=True, filename=name)


@require_POST
@superuser_required
def narx_backup_delete(request):
    """Серверда сақланган нусхани (ёки ҳаммасини) бутунлай ўчириш — тасдиқлаш сўзи билан."""
    from narxbaza import backup as B
    if _norm_word(request.POST.get("confirm")) != _norm_word(DELETE_WORD):
        messages.error(request, "Нусха ўчирилмади: тасдиқлаш учун «" + DELETE_WORD + "» деб ёзинг.")
        return redirect("panel:narx_backup")
    names = [s["name"] for s in B.saved()]
    want = names if request.POST.get("all") == "1" else [n for n in request.POST.getlist("name") if n in names]
    if not want:
        messages.error(request, "Ўчириладиган нусха топилмади.")
        return redirect("panel:narx_backup")
    size = 0
    for n in want:  # фақат рўйхатдагилар (номи текширилган) — бошқа файлга йўл йўқ
        p = B.backups_dir() / n
        size += p.stat().st_size
        p.unlink(missing_ok=True)
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: {len(want)} та сақланган нусха ўчирилди ({', '.join(want)[:300]})")
    messages.success(request, f"{len(want)} та нусха ўчирилди, {size / 1048576:.1f} МБ бўшади.")
    return redirect("panel:narx_backup")


@require_POST
@superuser_required
def narx_backup_restore(request):
    """Нусхадан тиклаш: базадаги ҳамма нарх базаси маълумоти нусхадагиси билан алмашади."""
    import tempfile
    from narxbaza import backup as B
    f = request.FILES.get("file")
    if not f or not f.name.lower().endswith(".zip"):
        messages.error(request, "Нусха файлини (.zip) танланг.")
        return redirect("panel:narx_backup")
    fd, name = tempfile.mkstemp(suffix=".zip")
    try:
        with os.fdopen(fd, "wb") as out:
            for chunk in f.chunks():
                out.write(chunk)
        m = B.restore(Path(name), user=request.user)
    except B.BackupError as e:
        messages.error(request, str(e))
        return redirect("panel:narx_backup")
    finally:
        Path(name).unlink(missing_ok=True)
    c = m.get("counts") or {}
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: нусхадан тикланди — «{f.name}», {c.get('item', 0)} та ресурс, "
          f"{c.get('price', 0)} та нарх")
    messages.success(request, f"Тикланди: {c.get('item', 0)} та ресурс, {c.get('price', 0)} та нарх. "
                              f"Олдинги ҳолат «{m['safety']}» номи билан сақланди.")
    return redirect("panel:narx_backup")


def _merge_tmp(token):
    from narxbaza import backup as B
    if not token or not token.isalnum() or len(token) != 32:
        return None
    p = B.backups_dir() / "tmp" / f"merge_{token}.zip"
    return p if p.is_file() else None


@require_POST
@superuser_required
def narx_backup_merge(request):
    """Нусхани қўшиш — 1-қадам: файл сақланади ва синаб кўрилади (базага ҳеч нарса ёзилмайди), натижа кўрсатилади."""
    import uuid
    from narxbaza import backup as B
    f = request.FILES.get("file")
    if not f or not f.name.lower().endswith(".zip"):
        messages.error(request, "Нусха файлини (.zip) танланг.")
        return redirect("panel:narx_backup")
    token = uuid.uuid4().hex
    tmp = B.backups_dir() / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    p = tmp / f"merge_{token}.zip"
    with open(p, "wb") as out:
        for chunk in f.chunks():
            out.write(chunk)
    try:
        rep = B.merge(p, user=request.user, dry_run=True)
    except B.BackupError as e:
        p.unlink(missing_ok=True)
        messages.error(request, str(e))
        return redirect("panel:narx_backup")
    from django.utils.dateparse import parse_datetime
    created = parse_datetime(str(rep["manifest"].get("created") or ""))
    return render(request, "panel/narx_backup_merge.html", _nctx(request, "list", rep=rep, token=token, fname=f.name,
                                                                 created=timezone.localtime(created) if created else None,
                                                                 rules=B.MERGE_RULES, compact=1))


@require_POST
@superuser_required
def narx_backup_merge_go(request):
    """Нусхани қўшиш — 2-қадам: тасдиқлангандан кейин ростдан қўшилади."""
    from narxbaza import backup as B
    p = _merge_tmp(request.POST.get("token", ""))
    if p is None:
        messages.error(request, "Нусха файли топилмади — қайта юкланг.")
        return redirect("panel:narx_backup")
    try:
        rep = B.merge(p, user=request.user)
    except B.BackupError as e:
        messages.error(request, str(e))
        return redirect("panel:narx_backup")
    finally:
        p.unlink(missing_ok=True)
    name = request.POST.get("fname", "")[:120]
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: нусха қўшилди — «{name}»: {rep.get('items_new', 0)} та янги "
          f"ресурс, {rep.get('prices_new', 0)} та нарх, {rep.get('prices_dup', 0)} та такрор ўтказилди")
    messages.success(request, f"Қўшилди: {rep.get('items_new', 0)} та янги ресурс, {rep.get('prices_new', 0)} та нарх "
                              f"({rep.get('batches_new', 0)} та тўплам). Такрор нархлар ёзилмади: {rep.get('prices_dup', 0)} та.")
    return redirect("panel:narx_backup")


@require_POST
@superuser_required
def narx_backup_merge_cancel(request):
    p = _merge_tmp(request.POST.get("token", ""))
    if p is not None:
        p.unlink(missing_ok=True)
    return redirect("panel:narx_backup")


# ===================== тозалаш: файллар ва ишлатилмайдиган нархлар =====================
CLEAN_KINDS = {"": "Иккаласи", "out": "Четда (3 баробар фарқ)", "exc": "Ҳисобдан чиқарилган"}


def _unused(G):
    """Ҳисобга кирмайдиган нархлар (четда ёки ҳисобдан чиқарилган, белгиланмаган) — фильтр бўйича."""
    qs = Price.objects.filter(pin="").filter(Q(outlier=True) | Q(excluded=True))
    k = G.get("k", "")
    if k == "out":
        qs = qs.filter(outlier=True, excluded=False)
    elif k == "exc":
        qs = qs.filter(excluded=True)
    if G.get("region") in REGION_LABELS:
        qs = qs.filter(region=G["region"])
    if (G.get("year") or "").isdigit():
        qs = qs.filter(year=int(G["year"]))
    if (G.get("before") or "").isdigit():
        qs = qs.filter(year__lt=int(G["before"]))
    for w in (G.get("q") or "").split()[:6]:  # ресурслар рўйхатидаги каби: ҳар сўз, катта-кичик ҳарфсиз (калит бўйича)
        qs = qs.filter(Q(item__name__icontains=w) | Q(item__key__icontains=services.name_core(w)))
    return qs


@superuser_required
def narx_clean(request):
    from narxbaza import storage
    G = request.GET
    qs = _unused(G).select_related("item", "batch").order_by("item__name", "region", "-year", "-price")
    page = Paginator(qs, 100).get_page(G.get("page"))
    stats = {(s.item_id, s.region, s.year): s for s in Stat.objects.filter(
        item_id__in={p.item_id for p in page}, region__in={p.region for p in page}, year__in={p.year for p in page})}
    for p in page:
        p.stat = stats.get((p.item_id, p.region, p.year))
    years = sorted(set(Price.objects.values_list("year", flat=True)), reverse=True)
    keep = G.copy()
    keep.pop("page", None)
    files = storage.plan()
    files["total_h"], files["free_h"] = storage.human(files["total"]), storage.human(files["free"])
    for r in files["rows"]:
        r["h"] = storage.human(r["size"])
    return render(request, "panel/narx_clean.html", _nctx(
        request, "clean", files=files, page=page, total=qs.count(),
        all_unused=Price.objects.filter(pin="").filter(Q(outlier=True) | Q(excluded=True)).count(),
        kinds=CLEAN_KINDS, years=years, f=G, qkeep=keep.urlencode(), compact=1, delete_word=DELETE_WORD))


@require_POST
@superuser_required
def narx_clean_files(request):
    from narxbaza import storage
    keys = request.POST.getlist("key")
    if not keys:
        messages.error(request, "Нимани тозалашни белгиланг.")
        return redirect("panel:narx_clean")
    got = storage.cleanup(keys)
    freed = sum(got.values())
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: файллар тозаланди — {storage.human(freed)}")
    messages.success(request, f"Тозаланди: {storage.human(freed)} бўшади.")
    return redirect("panel:narx_clean")


@require_POST
@superuser_required
def narx_clean_prices(request):
    P = request.POST
    back = reverse("panel:narx_clean") + (("?" + P["qs"]) if P.get("qs") else "") + "#prices"
    if P.get("all") == "1":
        if _norm_word(P.get("confirm")) != _norm_word(DELETE_WORD):
            messages.error(request, "Ҳаммасини ўчириш учун тасдиқлаш сўзини ёзинг.")
            return redirect(back)
        prices = list(_unused(P))
    else:
        ids = [int(x) for x in P.getlist("id") if x.isdigit()]
        prices = list(_unused({}).filter(pk__in=ids))
    back = reverse("panel:narx_clean") + (("?" + P["qs"]) if P.get("qs") else "") + "#prices"
    if not prices:
        messages.error(request, "Ўчириладиган нарх танланмаган.")
        return redirect(back)
    n = services.delete_prices(prices, user=request.user)
    audit(request, Event.Kind.ADMIN_BASE, detail=f"нарх базаси: {n} та ишлатилмайдиган нарх ўчирилди")
    messages.success(request, f"{n} та нарх ўчирилди. Паст / ўрта / юқори қайта ҳисобланди.")
    return redirect(back)
