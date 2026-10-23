import json

from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from core.svodka import DEFAULT_PERCENTS, SVODKA_ROWS, parse_text_number, percent_text

from .models import Batch, Job
from .services import (
    UploadError, analyze_batch, build_combined, confirm_batch, create_batch, create_job, parse_percents, run_job,
)

STAT_CARDS = {
    "compat": [
        ("EXACT", "Аниқ топилди", "emerald"),
        ("FUZZY", "Ўхшаш (fuzzy)", "amber"),
        ("AMBIGUOUS", "ШАБҲАЛИ", "orange"),
        ("NOT_FOUND", "ТОПИЛМАДИ", "rose"),
        ("ZERO_RULE", "Нол қоидаси", "slate"),
        ("NON_NUMERIC", "G да рақам эмас", "rose"),
    ],
    "smart": [
        ("EXACT", "Аниқ топилди", "emerald"),
        ("NOT_PRICED", "Нархланмади — қўлда", "rose"),
        ("FUZZY", "Ўхшаш (аниқ эмас)", "rose"),
        ("AMBIGUOUS", "ШАБҲАЛИ", "rose"),
        ("UNIT_MISMATCH", "Бирлик бошқа", "rose"),
        ("NO_PRICE", "Нархсиз ресурс", "rose"),
        ("NOT_FOUND", "ТОПИЛМАДИ", "rose"),
        ("MANUAL", "Қўлда нархланди", "sky"),
        ("WORK_HEADER", "Иш сарлавҳаси → 0", "slate"),
        ("MACHINIST", "Машинист → 0", "slate"),
    ],
}
MODE_LABELS = {"smart": "Smart (тавсия)", "compat": "Эски дастур каби (compat)"}
CATEGORY_LABELS = {"I": "Иш ҳақи", "J": "Машина", "K": "Материал", "L": "Кабель", "M": "Ускуна"}


@require_http_methods(["GET", "POST"])
def index(request):
    error = None
    if request.method == "POST":
        files = request.FILES.getlist("files")
        local = request.FILES.get("local")
        try:
            if files:
                # Universal yuklash: bitta obyekt fayli yoki bir nechta fayl — tizim o'zi ajratadi
                batch = create_batch(files)
                return redirect("batch", batch_id=batch.id)
            if local:
                job = create_job(local, request.FILES.get("res"))  # eski ikki maydonli usul
                return redirect("job", job_id=job.id)
            error = "Файлни танланг."
        except UploadError as e:
            error = str(e)
    return render(request, "jobs/index.html", {
        "error": error,
        "recent_batches": Batch.objects.all()[:8],
        "recent": Job.objects.filter(batch__isnull=True)[:8],
    })


# ===================== obyekt (bir/ko'p fayl) =====================
ROLE_LABELS = {"local": "Локал", "resource": "Ресурс", "unknown": "Ўтказиш"}


def _batch_tree(batch):
    """Стройка → Объект → жуфтликлар (кўрсатиш учун)."""
    sheets = {s["key"]: s for s in batch.sheets}
    tree = {}
    for n, p in enumerate(batch.pairs):
        loc = sheets[p["local"]]
        st = tree.setdefault(loc.get("stroyka") or "—", {})
        st.setdefault(loc.get("obj") or "—", []).append(dict(p, n=n, loc=loc, res_sheet=sheets.get(p["res"])))
    return [{"stroyka": s, "objects": [{"obj": o, "pairs": ps} for o, ps in objs.items()]} for s, objs in tree.items()]


def batch_detail(request, batch_id):
    batch = get_object_or_404(Batch, id=batch_id)
    sheets = batch.sheets
    used_res = {p["res"] for p in batch.pairs if p.get("res")}
    jobs = list(batch.jobs.order_by("batch_order"))
    ctx = {
        "batch": batch,
        "tree": _batch_tree(batch),
        "resources": [s for s in sheets if s["role"] == "resource" and not s.get("duplicate_of")],
        "unused": [s for s in sheets if s["role"] == "resource" and not s.get("duplicate_of") and s["key"] not in used_res],
        "duplicates": [s for s in sheets if s.get("duplicate_of")],
        "unknown": [s for s in sheets if s["role"] == "unknown"],
        "sheets_by_file": [(i, f, [s for s in sheets if s["file"] == i]) for i, f in enumerate(batch.files)],
        "role_labels": ROLE_LABELS.items(),
        "ready": sum(1 for p in batch.pairs if p.get("include") and p.get("res")),
        "jobs": jobs,
        "grand_total": sum(j.result.get("totals", {}).get("s_nds") or 0 for j in jobs
                           if isinstance(j.result.get("totals", {}).get("s_nds"), (int, float))),
        "not_priced_total": sum(j.result.get("stats", {}).get("NOT_PRICED", 0) for j in jobs),
    }
    return render(request, "jobs/batch.html", ctx)


@require_POST
def batch_replan(request, batch_id):
    batch = get_object_or_404(Batch, id=batch_id)
    roles = {}
    for i, s in enumerate(batch.sheets):
        role = request.POST.get(f"role_{i}")
        if role in ROLE_LABELS and role != s["role"]:
            roles[s["key"]] = role
        elif s["key"] in (batch.roles or {}) and role == s["role"]:
            roles[s["key"]] = role  # аввал қўлда белгиланган — сақланади
    analyze_batch(batch, roles)
    batch.status = Batch.Status.REVIEW
    batch.save()
    return redirect("batch", batch_id=batch.id)


@require_POST
def batch_confirm(request, batch_id):
    batch = get_object_or_404(Batch, id=batch_id)
    decisions = {}
    for n in range(len(batch.pairs)):
        decisions[n] = {
            "include": request.POST.get(f"include_{n}") == "1",
            "res": request.POST.get(f"res_{n}", ""),
            "label": request.POST.get(f"label_{n}", ""),
        }
    confirm_batch(batch, decisions)
    return redirect("batch", batch_id=batch.id)


def batch_download(request, batch_id):
    batch = get_object_or_404(Batch, id=batch_id)
    try:
        path = build_combined(batch)
    except UploadError as e:
        raise Http404(str(e))
    return FileResponse(open(path, "rb"), as_attachment=True, filename=path.name)


def job_detail(request, job_id):
    job = get_object_or_404(Job, id=job_id)
    ctx = {"job": job, "modes": MODE_LABELS.items(),
           "siblings": list(job.batch.jobs.order_by("batch_order")) if job.batch_id else []}
    if job.status == Job.Status.DONE:
        res = job.result
        totals = res["totals"]
        mode = res.get("mode", "compat")
        ctx.update(
            result_mode=mode,
            # «N та қатор нархланмади» — ён панелда ва сводкада бор, бу ерда такрорламаймиз
            warnings=[w for w in res.get("warnings", []) if "нархланмади" not in w],
            columns=res.get("columns", {}),
            # smart: фақат бор нарсалар (0 бўлган карталар чалғитмасин); compat — эски дастур каби ҳаммаси
            stats=[(key, label, res["stats"].get(key, 0), color) for key, label, color in STAT_CARDS[mode]
                   if mode != "smart" or res["stats"].get(key, 0) or key in ("EXACT", "NOT_PRICED")],
            svodka=_svodka_rows(res, mode),
            percents_changed=bool(job.percents) and mode == "smart",
            s_nds=totals.get("s_nds"),
            has_errors=any(isinstance(v, str) for v in totals.values()),
            rows=res["rows"],
            problems=res["problems"],
            category_counts=[(c, label, sum(1 for r in res["rows"] if r.get("name") and r.get("category", "K") == c))
                             for c, label in CATEGORY_LABELS.items()],
            category_choices=CATEGORY_LABELS.items(),
            category_suggestions=_category_suggestions(res["rows"]),
            override_count=sum(1 for r in res["rows"] if r.get("override")),
            bez_nds=totals.get("bez_nds"),
            not_priced=res["stats"].get("NOT_PRICED", res["stats"].get("NON_NUMERIC", 0)),
            sugg_price_count=sum(1 for r in res["rows"] if r.get("suggested_price") is not None and not r.get("manual")),
            manual_count=sum(1 for r in res["rows"] if r.get("manual")),
            sugg_cat_count=sum(1 for r in res["rows"] if r.get("suggested_category")),
        )
    return render(request, "jobs/job.html", ctx)


def _category_suggestions(rows):
    groups = {}
    for r in rows:
        cat = r.get("suggested_category")
        if cat:
            g = groups.setdefault(cat, {"cat": cat, "label": CATEGORY_LABELS[cat], "count": 0, "reasons": []})
            g["count"] += 1
            if r["suggest_reason"] not in g["reasons"] and len(g["reasons"]) < 3:
                g["reasons"].append(r["suggest_reason"])
    return [groups[c] for c in CATEGORY_LABELS if c in groups]


@require_POST
def job_categories(request, job_id):
    """JSON: ``{"rows": [183, 281], "category": "M"}``; ``category: ""`` — asl holiga qaytarish."""
    job = get_object_or_404(Job, id=job_id)
    if job.status != Job.Status.DONE or job.mode != "smart":
        return JsonResponse({"ok": False, "error": "Устунни фақат Smart режимдаги натижада ўзгартириш мумкин"}, status=400)
    try:
        data = json.loads(request.body or b"{}")
        rows = [int(r) for r in data["rows"]]
        category = data.get("category") or ""
    except (ValueError, TypeError, KeyError):
        return JsonResponse({"ok": False, "error": "нотўғри сўров"}, status=400)
    if category and category not in CATEGORY_LABELS:
        return JsonResponse({"ok": False, "error": f"нотўғри устун: {category}"}, status=400)
    known = {r["row"]: r for r in job.result.get("rows", []) if r.get("name")}
    unknown = [r for r in rows if r not in known]
    if not rows or unknown:
        return JsonResponse({"ok": False, "error": f"бундай қатор йўқ: {unknown[:5]}"}, status=400)

    categories = dict(job.categories or {})
    for r in rows:
        if not category or category == known[r]["default_category"]:
            categories.pop(str(r), None)  # дастур аниқлагани билан бир хил — қўлда белги шарт эмас
        else:
            categories[str(r)] = category
    run_job(job, None, None, job.fuzzy, job.mode, None, categories)
    if job.status != Job.Status.DONE:
        return JsonResponse({"ok": False, "error": job.error}, status=500)
    return JsonResponse({"ok": True, "changed": len(rows), "overrides": len(job.categories)})


def _as_price(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return parse_text_number(str(v)) if v is not None else None


@require_POST
def job_prices(request, job_id):
    """JSON: ``{"rows": [...], "action": "accept" | "set" | "clear", "price": "4 600 000"}``.
    accept — taklif narxini qabul qilish; set — bitta narxni hammasiga; clear — qo'lda narxni bekor qilish."""
    job = get_object_or_404(Job, id=job_id)
    if job.status != Job.Status.DONE or job.mode != "smart":
        return JsonResponse({"ok": False, "error": "Нархни фақат Smart режимдаги натижада ўзгартириш мумкин"}, status=400)
    try:
        data = json.loads(request.body or b"{}")
        rows = [int(r) for r in data["rows"]]
        action = data["action"]
    except (ValueError, TypeError, KeyError):
        return JsonResponse({"ok": False, "error": "нотўғри сўров"}, status=400)
    known = {r["row"]: r for r in job.result.get("rows", []) if r.get("name")}
    unknown = [r for r in rows if r not in known]
    if not rows or unknown or action not in ("accept", "set", "clear"):
        return JsonResponse({"ok": False, "error": f"нотўғри сўров: {unknown[:5] or action}"}, status=400)

    prices = dict(job.prices or {})
    skipped = []
    if action == "set":
        price = _as_price(data.get("price"))
        if price is None or price < 0:
            return JsonResponse({"ok": False, "error": "нарх рақам бўлиши керак (масалан 4 600 000)"}, status=400)
        for r in rows:
            prices[str(r)] = price
    elif action == "accept":
        for r in rows:
            price = _as_price(known[r].get("suggested_price"))
            if price is None:
                skipped.append(r)
            else:
                prices[str(r)] = price
    else:
        for r in rows:
            prices.pop(str(r), None)
    changed = len(rows) - len(skipped)
    if changed:
        run_job(job, None, None, job.fuzzy, job.mode, prices=prices)
        if job.status != Job.Status.DONE:
            return JsonResponse({"ok": False, "error": job.error}, status=500)
    return JsonResponse({"ok": True, "changed": changed, "skipped": len(skipped)})


def _svodka_rows(res, mode):
    used = res.get("percents") or DEFAULT_PERCENTS
    rows = []
    for i, (key, text, pct) in enumerate(SVODKA_ROWS):
        p = used.get(key, pct) if pct is not None else None
        if key == "s_nds" and mode == "smart":
            text = f"Всего с НДС      {percent_text(p)} %"
        rows.append({"row": res["svodka_start"] + i, "key": key, "text": text, "pct": p,
                     "default": pct, "value": res["totals"].get(key)})
    return rows


@require_POST
def job_run(request, job_id):
    job = get_object_or_404(Job, id=job_id)
    if "reset_percents" in request.POST:
        percents = {}
    elif any(k.startswith("pct_") for k in request.POST):
        percents = parse_percents(request.POST, job.percents)
    else:
        percents = None  # yuqoridagi forma: foizlar o'zgarmaydi
    run_job(job, request.POST.get("local_sheet"), request.POST.get("res_sheet"),
            request.POST.get("fuzzy") or job.fuzzy, request.POST.get("mode"), percents)
    return redirect("job", job_id=job.id)


def job_download(request, job_id):
    job = get_object_or_404(Job, id=job_id)
    if job.status != Job.Status.DONE or not job.output_path:
        raise Http404("Натижа ҳали тайёр эмас")
    path = job.abs(job.output_path)
    if not path.is_file():
        raise Http404("Файл топилмади")
    return FileResponse(open(path, "rb"), as_attachment=True, filename=job.download_name)
