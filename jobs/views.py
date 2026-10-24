import json

from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from openpyxl.utils import column_index_from_string, get_column_letter

from core.pipeline import extra_columns_of
from core.svodka import DEFAULT_PERCENTS, parse_text_number, percent_text, svodka_layout

from .folders import build_project_zip, build_tree, create_project, ensure_running
from .models import Batch, Job, Project
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
LAST_EXTRA_COLUMN = "Z"


def column_labels(job) -> dict:
    """Жадвал устунлари ва номлари: I..M (смета­чи қайта номлаган бўлиши мумкин) + қўшилган N, O, …"""
    names = job.column_names or {}
    labels = {c: (names.get(c) or label) for c, label in CATEGORY_LABELS.items()}
    for col, name in extra_columns_of(names):
        labels[col] = name or f"Устун {col}"
    return labels


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
    recent = recent_work()
    return render(request, "jobs/index.html", {"error": error, "recent": recent, "resume": recent[0] if recent else None})


def _job_sums(jobs):
    total = sum(t for j in jobs for t in [j.result.get("totals", {}).get("s_nds")] if isinstance(t, (int, float)))
    return total, sum(j.result.get("stats", {}).get("NOT_PRICED", 0) for j in jobs if j.status == Job.Status.DONE)


def recent_work(limit=15):
    """Охирги ишлар (папка, объект, алоҳида смета) — энг янгиси биринчи; ҳолати ва суммаси билан."""
    items = []
    for pr in Project.objects.prefetch_related("parts__jobs")[:limit]:
        parts = [p for p in pr.parts.all() if p.stage != Batch.Stage.EMPTY]
        jobs = [j for p in parts for j in p.jobs.all()]
        total, not_priced = _job_sums(jobs)
        finished = sum(1 for p in parts if p.stage not in ("queued", "converting", "pricing"))
        items.append({"kind": "folder", "name": pr.name, "url": reverse("project", args=[pr.id]), "created": pr.created,
                      "meta": f"{len(parts)} та смета", "working": pr.status == Project.Status.PROCESSING,
                      "progress": f"{finished}/{len(parts)}", "total": total, "not_priced": not_priced,
                      "review": sum(1 for p in parts if p.stage in ("review", "failed"))})
    for b in Batch.objects.filter(project__isnull=True).prefetch_related("jobs")[:limit]:
        jobs = list(b.jobs.all())
        total, not_priced = _job_sums(jobs)
        items.append({"kind": "object", "name": b.name, "url": reverse("batch", args=[b.id]), "created": b.created,
                      "meta": f"{len(b.pairs)} та жуфтлик", "working": False, "total": total, "not_priced": not_priced,
                      "review": 0 if b.status == Batch.Status.DONE else 1})
    for j in Job.objects.filter(batch__isnull=True)[:limit]:
        total, not_priced = _job_sums([j]) if j.status == Job.Status.DONE else (0, 0)
        items.append({"kind": "smeta", "name": j.local_name, "url": reverse("job", args=[j.id]), "created": j.created,
                      "meta": j.local_sheet, "working": False, "total": total, "not_priced": not_priced,
                      "review": 1 if j.status != Job.Status.DONE else 0})
    items.sort(key=lambda x: x["created"], reverse=True)
    return items[:limit]


# ===================== sahifa yo'li (breadcrumb) =====================
def crumbs_for(project=None, batch=None, job=None):
    """Папка › ички папка › смета: ҳар бир саҳифадан орқага қайтиш учун."""
    batch = batch or (job.batch if job and job.batch_id else None)
    project = project or (batch.project if batch and batch.project_id else None)
    out = []
    if project:
        out.append({"label": project.name, "url": reverse("project", args=[project.id]), "icon": "folder", "color": "text-amber-500"})
    if batch:
        label = batch.folder or batch.name if project else batch.name
        out.append({"label": label, "url": reverse("batch", args=[batch.id]),
                    "icon": "folder-open" if project else "layers", "color": "text-amber-500" if project else "text-slate-400"})
    if job:
        name = job.local_name
        if batch and job.batch_file is not None and job.batch_file < len(batch.files):
            name = batch.files[job.batch_file]["name"]  # локал файл номи (лист номи «-2026_ЛРВ» тушунарсиз)
        out.append({"label": name, "url": "", "icon": "sheet", "color": "text-emerald-600"})
    return out


# ===================== butun papka =====================
PART_STATES = {
    "queued": ("Навбатда", "slate"),
    "converting": ("Файллар очиляпти", "sky"),
    "pricing": ("Нархланяпти", "sky"),
    "done": ("Тайёр", "emerald"),
    "warn": ("Нархланмаган қатор бор", "rose"),
    "review": ("Текширинг", "amber"),
    "empty": ("Локал топилмади", "slate"),
    "failed": ("Хато", "rose"),
}


@require_POST
def project_create(request):
    """Папка юклаш (JS): ``files`` + ``paths`` (ҳар бирининг папка ичидаги йўли) + ``skipped``."""
    try:
        project = create_project(request.FILES.getlist("files"), request.POST.getlist("paths"),
                                 request.POST.getlist("skipped"))
    except UploadError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    return JsonResponse({"ok": True, "url": reverse("project", args=[project.id])})


def _part_nodes(node, out):
    if node["part"]:
        out.append(node)
    for c in node["folders"]:
        _part_nodes(c, out)
    return out


def project_detail(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    ensure_running(project)
    parts = list(project.parts.prefetch_related("jobs").order_by("folder"))
    tree = build_tree(project, parts)
    # нархланадиган лист йўқ қисмлар (масалан «Умумий қиймати») — жадвал охирида
    nodes = sorted(_part_nodes(tree, []), key=lambda n: n["part"]["state"] == "empty")
    states = [n["part"]["state"] for n in nodes]
    finished = sum(1 for s in states if s not in ("queued", "converting", "pricing"))
    current = next((n for n in nodes if n["part"]["state"] in ("converting", "pricing")), None)
    ctx = {
        "crumbs": crumbs_for(project=project),
        "project": project,
        "tree": tree,
        "nodes": nodes,
        "states": PART_STATES,
        "working": project.status == Project.Status.PROCESSING,
        "current": current,
        "finished": finished,
        "progress": round(100 * finished / len(nodes)) if nodes else 100,
        "count_done": states.count("done"),
        "count_warn": states.count("warn"),
        "count_review": states.count("review") + states.count("failed"),
        "count_skipped": sum(1 for f in project.files if f["skip"]),
        "grand_total": sum(n["part"]["total"] for n in nodes),
        "not_priced_total": sum(n["part"]["not_priced"] for n in nodes),
        "can_download": any(s in ("done", "warn", "review") and n["part"]["jobs"] for s, n in zip(states, nodes)),
    }
    if request.GET.get("partial"):
        return render(request, "jobs/_project_body.html", ctx)
    return render(request, "jobs/project.html", ctx)


def project_download(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    try:
        path = build_project_zip(project)
    except UploadError as e:
        raise Http404(str(e))
    return FileResponse(open(path, "rb"), as_attachment=True, filename=path.name)


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
        "crumbs": crumbs_for(batch=batch),
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
    ctx = {"job": job, "modes": MODE_LABELS.items(), "crumbs": crumbs_for(job=job),
           "siblings": list(job.batch.jobs.order_by("batch_order")) if job.batch_id else []}
    if job.status == Job.Status.DONE:
        res = job.result
        totals = res["totals"]
        mode = res.get("mode", "compat")
        labels = column_labels(job) if mode == "smart" else dict(CATEGORY_LABELS)
        res_groups = _res_groups(res, labels) if mode == "smart" else []
        view = request.GET.get("view") or ("res" if res_groups else "local")
        if not res_groups:
            view = "local"
        ctx.update(
            view=view,
            res_groups=res_groups,
            result_mode=mode,
            # «N та қатор нархланмади» — ён панелда ва сводкада бор, бу ерда такрорламаймиз
            warnings=[w for w in res.get("warnings", []) if "нархланмади" not in w],
            columns=res.get("columns", {}),
            # smart: фақат бор нарсалар (0 бўлган карталар чалғитмасин); compat — эски дастур каби ҳаммаси
            stats=[(key, label, res["stats"].get(key, 0), color) for key, label, color in STAT_CARDS[mode]
                   if mode != "smart" or res["stats"].get(key, 0) or key in ("EXACT", "NOT_PRICED")],
            svodka=_svodka_rows(res, mode),
            grid_columns=list(labels.items()),
            grid_span=len(labels) + 6,
            extra_letters=[c for c in labels if c not in CATEGORY_LABELS],
            renamed=[c for c in CATEGORY_LABELS if (job.column_names or {}).get(c)],
            can_add_column=mode == "smart" and _next_column(labels) is not None,
            percents_changed=bool(job.percents) and mode == "smart",
            s_nds=totals.get("s_nds"),
            has_errors=any(isinstance(v, str) for v in totals.values()),
            rows=res["rows"],
            problems=res["problems"],
            category_counts=[(c, label, sum(1 for r in res["rows"] if _is_resource(r) and r.get("category", "K") == c))
                             for c, label in labels.items()],
            category_choices=labels.items(),
            category_suggestions=_category_suggestions(res["rows"]),
            override_count=sum(1 for r in res["rows"] if r.get("override")),
            bez_nds=totals.get("bez_nds"),
            not_priced=res["stats"].get("NOT_PRICED", res["stats"].get("NON_NUMERIC", 0)),
            sugg_price_count=sum(1 for r in res["rows"] if r.get("suggested_price") is not None and not r.get("manual")),
            manual_count=sum(1 for r in res["rows"] if r.get("manual")),
            sugg_cat_count=sum(1 for r in res["rows"] if r.get("suggested_category")),
        )
    return render(request, "jobs/job.html", ctx)


def _is_resource(row) -> bool:
    """Жадвалда танлаш/ўтказиш мумкин бўлган қатор: номи бор."""
    return bool(row.get("name"))


ZERO_STATUSES = ("WORK_HEADER", "MACHINIST")


def _next_column(labels):
    last = max(column_index_from_string(c) for c in labels)
    nxt = get_column_letter(last + 1)
    return nxt if column_index_from_string(nxt) <= column_index_from_string(LAST_EXTRA_COLUMN) else None


def _res_groups(res, labels=None):
    """Smart: қаторлар ресурс файлидаги бўлимлар тартибида («ТРУДОВЫЕ РЕСУРСЫ», «…МАШИНЫ…»,
    «МАТЕРИАЛЬНЫЕ…», «ОБОРУДОВАНИЕ»); бўлим ичида — ресурслар RES даги тартибда."""
    sections = res.get("res_sections") or []
    if not sections:
        return []
    labels = labels or CATEGORY_LABELS
    groups = [{"title": s["title"], "column": s["column"], "label": labels[s["column"]], "rows": []}
              for s in sections]
    missing = {"title": "Ресурс файлида топилмаганлар", "column": None, "label": "", "rows": [],
               "hint": "аниқ мос ресурс йўқ — нархни қўлда киритинг"}
    other = {"title": "RES бўлимидан ташқари", "column": None, "label": "", "rows": [], "hint": ""}
    zero = {"title": "Иш сарлавҳалари ва машинистлар", "column": None, "label": "", "rows": [],
            "hint": "суммага кирмайди (0)"}
    for r in res["rows"]:
        if not _is_resource(r):
            continue
        idx = r.get("res_section")
        if r.get("status") in ZERO_STATUSES:
            zero["rows"].append(r)
        elif idx is None:
            missing["rows"].append(r)
        elif idx < 0:
            other["rows"].append(r)
        else:
            groups[idx]["rows"].append(r)
    for g in groups:
        g["rows"].sort(key=lambda r: (r.get("res_row") or 0, r["row"]))
    out = []
    for i, g in enumerate(groups + [other, missing, zero]):
        if not g["rows"] and g["column"] is None:
            continue
        g["id"] = i
        g["count"] = len(g["rows"])
        g["sum"] = sum(r["h"] for r in g["rows"] if isinstance(r.get("h"), (int, float)) and not isinstance(r["h"], bool))
        # ресурс бўлими бошқа устунни кўрсатяпти: «M га ўтказамизми?»
        g["to_move"] = [r["row"] for r in g["rows"] if g["column"] and r.get("category") != g["column"]
                        and not r.get("override")]
        out.append(g)
    return out


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
    if category and category not in column_labels(job):
        return JsonResponse({"ok": False, "error": f"нотўғри устун: {category}"}, status=400)
    known = {r["row"]: r for r in job.result.get("rows", []) if _is_resource(r)}
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
    known = {r["row"]: r for r in job.result.get("rows", []) if _is_resource(r)}
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
    extra = [tuple(c) for c in res.get("extra_columns", [])]
    for i, (key, text, pct) in enumerate(svodka_layout(extra)):
        p = used.get(key, pct) if pct is not None else None
        if key == "s_nds" and mode == "smart":
            text = f"Всего с НДС      {percent_text(p)} %"
        rows.append({"row": res["svodka_start"] + i, "key": key, "text": text, "pct": p,
                     "default": pct, "value": res["totals"].get(key)})
    return rows


@require_POST
def job_columns(request, job_id):
    """JSON: ``{"action": "add", "name": "Монтаж"}`` — кейинги бўш устун (N, O, …);
    ``{"action": "rename", "col": "K", "name": "…"}`` (бўш ном — асл номига қайтади);
    ``{"action": "remove", "col": "N"}`` — қўшилган устун, ундаги қаторлар асл устунига қайтади."""
    job = get_object_or_404(Job, id=job_id)
    if job.status != Job.Status.DONE or job.mode != "smart":
        return JsonResponse({"ok": False, "error": "Устунлар фақат Smart режимда"}, status=400)
    try:
        data = json.loads(request.body or b"{}")
        action = data["action"]
        col = str(data.get("col") or "").strip().upper()
        name = " ".join(str(data.get("name") or "").split())[:60]
    except (ValueError, TypeError, KeyError):
        return JsonResponse({"ok": False, "error": "нотўғри сўров"}, status=400)
    labels = column_labels(job)
    names = dict(job.column_names or {})
    categories = None
    if action == "add":
        col = _next_column(labels)
        if not col:
            return JsonResponse({"ok": False, "error": f"{LAST_EXTRA_COLUMN} устунидан кейин қўшиб бўлмайди"}, status=400)
        names[col] = name or f"Устун {col}"
    elif action == "rename" and col in labels:
        if col in CATEGORY_LABELS and not name:
            names.pop(col, None)
        else:
            names[col] = name or f"Устун {col}"
    elif action == "remove" and col in labels and col not in CATEGORY_LABELS:
        names.pop(col)
        categories = {r: c for r, c in (job.categories or {}).items() if c != col}
    else:
        return JsonResponse({"ok": False, "error": f"нотўғри амал ёки устун: {action} {col}"}, status=400)
    job.column_names = names
    run_job(job, None, None, job.fuzzy, job.mode, None, categories)
    if job.status != Job.Status.DONE:
        return JsonResponse({"ok": False, "error": job.error}, status=500)
    return JsonResponse({"ok": True, "col": col})


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
