import json
import re
import time
import uuid
from datetime import timedelta
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import redirect_to_login
from django.core.cache import cache
from django.db.models import Prefetch
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from openpyxl.utils import column_index_from_string, get_column_letter

from core.batch import KIND_LABELS
from core.pipeline import UNPRICED_STATUSES, extra_columns_of
from core.svodka import DEFAULT_PERCENTS, PERCENT_LABELS, parse_text_number, percent_text, svodka_layout

from panel.audit import log as audit
from panel.models import Event

from . import bulk_price, preview, recalc, xlview
from .archives import is_archive
from .guide import FLOW, GUIDE_PAGES, help_groups
from .folders import (
    build_project_zip, build_tree, create_project, delete_project, ensure_running, is_shown, queue_part,
    PAUSED, ready_stage, resume_project, settle, start_pricing, stop_project,
)
from .models import Batch, Job, Project
from .services import (
    BASE_RES, UploadError, analyze_batch, forget_sheet, remember_sheets, apply_decisions, base_period_of, check_quota, pair_ready, build_combined, confirm_batch, create_batch, create_job, delete_batch, delete_job,
    nf_auto_of, parse_percents, remember_base_period, remember_nf_auto, remember_percents, remembered_percents, unpriced_res,
    price_source_of, remember_price_source, base_only, batch_original, replace_batch_file, open_suggestions,
    can_undo_file, refresh_file_jobs, undo_batch_file,
)
from .templatetags.narx import file_title, pct as pct_text, pct_input, readable, som

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
        ("BASE", "Ресурс базасидан", "violet"),
        ("MANUAL", "Қўлда нархланди", "sky"),
        ("WORK_HEADER", "Иш сарлавҳаси → 0", "slate"),
        ("MACHINIST", "Машинист → 0", "slate"),
    ],
}
MODE_LABELS = {"smart": "Smart (тавсия)", "compat": "Эски дастур каби (compat)"}
CATEGORY_LABELS = {"I": "Иш ҳақи", "J": "Машина", "K": "Материал", "L": "Кабель", "M": "Ускуна"}
LAST_EXTRA_COLUMN = "Z"


_KIND_OF = {Project: "folder", Batch: "object", Job: "smeta"}


def _get_work(request, model, **kw):
    """Ўз иши. Суперфойдаланувчи текширув учун бошқанинг ишини ҳам очади — фақат GET (кўриш ва юклаб олиш);
    ўзгартирадиган (POST) сўровлар фақат эгасига. Бошқанинг иши очилса — журналга ёзилади."""
    if request.method in ("GET", "HEAD") and request.user.is_superuser:
        obj = get_object_or_404(model, **kw)
        if obj.owner_id != request.user.pk:
            request.viewing_owner = obj.owner
            # саҳифа ўзи янгиланиб туради — журналга 10 дақиқада бир марта
            if cache.add(f"panel:viewed:{request.user.pk}:{obj.pk}", 1, 600):
                audit(request, Event.Kind.ADMIN_VIEW, target=obj.owner, work=obj, work_kind=_KIND_OF[model],
                      detail=str(getattr(obj, "name", "") or getattr(obj, "local_name", "")))
        return obj
    return get_object_or_404(model, owner=request.user, **kw)


def _audit_download(request, obj, kind, name, *, what="натижа", force=False):
    """Админ бошқа одамнинг файлини юклаб олса — журналга: текширув режимида (``viewing_owner``)
    ёки панелдан (``force``; ``what`` — нима: «натижа», «асл файл», «асл файллар (ZIP)»)."""
    if force or getattr(request, "viewing_owner", None):
        audit(request, Event.Kind.ADMIN_DOWNLOAD, target=obj.owner, work=obj, work_kind=kind, detail=f"{what}: {name}")


def column_labels(job) -> dict:
    """Жадвал устунлари ва номлари: I..M (смета­чи қайта номлаган бўлиши мумкин) + қўшилган N, O, …"""
    names = job.column_names or {}
    labels = {c: (names.get(c) or label) for c, label in CATEGORY_LABELS.items()}
    for col, name in extra_columns_of(names):
        labels[col] = name or f"Устун {col}"
    return labels


def _from_inside(request) -> bool:
    """Шу сайтнинг бошқа саҳифасидан келинганми (Referer — ўз хостимиз)."""
    from urllib.parse import urlsplit
    ref = request.headers.get("Referer") or ""
    return bool(ref) and urlsplit(ref).netloc == request.get_host()


@login_not_required
@require_http_methods(["GET", "POST"])
def index(request):
    """Бош саҳифа: биринчи келган (кирмаган) одамга — «Тизим ҳақида» (landing), кирганга — юклаш."""
    if not request.user.is_authenticated:
        if request.method == "POST":
            return redirect_to_login(request.get_full_path())
        return landing(request)
    if request.method == "GET" and not request.GET and not _from_inside(request):
        # сайт янгидан очилди (манзил сатри, хатчўп, эслаб қолинган сессия) — аввал «Иш столи»;
        # сайт ичидаги «Смета юклаш» ҳаволалари (Referer — ўз сайтимиз) юклаш саҳифасини очади
        return redirect("dashboard")
    error = None
    xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"  # юклаш саҳифаси файлларни XHR билан юборади
    if request.method == "POST":
        t0, kind = time.monotonic(), "object"
        try:
            check_quota(request.user)  # файллар ўқилишидан олдин
            files = request.FILES.getlist("files")
            local = request.FILES.get("local")
            if files and any(is_archive(f.name) for f in files):
                # ZIP/RAR — ичида папкалар бўлади: папка каби очилади (ҳисоблаш смета­чи босгандагина)
                kind = "folder"
                project = create_project(request.user, files, [f.name for f in files])
                request.track_work = project.pk  # фойдаланувчи йўли (panel.track)
                audit(request, Event.Kind.UPLOAD, work=project, work_kind="folder", detail=project.name)
                return _went(xhr, reverse("project", args=[project.id]), _upload_timing(request, kind, t0, project))
            if files:
                # Universal yuklash: bitta obyekt fayli yoki bir nechta fayl — tizim o'zi ajratadi
                batch = create_batch(request.user, files)
                request.track_work = batch.pk
                audit(request, Event.Kind.UPLOAD, work=batch, work_kind="object", detail=batch.name)
                timing = _upload_timing(request, kind, t0, batch)
                if xhr:  # юклаш саҳифаси листлар ва жуфтликларни шу ернинг ўзида кўрсатади (_picker.html)
                    return JsonResponse({"ok": True, "url": reverse("batch", args=[batch.id]), "id": str(batch.id),
                                         "picker": reverse("batch", args=[batch.id]) + "?partial=picker",
                                         "delete": reverse("work_delete", args=["object", batch.id]),
                                         **({"timing": timing.pk} if timing else {})})
                return _went(xhr, reverse("batch", args=[batch.id]), timing)
            if local:
                job = create_job(request.user, local, request.FILES.get("res"))  # eski ikki maydonli usul
                request.track_work = job.pk
                audit(request, Event.Kind.UPLOAD, work=job, work_kind="smeta", detail=job.local_name)
                _upload_timing(request, "smeta", t0, job)
                return redirect("job", job_id=job.id)
            error = "Файлни танланг."
        except UploadError as e:
            error = str(e)
            request.track_detail = error[:200]
            if request.FILES:
                _upload_timing(request, kind, t0, error=error)
        if xhr:
            return JsonResponse({"ok": False, "error": error}, status=400)
    recent = work_items(request.user, limit=6)
    return render(request, "jobs/index.html", {"error": error, "recent": recent, "resume": recent[0] if recent else None,
                                               "nav": "upload", "nf_auto": nf_auto_of(request.user),
                                               "price_source": price_source_of(request.user),
                                               "pct_form": _pct_form(request.user.pk),
                                               **_base_ctx(base_period_of(request.user))})


def _went(xhr, url, timing=None):
    """Юклаш тугади: XHR'га — қаерга ўтиш (JSON), оддий формага — redirect. ``timing`` — юклаш вақти ёзуви:
    браузер юбориш вақтини (``upload_timing``) шунга қўшади."""
    if not xhr:
        return redirect(url)
    return JsonResponse({"ok": True, "url": url, **({"timing": timing.pk} if timing else {})})


def _upload_timing(request, kind, t0, work=None, error=""):
    """Юклашнинг сервердаги вақти (сақлаш, архив очиш, файлларни очиш) — панелдаги «Тезлик» учун.
    Ёзилмаса ҳам юклаш бузилмайди."""
    from .models import UploadTiming
    try:
        files = request.FILES.getlist("files") or [f for f in (request.FILES.get("local"), request.FILES.get("res")) if f]
        return UploadTiming.objects.create(
            owner=request.user, kind=kind, files=len(files), size=sum(f.size or 0 for f in files),
            name=(getattr(work, "name", "") or getattr(work, "local_name", "") or (files[0].name if files else ""))[:255],
            server_s=round(time.monotonic() - t0, 3), error=error[:300], work_id=str(work.pk) if work else "")
    except Exception:  # noqa: BLE001
        return None


@require_POST
def upload_timing(request):
    """Браузер ўлчаган юбориш вақти (файллар серверга етиб боргунча): ``id``, ``upload_s``."""
    from .models import UploadTiming
    try:
        sec = float(request.POST.get("upload_s", ""))
        pk = int(request.POST.get("id", ""))
    except ValueError:
        return JsonResponse({"ok": False}, status=400)
    if not 0 <= sec < 86400:
        return JsonResponse({"ok": False}, status=400)
    n = UploadTiming.objects.filter(pk=pk, owner=request.user, upload_s__isnull=True).update(upload_s=round(sec, 3))
    return JsonResponse({"ok": bool(n)})


def _base_ctx(period) -> dict:
    """«Нарх манбаи» танлови учун (ресурс базаси ёқилган бўлса): жорий давр ва рўйхатлар."""
    from resbase.models import QUARTERS
    from resbase.regions import REGIONS
    from resbase.services import base_on, new_base_on, period_label
    if not base_on():
        return {"base_on": False}
    y = timezone.now().year
    out = {"base_on": True, "base_period": period, "base_label": period_label(period), "regions": REGIONS,
           "years": list(range(y + 1, 2017, -1)), "quarters": QUARTERS}
    if new_base_on():  # янги база: қайси нарх — паст / ўрта / юқори (смета­чи танлайди)
        from narxbaza.models import LEVELS
        from narxbaza.services import level_of
        out.update(levels=LEVELS, level=level_of(period))
    return out


@require_POST
def base_period(request):
    """Юклаш саҳифасида смета­чи танлаган вилоят ва давр — кейинги юклашлар шу билан нархланади."""
    from resbase.services import parse_period, period_label
    period = parse_period(request.POST)
    remember_base_period(request.user, period)
    return JsonResponse({"ok": True, "label": period_label(period)})


@require_POST
def nf_mode(request):
    """Юклаш саҳифасида танланган усул: «Автоматик» — бўлим номлари ўзи тозаланади, «Қўлда» — смета­чи ўзи."""
    remember_nf_auto(request.user, request.POST.get("nf_mode") != "manual")
    return JsonResponse({"ok": True})


@require_POST
def price_source(request):
    """Юклаш саҳифасидаги «Нарх манбаи»: ``res`` — локал + RES (олдинги усул), ``base`` — фақат ресурс базаси."""
    remember_price_source(request.user, request.POST.get("source", "res"))
    return JsonResponse({"ok": True, "source": price_source_of(request.user)})


@require_POST
def upload_percents(request):
    """Юклаш саҳифасида («Автоматик» усул) тасдиқланган сводка фоизлари — кейинги сметалар шу фоизлар билан."""
    saved = remembered_percents(request.user)
    data = {f"pct_{k}": (request.POST.get(f"pct_{k}", "").strip() or "0") for k in DEFAULT_PERCENTS
            if f"pct_{k}" in request.POST}  # бўш катак — 0 (смета саҳифасидагидек)
    if not data:
        return JsonResponse({"ok": False, "error": "фоиз йўқ"}, status=400)
    remember_percents(request.user, {**saved, **parse_percents(data, saved)})
    return JsonResponse({"ok": True})


# Рўйхатлар учун сметанинг фақат жами суммаси ва ҳолати керак: тўлиқ ``result`` (минглаб қатор) юкланмайди —
# жамлама ``Job.sum_*`` майдонларида (SQLite JSON'дан қиймат олиш учун ҳар бир ``result``ни бутун ўқирди)
def light_jobs(qs):
    return qs.only("id", "created", "status", "owner_id", "batch_id", "batch_order", "local_name", "local_sheet",
                   "recalc_seq", "recalc_done", *Job.SUMMARY_FIELDS)


def _summary(j) -> dict:
    """``result``дан керакли қисм — ``Job.sum_*`` майдонларидан, тўлиқ натижани юкламасдан."""
    return {"totals": {"s_nds": j.sum_total}, "stats": {"NOT_PRICED": j.sum_not_priced}, "checks": j.sum_checks}


def _job_sums(jobs):
    total = sum(t for j in jobs for t in [_summary(j).get("totals", {}).get("s_nds")] if isinstance(t, (int, float)))
    return total, sum(_summary(j).get("stats", {}).get("NOT_PRICED", 0) or 0 for j in jobs if j.status == Job.Status.DONE)


# Ишнинг умумий ҳолати (тарих ва дашборд): номи, ранги, белгиси
WORK_STATES = {
    "working": ("Ишланяпти", "sky", "loader"),
    "awaiting": ("Ҳисоблашга тайёр", "violet", "clock"),
    "stopped": ("Тўхтатилди", "slate", "stop"),
    "review": ("Текширинг", "amber", "alert"),
    "failed": ("Хато", "rose", "x-circle"),
    "warn": ("Нархланмаган бор", "rose", "alert-circle"),
    "done": ("Тайёр", "emerald", "check"),
    "empty": ("Смета топилмади", "slate", "minus"),
}
# Нега смета топилмади — ўтказиб юборилган листлар тури (core.batch.KIND_*) бўйича
SKIP_REASONS = {
    "priced": "локал смета аллақачон нархланган",
    "act": "дефект акти — ишлар рўйхати",
    "summary": "сводка / умумий ҳисоб",
    "catalog": "нарх каталоги (миқдор йўқ)",
    "empty": "бўш лист",
}


def _skip_reason(sheets) -> str:
    """Листлар орасида локал смета йўқлигининг сабаби: «локал смета аллақачон нархланган · сводка»."""
    kinds = {s.get("kind") for s in sheets if s.get("role") != "local"}
    reasons = [r for k, r in SKIP_REASONS.items() if k in kinds]
    return " · ".join(reasons) or "«Наименование» ва «Ед. изм.» жадвали топилмади"
# Смета (папка қисми) босқичлари — дашборддаги «қувур»
PIPELINE = [
    ("opening", "Очиляпти", "sky", "Навбатда ёки файллари очиляпти"),
    ("pricing", "Ҳисобланяпти", "indigo", "Ҳозир фонда нархланяпти"),
    ("pending", "Ҳисобланмаган", "violet", "Тайёр ёки тўхтатилган — «Ҳисоблашни бошлаш»ни кутяпти"),
    ("review", "Текширинг", "amber", "Ресурс танлаш ёки хатони кўриш керак"),
    ("warn", "Нархланмаган қатор бор", "rose", "Ҳисобланди, лекин айрим қаторлар қўлда нархланиши керак"),
    ("done", "Ҳисобланди", "emerald", "Тўлиқ нархланди"),
]
_STAGE_PIPE = {"queued": "opening", "converting": "opening", "pricing": "pricing", "ready": "pending",
               "stopped": "pending", "review": "review", "failed": "review"}


def _pipe_of_jobs(jobs, fallback):
    if any(j.recalc_pending for j in jobs):
        return "pricing"
    if any(_summary(j).get("checks") for j in jobs if j.status == Job.Status.DONE):
        return "review"
    if any(j.status == Job.Status.DONE for j in jobs):
        return "warn" if any(_summary(j).get("stats", {}).get("NOT_PRICED") for j in jobs) else "done"
    return fallback


def work_items(user, limit=None):
    """Ишлар (папка, объект, алоҳида смета) — энг янгиси биринчи; ҳолати ва суммаси билан.
    ``pipes`` — ҳар бир смета (папка қисми) қайси босқичда: дашборд статистикаси шундан йиғилади."""
    items = []
    for pr in Project.objects.filter(owner=user).prefetch_related(Prefetch("parts__jobs", light_jobs(Job.objects)))[:limit]:
        parts = [p for p in pr.parts.all() if p.stage != Batch.Stage.EMPTY]
        jobs = [j for p in parts for j in p.jobs.all()]
        total, not_priced = _job_sums(jobs)
        stages = [p.stage for p in parts]
        finished = sum(1 for s in stages if s not in ("queued", "converting", "pricing"))
        stopped, awaiting = stages.count(Batch.Stage.STOPPED), stages.count(Batch.Stage.READY)
        review = sum(1 for s in stages if s in ("review", "failed"))
        reason = ""
        if pr.status == Project.Status.PROCESSING or any(j.recalc_pending for j in jobs):
            state = "working"  # нарх қўйилгандан кейин қайта ҳисоб ҳам — эски сон кўрсатилмайди
        elif not parts:
            state, reason = "empty", _skip_reason([s for p in pr.parts.all() for s in p.sheets])
        else:
            state = ("stopped" if stopped else "awaiting" if awaiting else "review" if review
                     else "warn" if not_priced else "done")
        items.append({"kind": "folder", "name": pr.name, "url": reverse("project", args=[pr.id]), "created": pr.created,
                      "delete_url": reverse("work_delete", args=["folder", pr.id]),
                      "stop_url": reverse("project_stop", args=[pr.id]) if pr.status == Project.Status.PROCESSING else "",
                      "meta": f"{len(parts)} та смета", "reason": reason, "working": state == "working",
                      "progress": f"{finished}/{len(parts)}", "percent": round(100 * finished / len(parts)) if parts else 100,
                      "total": total, "not_priced": not_priced, "review": review, "stopped": stopped, "awaiting": awaiting,
                      "state": state, "pipes": [_STAGE_PIPE.get(p.stage) or _pipe_of_jobs(list(p.jobs.all()), "done")
                                                for p in parts]})
    for b in Batch.objects.filter(owner=user, project__isnull=True).prefetch_related(
            Prefetch("jobs", light_jobs(Job.objects)))[:limit]:
        jobs = list(b.jobs.all())
        total, not_priced = _job_sums(jobs)
        state = _pipe_of_jobs(jobs, "review") if b.status == Batch.Status.DONE else "review" if b.pairs else "empty"
        items.append({"kind": "object", "name": b.name, "url": reverse("batch", args=[b.id]), "created": b.created,
                      "delete_url": reverse("work_delete", args=["object", b.id]),
                      "meta": f"{len(b.pairs)} та жуфтлик", "working": False,
                      "reason": _skip_reason(b.sheets) if state == "empty" else "", "total": total, "not_priced": not_priced,
                      "review": 0 if b.status == Batch.Status.DONE else 1, "state": state, "pipes": [state]})
    for j in light_jobs(Job.objects.filter(owner=user, batch__isnull=True))[:limit]:
        total, not_priced = _job_sums([j]) if j.status == Job.Status.DONE else (0, 0)
        state = ("failed" if j.status == Job.Status.FAILED else "review" if j.status != Job.Status.DONE
                 else "warn" if not_priced else "done")
        items.append({"kind": "smeta", "name": j.local_name, "url": reverse("job", args=[j.id]), "created": j.created,
                      "delete_url": reverse("work_delete", args=["smeta", j.id]),
                      "meta": j.local_sheet, "reason": "", "working": False, "total": total, "not_priced": not_priced,
                      "review": 1 if j.status != Job.Status.DONE else 0, "state": state,
                      "pipes": [state if state in ("warn", "done") else "review"]})
    items.sort(key=lambda x: x["created"], reverse=True)
    for it in items:
        it["state_label"], it["state_color"], it["state_icon"] = WORK_STATES[it["state"]]
    return items[:limit] if limit else items


def _tn_summary(cards) -> dict:
    """Техназор объектлари: умумий шартнома, бажарилган ва қолдиқ (пудратчи, ҚҚС билан), энг янгилари."""
    contract = sum(c["contract"] for c in cards)
    done = sum(c["done"] for c in cards)
    return {"cards": cards[:5], "count": len(cards), "contract": contract, "done": done, "left": contract - done,
            "pct": 100 * done / contract if contract else 0.0}


def _tn_recent(user, limit=8) -> list:
    """Офёрта ва далолатномалар: объектлар охирги иш бўйича (янги далолатнома ёки юклаш), энг янгиси биринчи.
    Суммалар ҳисобланмайди — китобни ўқиш оғир; фақат далолатномалар сони ва охиргиси."""
    from django.db.models import Count, Max, Prefetch as P, Q
    from texnazor.models import Act, SiteObject
    objs = (SiteObject.objects.filter(owner=user)
            .annotate(n_acts=Count("acts"), n_closed=Count("acts", filter=Q(acts__closed=True)), last_act=Max("acts__created"))
            .prefetch_related(P("acts", Act.objects.order_by("-number"), to_attr="acts_desc")))
    rows = []
    for o in objs:
        last = o.acts_desc[0] if o.acts_desc else None
        rows.append({"o": o, "when": o.last_act or o.created, "acts": o.n_acts, "closed": o.n_closed, "last": last,
                     "url": reverse("texnazor:object", args=[o.pk])})
    rows.sort(key=lambda r: r["when"], reverse=True)
    return rows[:limit]


# «Эътибор керак»: ҳолат -> смета­чи нима қилиши керак (иш маълумотидан)
def _todo(it) -> str:
    st = it["state"]
    if st == "warn":
        return f"{it['not_priced']} та қаторга нарх ёзинг"
    if st == "awaiting":
        return f"{it['awaiting']} та қисмни ҳисоблашни бошланг" if it["kind"] == "folder" else "Ҳисоблашни бошланг"
    if st == "stopped":
        return "Тўхтатилган — давом эттиринг"
    if st == "failed":
        return "Очиб бўлмади — сабабини кўринг"
    if it["kind"] == "folder" and it.get("review"):
        return f"{it['review']} та қисмни текширинг"
    return "Ресурсни танланг ёки хатони кўринг"


ATTENTION = ("warn", "awaiting", "stopped", "review", "failed")


def dashboard(request):
    """Дашборд: нима тайёр ва нима смета­чини кутяпти (нархлаш ҳамда техназор), сметалар босқичлари, барча ишлар."""
    from texnazor.services import object_cards
    items = work_items(request.user)
    attention = [dict(it, todo=_todo(it)) for it in items if it["state"] in ATTENTION]
    pipes = [p for it in items for p in it["pipes"]]
    pipeline = [{"key": k, "label": label, "color": color, "hint": hint, "count": pipes.count(k),
                 "pct": round(100 * pipes.count(k) / len(pipes), 1) if pipes else 0}
                for k, label, color, hint in PIPELINE]
    states = [{"key": k, "label": label, "color": color, "icon": ic, "count": sum(1 for it in items if it["state"] == k)}
              for k, (label, color, ic) in WORK_STATES.items()]
    by_state = {s["key"]: s["count"] for s in states}
    from texnazor.models import Act
    tn_recent = _tn_recent(request.user)
    tn_acts = Act.objects.filter(obj__owner=request.user).count()
    return render(request, "jobs/dashboard.html", {
        "nav": "dashboard",
        "items": items,
        "latest": items[:2],
        "pipeline": pipeline,
        "by_state": by_state,
        "not_priced_rows": sum(it["not_priced"] for it in items if it["state"] == "warn"),
        "count_notstarted": pipes.count("pending"),
        "count_check": pipes.count("review"),
        "warn_pct": round(100 * pipes.count("warn") / len(pipes)) if pipes else 0,
        "wait_pct": round(100 * (pipes.count("pending") + pipes.count("review")) / len(pipes)) if pipes else 0,
        "states": states,
        "kinds": {k: sum(1 for it in items if it["kind"] == k) for k in ("folder", "object", "smeta")},
        "count_smetas": len(pipes),
        "count_priced": pipes.count("done") + pipes.count("warn"),
        "count_active": pipes.count("opening") + pipes.count("pricing"),
        "count_pending": pipes.count("pending") + pipes.count("review"),
        "count_done": pipes.count("done"),
        "count_warn": pipes.count("warn"),
        "done_pct": round(100 * pipes.count("done") / len(pipes)) if pipes else 0,
        "attention": attention[:6],
        "attention_total": len(attention),
        "tn": _tn_summary(object_cards(request.user)),
        "tn_recent": tn_recent,
        "tn_acts": tn_acts,
        # qaysi bo'lim ochiq turadi (foydalanuvchi tanlamagan bo'lsa): oxirgi ish qayerda bo'lgan bo'lsa — o'sha
        "pane": "tn" if tn_recent and (not items or tn_recent[0]["when"] > items[0]["created"]) else "smeta",
        "priced_pct": round(100 * (pipes.count("done") + pipes.count("warn")) / len(pipes)) if pipes else 0,
        "any_working": any(it["working"] for it in items),
    })


@require_POST
def project_stop(request, project_id):
    """«Тўхтатиш»: фондаги иш кейинги файлдан олдин тўхтайди (очилганлари сақланади)."""
    project = _get_work(request, Project, id=project_id)
    stop_project(project)
    audit(request, Event.Kind.STOP, work=project, work_kind="folder", detail=project.name)
    if request.headers.get("x-requested-with") == "fetch":
        return JsonResponse({"ok": True})
    return redirect("project", project_id=project.id)


@require_POST
def project_resume(request, project_id):
    """«Давом эттириш»: тўхтатилган қисмлар қайта очилади."""
    project = _get_work(request, Project, id=project_id)
    resume_project(project)
    return redirect("project", project_id=project.id)


@require_POST
def work_delete(request, kind, work_id):
    """Тарихдан ўчириш: папка, объект ёки алоҳида смета — базадан ҳам, дискдан ҳам (қайтариб бўлмайди)."""
    try:
        if kind == "folder":
            work = _get_work(request, Project, id=work_id)
            name = work.name
            delete_project(work, force=True)  # ишлаётган бўлса — аввал тўхтатилади
        elif kind == "object":
            work = _get_work(request, Batch, id=work_id, project__isnull=True)
            name = work.name
            delete_batch(work)
        elif kind == "smeta":
            work = _get_work(request, Job, id=work_id, batch__isnull=True)
            name = work.local_name
            delete_job(work)
        else:
            raise Http404("Нотўғри тур")
    except UploadError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=409)
    audit(request, Event.Kind.DELETE, work_kind=kind, detail=f"{name} ({work_id})")
    if request.headers.get("x-requested-with") == "fetch":
        return JsonResponse({"ok": True})
    return redirect("index")


@require_POST
def work_rename(request, kind, work_id):
    """Папка ёки объектни қайта номлаш (фақат эгаси)."""
    model = {"folder": Project, "object": Batch}.get(kind)
    if model is None:
        raise Http404("Нотўғри тур")
    work = _get_work(request, model, id=work_id)
    name = " ".join(request.POST.get("name", "").split())[:255]
    if name and name != work.name:
        old, work.name = work.name, name
        work.save(update_fields=["name"])
        audit(request, Event.Kind.RENAME, work=work, work_kind=kind, detail=f"{old} → {name}")
    return redirect("project" if kind == "folder" else "batch", work.id)


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
    "ready": ("Ҳисоблашга тайёр", "violet"),
    "stopped": ("Тўхтатилди", "slate"),
    "pricing": ("Нархланяпти", "sky"),
    "recalc": ("Қайта ҳисобланяпти", "sky"),
    "done": ("Тайёр", "emerald"),
    "warn": ("Нархланмаган қатор бор", "rose"),
    "review": ("Текширинг", "amber"),
    "empty": ("Локал топилмади", "slate"),
    "failed": ("Хато", "rose"),
}


@require_POST
def project_create(request):
    """Папка юклаш (JS): ``files`` + ``paths`` (ҳар бирининг папка ичидаги йўли) + ``skipped``."""
    t0 = time.monotonic()
    try:
        check_quota(request.user)  # файллар ўқилишидан олдин
        project = create_project(request.user, request.FILES.getlist("files"), request.POST.getlist("paths"),
                                 request.POST.getlist("skipped"))
    except UploadError as e:
        if request.FILES:
            _upload_timing(request, "folder", t0, error=str(e))
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    audit(request, Event.Kind.UPLOAD, work=project, work_kind="folder", detail=project.name)
    return _went(True, reverse("project", args=[project.id]), _upload_timing(request, "folder", t0, project))


def _part_nodes(node, out):
    if node["part"]:
        out.append(node)
    for c in node["folders"]:
        _part_nodes(c, out)
    return out


def project_detail(request, project_id):
    project = _get_work(request, Project, id=project_id)
    ensure_running(project)
    recalc.resume(Job.objects.filter(batch__project=project))  # server qayta yonganda to'xtab qolgan qayta hisob
    parts = list(project.parts.prefetch_related("jobs").order_by("folder"))
    tree = build_tree(project, parts)
    # нархланадиган лист йўқ қисмлар (масалан «Умумий қиймати») — жадвал охирида
    nodes = sorted(_part_nodes(tree, []), key=lambda n: n["part"]["state"] == "empty")
    auto = nf_auto_of(project.owner_id)
    for n in nodes:  # «Қўлда»: листлари белгиланмаган қисм нархланмайди — «тайёр» эмас
        part = n["part"]
        part["left"] = manual_left(part["batch"]) if not auto and part["state"] == "ready" else 0
        plan = part["planned"]
        part["n_miss"] = sum(1 for x in plan if not x["rk"] and not x["base"])
        part["n_res"] = len({x["rk"] for x in plan if x["rk"]})
    count_mark = sum(1 for n in nodes if n["part"]["left"])
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
        "awaiting": project.status == Project.Status.READY,
        "stopping": project.stop_requested,
        "count_stopped": states.count("stopped"),
        # тўхтатилгандан кейин давом эттириладиган қисмлар (очилаётган ёки нархланаётган эди)
        "count_resume": states.count("stopped") + sum(1 for n in nodes if n["part"]["state"] == "ready"
                                                       and n["part"]["batch"].message == PAUSED),
        "count_ready": states.count("ready") - count_mark,
        "count_mark": count_mark,
        "count_check": states.count("review") + states.count("failed"),
        "count_opening": states.count("queued") + states.count("converting"),
        "count_pricing": states.count("pricing"),
        "count_pairs_ready": sum(len(n["part"]["planned"]) for n in nodes if n["part"]["state"] == "ready"),
        "current": current,
        "finished": finished,
        "progress": round(100 * finished / len(nodes)) if nodes else 100,
        "count_done": states.count("done"),
        "count_warn": states.count("warn"),
        "count_recalc": states.count("recalc"),
        "count_review": states.count("review") + states.count("failed") + count_mark,
        "count_skipped": sum(1 for f in project.files if f["skip"] and is_shown(f["path"])),
        "grand_total": sum(n["part"]["total"] for n in nodes),
        "not_priced_total": sum(n["part"]["not_priced"] for n in nodes if n["part"]["state"] == "warn"),
        "can_download": any(s in ("done", "warn", "review") and n["part"]["jobs"] for s, n in zip(states, nodes)),
        "pct_form": _pct_form(project.owner_id), "nf_auto": auto,
    }
    # нархлаш тугаган: натижалар саҳифаси каби — юқорида жами ва навбатдаги қадамлар, пастда сметалар
    ctx["done_view"] = bool(nodes) and not ctx["working"] and not ctx["awaiting"] and not ctx["count_resume"]
    ctx["count_smeta"] = sum(len(n["part"]["jobs"]) for n in nodes)
    # нархсиз қаторлар ҳамма қисмда (текширилаётганида ҳам) — «Нархсиз ресурслар жадвали» шулардан йиғилади
    ctx["unpriced_rows"] = sum(n["part"]["not_priced"] or 0 for n in nodes)
    ctx["unpriced_parts"] = sum(1 for n in nodes if n["part"]["not_priced"])
    if request.GET.get("partial") == "part":  # битта қисм картасининг боши — танловдан кейин қайта чизилади
        n = next((n for n in nodes if str(n["part"]["batch"].id) == request.GET.get("id")), None)
        if not n:
            raise Http404
        return render(request, "jobs/_project_part_head.html",
                      {**ctx, "n": n, "p": n["part"], "b": n["part"]["batch"], "st": PART_STATES.get(n["part"]["state"])})
    if request.GET.get("partial"):
        return render(request, "jobs/_project_body.html", ctx)
    return render(request, "jobs/project.html", ctx)


def project_viewer(request, project_id, batch_id):
    """Ko'rish oynasi (Excel kabi) uchun bitta qism: listlari va juftliklardagi nomzod resurslar.
    Sahifaga oldindan joylanmaydi — papkada 60 qism bo'lsa, sahifa har yangilanishda yuzlab KB bo'lib ketardi."""
    project = _get_work(request, Project, id=project_id)
    batch = get_object_or_404(Batch, id=batch_id, project=project)
    return JsonResponse({"url": reverse("batch_sheet", args=[batch.id]),
                         "sheets": _sheets_js(batch), "cands": _cands_js(batch)})


def _pct_form(owner_id) -> list:
    """Папкани ҳисоблашдан олдинги сводка фоизлари: охирги танлов (бўлмаса — стандарт)."""
    saved = {**DEFAULT_PERCENTS, **remembered_percents(owner_id)}
    return [{"key": k, "text": PERCENT_LABELS.get(k, k), "pct": saved[k], "default": d} for k, d in DEFAULT_PERCENTS.items()]


@require_POST
def project_price(request, project_id):
    """«Ҳисоблашни бошлаш»: смета­чи файлларни текширди — тайёр қисмлар фонда нархланади.
    Формадаги сводка фоизлари эслаб қолинади: папкадаги ҳамма смета шу фоизлар билан ҳисобланади."""
    project = _get_work(request, Project, id=project_id)
    if any(k.startswith("pct_") for k in request.POST):
        saved = remembered_percents(project.owner_id)
        # бўш катак — 0 (смета саҳифасидагидек)
        data = {f"pct_{k}": (request.POST.get(f"pct_{k}", "").strip() or "0") for k in DEFAULT_PERCENTS
                if f"pct_{k}" in request.POST}
        remember_percents(project.owner_id, {**saved, **parse_percents(data, saved)})
    skip = []
    if not nf_auto_of(project.owner_id):  # «Қўлда»: смета­чи листларини белгиламаган қисм нархланмайди
        skip = [b.id for b in project.parts.filter(stage=Batch.Stage.READY) if manual_left(b)]
    n = start_pricing(project, exclude=skip)
    if skip:
        messages.warning(request, f"{len(skip)} та қисмда листлар ҳали белгиланмаган — улар нархланмади. "
                                  "Қисмни очиб, ҳар бир листни белгиланг ва қайта «Бошлаш»ни босинг.")
    elif not n:
        messages.warning(request, "Нархлашга тайёр қисм йўқ.")
    return redirect("project", project_id=project.id)


@require_http_methods(["GET", "POST"])
def project_unpriced(request, project_id):
    """Папка бўйича бир хил нархланмаганлар. GET — гуруҳлар (ёки ``?pending=1`` — нечта смета ҳисобланяпти);
    POST JSON ``{"items": [{"price": "4 600 000", "rows": {job_id: [қатор, …]}}], "undo": false}``."""
    project = _get_work(request, Project, id=project_id)
    if request.method == "GET":
        if request.GET.get("pending"):
            recalc.resume(Job.objects.filter(batch__project=project))
            return JsonResponse({"pending": bulk_price.pending(project)})
        return JsonResponse({"groups": bulk_price.groups(project), "pending": bulk_price.pending(project)})
    if project.status == Project.Status.PROCESSING:
        return JsonResponse({"ok": False, "error": "Папка ҳали ишланяпти — тугагач нарх қўйинг"}, status=400)
    try:
        data = json.loads(request.body or b"{}")
        items = []
        for it in data["items"]:
            price = _as_price(it["price"])
            if price is None or price < 0:
                return JsonResponse({"ok": False, "error": f"нарх рақам бўлиши керак: «{it['price']}»"}, status=400)
            rows = {str(uuid.UUID(str(j))): [int(r) for r in rs] for j, rs in dict(it["rows"]).items()}
            items.append({"price": price, "rows": rows})
    except (ValueError, TypeError, KeyError, AttributeError):
        return JsonResponse({"ok": False, "error": "нотўғри сўров"}, status=400)
    if not items:
        return JsonResponse({"ok": False, "error": "нарх ёзилмаган"}, status=400)
    return JsonResponse({"ok": True, **bulk_price.apply(project, items, undo=bool(data.get("undo")))})


def project_download(request, project_id):
    project = _get_work(request, Project, id=project_id)
    try:
        path = build_project_zip(project)
    except UploadError as e:
        raise Http404(str(e))
    _audit_download(request, project, "folder", path.name)
    return FileResponse(open(path, "rb"), as_attachment=True, filename=path.name)


# ===================== tizim haqida (landing) =====================
LANDING_FEATURES = [
    ("folder-open", "Бутун объект бир йўла",
     "ZIP, RAR архив ёки папкани юкланг. Ичидаги локал сметалар ва нарх файллари топилади, ҳар бир қисм ўз нархлари билан ҳисобланади."),
    ("layers", "Листларни ўзи танийди",
     "Қайси лист локал смета, қайсиси нарх рўйхати ёки йиғма смета эканини мазмунидан аниқлайди. Хато бўлса, турини бир босишда ўзгартирасиз."),
    ("calculator", "Йиғма смета тайёр",
     "Иш ҳақи, машина ва механизмлар, материаллар, транспорт, устама харажатлар, суғурта ва ҚҚС ҳисобланади. Фоизларни ўзингиз ўзгартира оласиз."),
    ("list", "Ҳар бир нарх изоҳли",
     "Натижа файлидаги «LOG» листида қайси қаторга қайси нарх ва қаердан олингани, қўлда нима ўзгартирилгани ёзиб борилади."),
]
LANDING_STEPS = [
    ("Юкланг", "Локал смета ва ресурс нархлари файлини (.xls, .xlsx) ёки бутун объект папкасини юкланг."),
    ("Текширинг", "Тизим листлар ва устунларни аниқлаб кўрсатади. Бирортаси нотўғри бўлса, шу ернинг ўзида тузатасиз."),
    ("Нархланг", "Ҳар бир ресурс нарх файли билан солиштирилади, нарх ва сумма ёзилади, йиғма смета ҳисобланади."),
    ("Юклаб олинг", "Тайёр Excel файл: нархлар, рангли ҳолатлар, йиғма смета ва «LOG» листи."),
]
LANDING_SECURITY = [
    ("database", "Файллар серверда қолади", "Сметалар фақат шу серверда ишланади ва сақланади. Ташқи хизматларга ҳам, сунъий интеллектга ҳам юборилмайди."),
    ("file", "Асл файл ўзгармайди", "Иш юкланган нусха устида бажарилади. Натижа алоҳида «…_narxlandi.xlsx» файлда чиқади."),
    ("users", "Ҳар ким ўз ишини кўради", "Ҳар бир фойдаланувчи ўз ҳисоби билан киради. Бошқа одамнинг сметаси ёки объекти унга кўринмайди."),
    ("lock", "Фақат Excel", "Қабул қилинадиган файллар: .xls, .xlsx, .xlsm ва уларнинг архиви. Битта файл 30 МБ гача."),
]
LANDING_FAQ = [
    ("Ресурс нархлари файли (RES) нима?",
     "Объект учун тузилган ресурслар рўйхати: ҳар бир материал, машина-соат ва иш кучининг номи, ўлчов бирлиги ва нархи. "
     "Тизим локал сметадаги ресурсларни шу рўйхат билан солиштириб, нархини олади."),
    ("Нарх топилмаса нима бўлади?",
     "Қатор қизил «ТОПИЛМАДИ» деб белгиланади ва тизим нархни тахмин қилмайди. Ёнида номи энг яқин учта ресурс ўхшашлик фоизи билан "
     "кўрсатилади: бирини танлайсиз ёки нархни ўзингиз ёзасиз."),
    ("Техник назорат бўлими нима қилади?",
     "Шартнома офертасини бир марта юклайсиз. Кейин ҳар бир далолатномада фақат бажарилган иш ҳажмини белгилайсиз: ресурслар меъёр "
     "бўйича ўзи ҳисобланади, бажарилган сумма ва қолдиқ кўриниб туради."),
    ("Бирлик бошқача ёзилган бўлса-чи?",
     "Бирлик ҳам ном билан бирга солиштирилади. «м3» ва «т» каби бошқа бирликдаги ресурсга нарх ёзилмайди, у текшириш учун белгиланади."),
]


@login_not_required
def landing(request):
    """Тизим ҳақида: нима қилади, қандай ишлайди, тезлик, хавфсизлик."""
    return render(request, "jobs/landing.html",
                  {"features": LANDING_FEATURES, "steps": LANDING_STEPS, "security": LANDING_SECURITY,
                   "faq": LANDING_FAQ})


# ===================== obyekt (bir/ko'p fayl) =====================
ROLE_LABELS = {"local": "Локал", "resource": "Ресурс", "unknown": "Ўтказиш"}
# Устунлар вазифаси (smetachi qo'lda ko'rsatishi mumkin): калит, номи, қайси лист турига тегишли, нима эканлиги
COLUMN_ROLES = [
    ("name", "Ном", ("local", "resource"),
     "Иш ёки ресурс номи (матн). Масалан: «Щебень фракции 20-40 мм». Шу ном бўйича RES'дан нарх қидирилади."),
    ("unit", "Бирлик", ("local", "resource"),
     "Ўлчов бирлиги: м3, т, км, чел.-ч, маш.-ч. Ном билан бирга солиштирилади — бирлиги бошқа бўлса, нарх олинмайди."),
    ("qty_unit", "Миқдор (бирликка)", ("local",),
     "1 бирлик иш учун сарф — норма. Масалан: 1000 м3 грунт қазишга 21,4 маш.-ч экскаватор. Бу устун бўлмаса — «йўқ»."),
    ("qty_total", "Миқдор (жами)", ("local",),
     "Бутун иш ҳажмига сарф = норма × иш ҳажми (21,4 × 0,84 = 17,98 маш.-ч). Сумма шундан ҳисобланади: жами × нарх."),
    ("price", "Нарх", ("resource",),
     "RES'даги 1 бирлик ресурснинг нархи (сўм). Мос келган ресурснинг шу нархи локалга ёзилади."),
]
ROW_HINTS = {
    "start": "Биринчи иш ёки ресурс ёзилган қатор — сарлавҳа ва «1 2 3 4…» рақамлаш қаторидан кейингиси.",
    "end": "Охирги ресурс қатори. Ундан пасти («Итого», имзолар) нархланмайди.",
}


def _column_view(s, manual=None):
    """«Листлар тури» жадвали учун: тизим (ёки смета­чи) қайси устунни нима деб олди ва таҳрир майдонлари."""
    found = s.get("columns") or {}
    letter = lambda c: get_column_letter(int(c)) if c else ""
    parts = []
    for key, label, roles, hint in COLUMN_ROLES:
        if s["role"] not in roles or (key == "qty_unit" and not found.get(key)):
            continue
        parts.append({"key": key, "label": label, "hint": hint, "value": letter(found.get(key))})
    options = [(letter(c), t) for c, t in sorted((s.get("headers") or {}).items(), key=lambda x: int(x[0]))]
    fields = [{"key": key, "label": label, "roles": " ".join(roles), "hint": hint, "value": letter(found.get(key))}
              for key, label, roles, hint in COLUMN_ROLES]
    # қаторлар катагида — фақат смета­чи ёзгани (тизим топгани placeholder: акс ҳолда юборилганда «қўлда» бўлиб қотиб қолади)
    manual = manual or {}
    return {"parts": parts, "start": found.get("start"), "end": found.get("end"), "options": options, "fields": fields,
            "manual_start": manual.get("start"), "manual_end": manual.get("end"), "row_hints": ROW_HINTS,
            "missing": any(not p["value"] for p in parts)}


def _sheet_verdict(s, cv, by_key, chosen=False) -> dict:
    """Лист бир қарашда: ранг (tone), қисқа хулоса ва — керак бўлса — нима қилиш кераклиги.
    ``chosen`` — турини смета­чи ўзи танлаган (тизим тушунмади деб сўралмайди)."""
    if s.get("duplicate_of"):
        twin = by_key.get(s["duplicate_of"], {})
        return {"tone": "off", "text": "Нусха", "hint": f'«{twin.get("sheet", "")}» билан бир хил — ўтказилди'}
    if s["role"] == "unknown" and not s.get("kind") and not chosen:
        # свод / акт / бўш эмас — тизим жадвални умуман тополмади: йиғиқ рўйхатга эмас, кўринадиган жойга
        return {"tone": "ask", "text": "Тизим тушунмади", "hint": "Жадвал топилмади — «Белгилаш»ни босиб кўрсатинг"}
    if s["role"] == "unknown":
        kind = KIND_LABELS.get(s.get("kind", ""), "")
        hint = "Қайта нархлаш керак бўлса — турини «Локал» қилинг" if s.get("kind") == "priced" else (
            "" if kind else "Локал ёки ресурс бўлса — турини ўзгартиринг")
        return {"tone": "off", "text": f"Ўтказилди — {kind}" if kind else "Ўтказилди", "hint": hint}
    if cv["missing"]:
        return {"tone": "warn", "text": "Устун топилмади", "hint": "«тузатиш»ни босиб, қизил устунни кўрсатинг"}
    if not s.get("rows"):
        return {"tone": "warn", "text": "Маълумот топилмади" if s["role"] == "local" else "Нарх топилмади",
                "hint": "«тузатиш»да қаторлар оралиғи ва устунларни текширинг"}
    if s["role"] == "local":
        return {"tone": "ok", "text": "Нархланади", "hint": f'{s.get("rows") or 0} та қатор'}
    return {"tone": "res", "text": "Нарх манбаи", "hint": f'{s.get("rows") or 0} та нарх'}


def _by_kind(sheets) -> list:
    """Ўтказилган листлар тури бўйича: [(«сводка / умумий ҳисоб», [листлар]), …] — ҳар бирига ёрлиқ қайта ёзилмайди."""
    groups: dict = {}
    for s in sheets:
        groups.setdefault(KIND_LABELS.get(s.get("kind"), "бошқа"), []).append(s)
    return list(groups.items())


def _sheet_groups(batch) -> list:
    """«Листлар тури»: файл бўйича гуруҳ, ҳар бир лист — (форма рақами, лист, устунлар, хулоса)."""
    by_key = {s["key"]: s for s in batch.sheets}
    groups = []
    for fi, f in enumerate(batch.files):
        rows = []
        for i, s in enumerate(batch.sheets):
            if s["file"] == fi:
                cv = _column_view(s, (batch.columns or {}).get(s["key"]))
                rows.append((i, s, cv, _sheet_verdict(s, cv, by_key, s["key"] in (batch.roles or {}))))
        if rows:
            groups.append({"name": f["name"], "rows": rows})
    return groups


def _parse_columns(post, i) -> dict:
    """POST'дан битта листнинг қўлда устунлари: {"name": 3, …, "start": 22, "end": 850}; бўш — автомат."""
    if post.get(f"cols_{i}_on") != "1":
        return {}
    out = {}
    for key, *_rest in COLUMN_ROLES:
        v = (post.get(f"cols_{i}_{key}") or "").strip().upper()
        if v:
            try:
                out[key] = column_index_from_string(v)
            except ValueError:
                pass
    for key in ("start", "end"):
        v = (post.get(f"cols_{i}_{key}") or "").strip()
        if v.isdigit() and int(v) > 0:
            out[key] = int(v)
    if out.get("start") and out.get("end") and out["end"] < out["start"]:
        out.pop("end")
    return out


def _batch_tags(batch):
    """Боғланишни кўрсатиш учун белгилар: ҳар бир локал «Л1, Л2…» (жуфтлик тартибида), ресурс «Р1, Р2…»."""
    loc_tag = {p["local"]: f"Л{n + 1}" for n, p in enumerate(batch.pairs)}
    resources = [s for s in batch.sheets if s["role"] == "resource" and not s.get("duplicate_of")]
    res_tag = {s["key"]: f"Р{i + 1}" for i, s in enumerate(resources)}
    return loc_tag, res_tag, resources


def _pair_options(p, resources, res_tag, sheets):
    """Танлаш рўйхати: ҳар бир ресурс листи мослиги билан, энг мос биринчи (эски объектларда — рўйхат тартибида)."""
    loc = sheets[p["local"]]
    by_key = {o["res"]: o for o in p.get("options") or []}
    out = []
    for s in resources:
        o = by_key.get(s["key"], {})
        out.append({"key": s["key"], "tag": res_tag[s["key"]], "sheet": s["sheet"], "file_name": s["file_name"],
                    "title": sheet_title(s), "rows": s.get("rows", 0), "rows": s.get("rows", 0), "number": s.get("number", ""), "hidden": s.get("hidden"),
                    "overlap": o.get("overlap"), "score": o.get("score", 0), "same_file": s["file"] == loc["file"],
                    "struct": bool(o.get("struct"))})
    # локалнинг ўз файлидаги ресурс листлари — мослиги паст бўлса ҳам биринчи (одатда тўғриси шу), кейин мослик бўйича
    out.sort(key=lambda o: (not o["same_file"], -(o["score"] or 0)))
    best = max(out, key=lambda o: o["overlap"] or 0, default=None)
    if best and best["overlap"]:
        best["best"] = True
    return out


def sheet_title(s) -> str:
    """Листнинг ўқиладиган номи: «LRV», «Лист5» ўрнига қисм номи ёки файл номи."""
    return readable(s.get("part")) or file_title(s["file_name"])


def _cands_js(batch):
    """Модал учун: ҳар бир жуфтликнинг номзод ресурслари (ўз файлидагилари биринчи, кейин мослик бўйича)."""
    sheets = {s["key"]: s for s in batch.sheets}
    _loc_tag, res_tag, resources = _batch_tags(batch)
    return [[{"k": o["key"], "ov": o["overlap"], "same": o["same_file"]} for o in _pair_options(p, resources, res_tag, sheets)]
            for p in batch.pairs]


def _batch_tree(batch):
    """Стройка → Объект → жуфтликлар (кўрсатиш учун)."""
    sheets = {s["key"]: s for s in batch.sheets}
    loc_tag, res_tag, resources = _batch_tags(batch)
    tree = {}
    for n, p in enumerate(batch.pairs):
        loc = sheets[p["local"]]
        res = sheets.get(p["res"])
        st = tree.setdefault(loc.get("stroyka") or "—", {})
        st.setdefault(loc.get("obj") or "—", []).append(dict(
            p, n=n, loc=loc, res_sheet=res, tag=loc_tag[p["local"]], res_tag=res_tag.get(p["res"], ""),
            title=readable(p.get("label")) or sheet_title(loc),
            same_file=bool(res) and res["file"] == loc["file"],
            choices=_pair_options(p, resources, res_tag, sheets)))
    return [{"stroyka": s, "objects": [{"obj": o, "pairs": ps} for o, ps in objs.items()]} for s, objs in tree.items()]


PREVIEW_ROWS = 80


def batch_sheet(request, batch_id):
    """Объект листи Excel кўринишида (бошидан ``n`` қатор): ``k`` — лист калити «файл:лист»."""
    batch = _get_work(request, Batch, id=batch_id)
    key = request.GET.get("k", "")
    info = batch.sheet(key)
    if info is None:
        raise Http404("Лист топилмади")
    f = batch.files[info["file"]]
    path = batch.abs(f["path"])
    if not f.get("path") or not path.is_file():
        raise Http404("Файл ҳали очилмаган")
    try:
        n = max(20, min(int(request.GET.get("n") or PREVIEW_ROWS), xlview.MAX_ROWS))
    except ValueError:
        n = PREVIEW_ROWS
    try:
        data = xlview.cached(batch.dir / "preview", f"{key}|{n}", path,
                             lambda: xlview.render(path, info["sheet"], max_rows=n))
    except Exception as e:
        return JsonResponse({"error": f"{type(e).__name__}: {e}"}, status=500)
    return JsonResponse({**data, "file": f["name"], "role": info["role"], "limit": n})


def batch_pair_preview(request, batch_id):
    """Жуфтлик картаси учун қисқа намуна: ``k`` — локал листи, ``r`` — RES листи ёки ``BASE_RES`` (ресурс базаси)."""
    from resbase.services import period_label, price_map

    from . import pair_preview
    batch = _get_work(request, Batch, id=batch_id)
    k, r = request.GET.get("k", ""), request.GET.get("r", "")
    loc = batch.sheet(k)
    if loc is None or loc["role"] != "local":
        raise Http404("Лист топилмади")
    base = None
    if r == BASE_RES:
        period = base_period_of(batch.owner_id)
        base, r = price_map(period), ""
        tag = f"{period_label(period)}|{len(base)}"
    else:
        res = batch.sheet(r) if r else None
        if r and (res is None or res["role"] != "resource"):
            raise Http404("Лист топилмади")
        tag = ""
    try:
        return JsonResponse(pair_preview.cached(batch, k, r, base, tag))
    except Exception as e:
        return JsonResponse({"error": f"{type(e).__name__}: {e}"}, status=500)


def _sheets_js(batch):
    """Модал учун: ҳар бир лист (файл тартибида) — тури ва белгиси (Л1/Р2), файл ичида листдан листга ўтиш учун."""
    loc_tag, res_tag, _resources = _batch_tags(batch)
    return [{"k": s["key"], "file": s["file"], "file_name": s["file_name"], "sheet": s["sheet"], "role": s["role"],
             "title": sheet_title(s),
             "tag": loc_tag.get(s["key"]) or res_tag.get(s["key"], ""), "dup": bool(s.get("duplicate_of")),
             "kind": KIND_LABELS.get(s.get("kind", ""), ""), "hidden": bool(s.get("hidden"))} for s in batch.sheets]


def manual_left(batch) -> int:
    """«Қўлда» усули: смета­чи ҳали ўзи белгиламаган листлар сони (нусхалар ҳисобланмайди)."""
    roles = batch.roles or {}
    return sum(1 for s in batch.sheets if not s.get("duplicate_of") and s["key"] not in roles)


def _skipped_files(batch) -> list:
    """Папка қисми: шу папкадаги олинмаган (керак эмас деб топилган) файллар — номи ва сабаби."""
    if not batch.project_id:
        return []
    from pathlib import PurePosixPath
    folder = batch.folder or ""
    out = []
    for e in batch.project.files or []:
        if not e.get("skip") or not is_shown(e["path"]):
            continue
        parent = str(PurePosixPath(e["path"]).parent)
        if ("" if parent == "." else parent) == folder:
            out.append({"name": PurePosixPath(e["path"]).name, "reason": e["skip"]})
    return out


def _picker_ctx(batch, embed=False) -> dict:
    """«Файллар → жуфтлаш → усул → Бошлаш» блоки (юклаш саҳифаси ва объект саҳифаси учун бир хил):
    ҳар бир файлнинг ҳамма листлари тури билан, ҳар бир локал учун ресурс танлови."""
    sheets = batch.sheets
    by_key = {s["key"]: s for s in sheets}
    loc_tag, res_tag, resources = _batch_tags(batch)
    auto = nf_auto_of(batch.owner_id)
    files = []
    for fi, f in enumerate(batch.files):
        rows = []
        for s in sheets:
            if s["file"] != fi:
                continue
            cv = _column_view(s, (batch.columns or {}).get(s["key"]))
            picked = s["key"] in (batch.roles or {})
            v = _sheet_verdict(s, cv, by_key, picked)
            sub = " · ".join(x for x in ((f'№ {s["number"]}' if s.get("number") else ""),
                                         readable(s.get("part") or s.get("obj") or "")) if x)
            rows.append({"key": s["key"], "sheet": s["sheet"], "role": s["role"], "dup": bool(s.get("duplicate_of")),
                         # «Қўлда»: тизим тахмини фақат маслаҳат — тугма смета­чи ўзи босгунча ёнмайди
                         "show": s["role"] if (auto or picked or s.get("duplicate_of")) else "none", "picked": picked,
                         "hidden": bool(s.get("hidden")), "tag": loc_tag.get(s["key"]) or res_tag.get(s["key"], ""),
                         "sub": sub, "verdict": v, "reason": s.get("reason", ""),
                         "kind": KIND_LABELS.get(s.get("kind", ""), "")})
        files.append({"i": fi, "name": f["name"], "sheets": rows,
                      "locals": sum(1 for r in rows if r["role"] == "local" and not r["dup"]),
                      "resources": sum(1 for r in rows if r["role"] == "resource" and not r["dup"])})
    allow_base = base_only(batch.owner_id)
    pairs = []
    for n, p in enumerate(batch.pairs):
        loc = by_key.get(p["local"])
        if not loc:
            continue
        pairs.append({"n": n, "local": p["local"], "tag": loc_tag.get(p["local"], ""),
                      "title": readable(p.get("label")) or sheet_title(loc), "label": p.get("label", ""),
                      "sheet": loc["sheet"], "file_name": loc["file_name"], "rows": loc.get("rows", 0),
                      "res": p.get("res") or (BASE_RES if p.get("base") and allow_base else ""),
                      "include": bool(p.get("include")),
                      "options": _pair_options(p, resources, res_tag, by_key)})
    ready = sum(1 for p in batch.pairs if pair_ready(p))
    # битта жадвал: ҳар бир лист — бир қатор (тури + локал бўлса қайси ресурс билан). Ўтказилганлар — охирида йиғиқ
    pair_by = {p["local"]: p for p in pairs}
    rows, skipped, unclear = [], [], []
    for f in files:
        for r in f["sheets"]:
            pr = pair_by.get(r["key"]) if r["show"] == "local" else None
            r = {**r, "file_name": f["name"], "pair": pr, "title": pr["title"] if pr else r["sheet"],
                 "fi": f["i"], "undo": can_undo_file(batch, f["i"])}
            r["q"] = f'{r["title"]} {r["sheet"]} {f["name"]} {r["sub"]}'.lower()
            if r["show"] == "unknown" and not r["dup"] and r["verdict"]["tone"] == "ask":
                unclear.append(r)  # тизим тушунмаган — йиғиқ «керак эмас» ичига эмас, алоҳида кўринадиган гуруҳ
            else:
                (skipped if r["dup"] or r["show"] == "unknown" else rows).append(r)
    rank = {"none": 0, "local": 1, "resource": 2}
    rows.sort(key=lambda r: (rank.get(r["show"], 3), r["pair"]["n"] if r["pair"] else 0))
    count = {k: sum(1 for r in rows if r["show"] == k) for k in ("local", "resource", "none")}
    # ёнма-ён кўриниш: чапда локал, ўнгда унинг ресурси (ўз номи ва файли билан)
    by_row = {r["key"]: r for r in rows}
    used = {r["pair"]["res"] for r in rows if r["pair"] and r["pair"]["include"]}
    for r in rows:
        if r["pair"]:
            rs = by_key.get(r["pair"]["res"]) if r["pair"]["res"] != BASE_RES else None
            rr = by_row.get(rs["key"]) if rs else None
            r["res"] = {"key": rs["key"], "title": sheet_title(rs), "sheet": rs["sheet"], "file_name": rs["file_name"],
                        "tag": res_tag.get(rs["key"], ""), "show": rr["show"] if rr else rs["role"],
                        "fi": rs["file"], "undo": can_undo_file(batch, rs["file"]),
                        "same_file": rs["file"] == by_key[r["key"]]["file"]} if rs else None
    pair_rows = [r for r in rows if r["pair"]]
    # белгиланмаганлар («Қўлда»), жуфтсиз локал ва ҳеч қайси локалга танланмаган ресурслар — алоҳида
    loose = [r for r in rows if not r["pair"] and not (r["show"] == "resource" and r["key"] in used)]
    return {"batch": batch, "pk_files": files, "pk_pairs": pairs, "pk_ready": ready,
            "pk_rows": rows, "pk_skipped": skipped, "pk_unclear": unclear,
            "pk_back": reverse("project", args=[batch.project_id]) if embed and batch.project_id else "", "pk_count": count, "pk_skipped_files": _skipped_files(batch),
            "pk_pair_rows": pair_rows, "pk_loose": loose,
            "pk_unmarked": [r for r in loose if r["show"] == "none"],
            "pk_idle": [r for r in loose if r["show"] != "none"],
            "pk_resources": len(resources), "pk_base": allow_base, "base_res": BASE_RES,
            "pk_unpriced": unpriced_res(batch), "sheets_js": _sheets_js(batch),
            "nf_auto": auto, "pct_form": _pct_form(batch.owner_id), "pk_left": 0 if auto else manual_left(batch),
            "pk_again": batch.jobs.exists(), "pk_embed": embed, "pk_roles": list(ROLE_LABELS.items()),
            "sheets_id": f"pk-sheets-{batch.id}"}


def batch_detail(request, batch_id):
    batch = _get_work(request, Batch, id=batch_id)
    if request.GET.get("partial") == "picker":
        return render(request, "jobs/_picker.html", _picker_ctx(batch, embed=request.GET.get("embed") == "1"))
    sheets = batch.sheets
    used_res = {p["res"] for p in batch.pairs if p.get("res")}
    jobs = list(batch.jobs.order_by("batch_order"))
    by_key = {s["key"]: s for s in sheets}
    # ишлатилмаган ресурсни бириктириш учун: ҳар бир локал (белгиси, номи, файли)
    loc_titles = [{"n": n, "tag": f"Л{n + 1}", "file": by_key[p["local"]]["file"], "has_res": bool(p.get("res")),
                   "title": readable(p.get("label")) or sheet_title(by_key[p["local"]])} for n, p in enumerate(batch.pairs)]
    ctx = {
        "batch": batch,
        "crumbs": crumbs_for(batch=batch),
        "tree": _batch_tree(batch),
        "resources": [s for s in sheets if s["role"] == "resource" and not s.get("duplicate_of")],
        # «ресурс базасидан» режимида RES листлари атайлаб ишлатилмайди — «ишлатилмаган» деб кўрсатилмайди
        "unused": [] if base_only(batch.owner_id) else
                  [dict(s, title=sheet_title(s), same_files=[t for t in loc_titles if t["file"] == s["file"]])
                   for s in sheets if s["role"] == "resource" and not s.get("duplicate_of") and s["key"] not in used_res],
        "base_ignored": [s for s in sheets if s["role"] == "resource" and not s.get("duplicate_of")]
                        if base_only(batch.owner_id) else [],
        "locals": loc_titles,
        "duplicates": [s for s in sheets if s.get("duplicate_of")],
        "unknown": [s for s in sheets if s["role"] == "unknown"],
        "unknown_groups": _by_kind(s for s in sheets if s["role"] == "unknown"),
        "sheets_by_file": [(i, f, [s for s in sheets if s["file"] == i]) for i, f in enumerate(batch.files)],
        "sheet_groups": _sheet_groups(batch),
        "row_hints": ROW_HINTS,
        "role_counts": {r: sum(1 for s in sheets if s["role"] == r and not s.get("duplicate_of")) for r in ROLE_LABELS},
        "kind_labels": KIND_LABELS,
        "sheets_js": _sheets_js(batch),
        "role_labels": ROLE_LABELS.items(),
        "ready": sum(1 for p in batch.pairs if pair_ready(p)),
        **_base_ctx(base_period_of(batch.owner_id)), "base_res": BASE_RES,
        **({} if base_only(batch.owner_id) else {"base_label": ""}),  # «Ресурс базаси» варианти — фақат «фақат база» режимида
        "jobs": jobs,
        **_picker_ctx(batch),
        "grand_total": sum(j.result.get("totals", {}).get("s_nds") or 0 for j in jobs
                           if isinstance(j.result.get("totals", {}).get("s_nds"), (int, float))),
        "not_priced_total": sum(j.result.get("stats", {}).get("NOT_PRICED", 0) for j in jobs),
        # натижалар: аввал тўғирлаш кераклари (нархланмаган қатор ёки хато), кейин тайёрлари
        "jobs_fix": [j for j in jobs if j.status != "done" or j.result.get("stats", {}).get("NOT_PRICED")],
        "jobs_ok": [j for j in jobs if j.status == "done" and not j.result.get("stats", {}).get("NOT_PRICED")],
        # танланган RES'да нарх йўқ — нархлашдан олдин огоҳлантириш
        "unpriced": [readable(p.get("label")) or p["local"] for p in batch.pairs
                     if p.get("include") and p.get("res") in unpriced_res(batch)],
        # нархсиз RES файллари — шу ердан юклаб олиб, тузатилганини қайта юклаш учун
        "unpriced_files": [{"i": i, "name": batch.files[i]["name"]} for i in sorted(
            {by_key[p["res"]]["file"] for p in batch.pairs
             if p.get("include") and p.get("res") in unpriced_res(batch) and p["res"] in by_key})],
    }
    return render(request, "jobs/batch.html", ctx)


@require_POST
def batch_pick(request, batch_id):
    """Танлов дарҳол сақланади (тугма йўқ): ``role`` — листнинг тури (Локал / Ресурс / Ўтказиш), кейин қайта
    жуфтланади; ``pair`` — локал учун қайси ресурс ва нархлансинми. Смета­чи танлови кейин ҳам эслаб қолинади."""
    batch = _get_work(request, Batch, id=batch_id)
    if batch.owner_id != request.user.pk:
        return JsonResponse({"ok": False, "error": "Бу иш сизники эмас — ўзгартириб бўлмайди"}, status=403)
    if batch.project_id and batch.stage in (Batch.Stage.QUEUED, Batch.Stage.CONVERTING, Batch.Stage.PRICING):
        return JsonResponse({"ok": False, "error": "Бу қисм ҳозир ишланяпти — тугагач ўзгартиринг"}, status=409)
    what = request.POST.get("what")
    remembered = 0
    if what == "role":
        key, role = request.POST.get("key", ""), request.POST.get("role", "")
        if role not in ROLE_LABELS or batch.sheet(key) is None:
            return JsonResponse({"ok": False, "error": "нотўғри лист"}, status=400)
        roles = dict(batch.roles or {})
        roles[key] = role
        analyze_batch(batch, roles)
        remembered = remember_sheets(batch, [key])
        if not batch.jobs.exists():
            batch.status = Batch.Status.REVIEW
        batch.save()
        if batch.project_id and not batch.jobs.exists():
            batch.stage, batch.message = ready_stage(batch)
            batch.save(update_fields=["stage", "message"])
            settle(batch.project)
    elif what == "pair":
        try:
            n = int(request.POST.get("n", ""))
        except ValueError:
            n = -1
        if not 0 <= n < len(batch.pairs):
            return JsonResponse({"ok": False, "error": "нотўғри жуфтлик"}, status=400)
        d = {"include": request.POST.get("include") == "1", "picked": True}
        if "res" in request.POST:
            d["res"] = request.POST["res"]
        if request.POST.get("label", "").strip():
            d["label"] = request.POST["label"]
        apply_decisions(batch, {n: d})
        batch.save(update_fields=["pairs"])
        if batch.project_id and not batch.jobs.exists():  # папка қисми: ҳолати (тайёр / текширинг) янгиланади
            batch.stage, batch.message = ready_stage(batch)
            batch.save(update_fields=["stage", "message"])
            settle(batch.project)
    else:
        return JsonResponse({"ok": False, "error": "номаълум амал"}, status=400)
    state = PART_STATES.get(batch.stage) if batch.project_id else None
    return JsonResponse({"ok": True, "ready": sum(1 for p in batch.pairs if pair_ready(p)),
                         "remembered": MEMO_SAVED if what == "role" and remembered else "",
                         "stage": batch.stage, "state": state[0] if state else "", "tone": state[1] if state else "",
                         "left": 0 if nf_auto_of(batch.owner_id) else manual_left(batch)})


_STEP_RE = re.compile(r"^(\d+)/(\d+)\s*(.*)$")


def batch_progress(request, batch_id):
    """Нархлаш жараёни (Бошлаш босилгандан кейинги аниқ прогресс): «2/5 смета нархланяпти: …»."""
    batch = _get_work(request, Batch, id=batch_id)
    m = _STEP_RE.match(batch.step or "")
    return JsonResponse({"status": batch.status, "at": int(m.group(1)) if m else 0, "total": int(m.group(2)) if m else 0,
                         "text": m.group(3) if m else "", "done": batch.jobs.filter(status=Job.Status.DONE).count()})


@require_POST
def batch_replan(request, batch_id):
    batch = _get_work(request, Batch, id=batch_id)
    roles, columns = {}, {}
    for i, s in enumerate(batch.sheets):
        role = request.POST.get(f"role_{i}")
        if role in ROLE_LABELS and role != s["role"]:
            roles[s["key"]] = role
        elif s["key"] in (batch.roles or {}) and role == s["role"]:
            roles[s["key"]] = role  # аввал қўлда белгиланган — сақланади
        cols = _parse_columns(request.POST, i)
        if cols:
            columns[s["key"]] = cols
    if batch.project_id and any(not f.get("path") for f in batch.files):
        from .folders import convert_files
        try:
            convert_files(batch, only_missing=True)  # аввал очилмай қолган файллар (таҳлил узилган бўлса)
        except Exception as e:  # noqa: BLE001 — файл барибир очилмаса: қолганлари билан давом, сабаби саҳифада
            messages.error(request, f"Баъзи файллар очилмади: {e}")
    before = (dict(batch.roles or {}), dict(batch.columns or {}))
    analyze_batch(batch, roles, columns)
    # шу сафар ўзгартирилган листлар — эслаб қолинади (шундай тузилмали лист кейин келса, ўзи қўлланади)
    changed = [k for k in set(roles) | set(columns)
               if roles.get(k) != before[0].get(k) or columns.get(k) != before[1].get(k)]
    if remember_sheets(batch, changed):
        messages.success(request, MEMO_SAVED)
    batch.status = Batch.Status.REVIEW
    batch.save()
    if batch.project_id and not batch.jobs.exists():
        batch.stage, batch.message = ready_stage(batch)
        batch.save(update_fields=["stage", "message"])
        settle(batch.project)
    return redirect("batch", batch_id=batch.id)


# Белгилаш саҳифаси (офёртадагидек): қайси лист тури учун қайси устунлар сўралади — тартиби шу
MARK_COLS = {
    "local": [("name", True), ("unit", True), ("qty_unit", False), ("qty_total", True)],
    "resource": [("name", True), ("unit", True), ("price", True)],
}
MARK_ROWS = 300  # саҳифа аввал шунча қатор кўрсатади; «Яна» — кўпроқ


def _mark_state(s, manual) -> dict:
    """Белгилаш саҳифаси бошланғич ҳолати: смета­чи аввал кўрсатгани, бўлмаса тизим топгани (ҳарф билан)."""
    found = dict(s.get("columns") or {})
    found.update(manual or {})
    letter = lambda c: get_column_letter(int(c)) if c else ""
    return {"role": s["role"] if s["role"] in ("local", "resource") else "",
            "cols": {k: letter(found.get(k)) for k, *_r in COLUMN_ROLES},
            "start": found.get("start") or None, "end": (manual or {}).get("end") or None,
            "auto_end": found.get("end") or None}


@require_http_methods(["GET", "POST"])
def batch_mark(request, batch_id):
    """Лист тизимга тушунарсиз (ёки нотўғри ўқилган): смета­чи файлнинг ўзида босиб кўрсатади — лист тури,
    биринчи қатор, устунлар, керак бўлса охирги қатор. Сақлангач қайта жуфтланади ва тузилмаси эслаб қолинади."""
    batch = _get_work(request, Batch, id=batch_id)
    key = request.POST.get("key") or request.GET.get("k", "")
    s = batch.sheet(key)
    if s is None:
        raise Http404("Лист топилмади")
    if request.method == "POST":
        if batch.owner_id != request.user.pk:
            messages.error(request, "Бу иш сизники эмас — ўзгартириб бўлмайди")
            return redirect("batch", batch_id=batch.id)
        if batch.project_id and batch.stage in (Batch.Stage.QUEUED, Batch.Stage.CONVERTING, Batch.Stage.PRICING):
            messages.error(request, "Бу қисм ҳозир ишланяпти — тугагач ўзгартиринг")
            return redirect("batch", batch_id=batch.id)
        role = request.POST.get("role", "")
        if role not in ROLE_LABELS:
            messages.error(request, "Лист турини танланг")
            return redirect(_mark_url(batch, key, _safe_next(request)))
        cols = {}
        for k, need in MARK_COLS.get(role, []):
            v = (request.POST.get(k) or "").strip().upper()
            try:
                if v:
                    cols[k] = column_index_from_string(v)
            except ValueError:
                pass
        for k in ("start", "end"):
            v = (request.POST.get(k) or "").strip()
            if v.isdigit() and int(v) > 0:
                cols[k] = int(v)
        if cols.get("end") and cols.get("start") and cols["end"] < cols["start"]:
            cols.pop("end")
        missing = [dict(COLUMN_ROLES_BY_KEY)[k] for k, need in MARK_COLS.get(role, []) if need and not cols.get(k)]
        if role != "unknown" and (missing or not cols.get("start")):
            messages.error(request, "Кўрсатилмади: " + ", ".join(missing + ([] if cols.get("start") else ["биринчи қатор"])))
            return redirect(_mark_url(batch, key, _safe_next(request)))
        roles, columns = dict(batch.roles or {}), dict(batch.columns or {})
        roles[key] = role
        if role == "unknown":
            columns.pop(key, None)
        else:
            columns[key] = cols
        analyze_batch(batch, roles, columns)
        remembered = remember_sheets(batch, [key])
        batch.status = Batch.Status.REVIEW
        batch.save()
        if batch.project_id and not batch.jobs.exists():
            batch.stage, batch.message = ready_stage(batch)
            batch.save(update_fields=["stage", "message"])
            settle(batch.project)
        done = batch.sheet(key) or s
        text = f'«{s["sheet"]}» листи сиз кўрсатгандек ўқилди' + (
            f' — {done.get("rows") or 0} та қатор.' if role != "unknown" else " — ўтказилади.")
        messages.success(request, text + (" " + MEMO_SAVED if remembered else ""))
        return redirect(_safe_next(request) or reverse("batch", args=[batch.id]))
    manual = (batch.columns or {}).get(key)
    f = batch.files[s["file"]]
    return render(request, "jobs/mark.html", {
        "batch": batch, "s": s, "key": key, "file_name": f["name"], "crumbs": crumbs_for(batch=batch),
        "state": _mark_state(s, manual), "manual": bool(manual) or key in (batch.roles or {}),
        "roles_js": [{"key": k, "label": label, "hint": hint} for k, label, _r, hint in COLUMN_ROLES],
        "mark_cols": MARK_COLS, "row_hints": ROW_HINTS, "limit": MARK_ROWS,
        "verdict": _sheet_verdict(s, _column_view(s, manual), {x["key"]: x for x in batch.sheets}, key in (batch.roles or {})),
        "next": _safe_next(request),
        "batch_url": reverse("batch", args=[batch.id]),
    })


def _mark_url(batch, key, nxt="") -> str:
    from urllib.parse import urlencode
    return f'{reverse("batch_mark", args=[batch.id])}?' + urlencode({"k": key, **({"next": nxt} if nxt else {})})


def _safe_next(request) -> str:
    """Белгилашдан кейин қайтиш манзили (папка саҳифаси ва ҳ.к.) — фақат шу сайт ичида."""
    from django.utils.http import url_has_allowed_host_and_scheme
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    return nxt if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}, request.is_secure()) else ""


COLUMN_ROLES_BY_KEY = [(k, label) for k, label, *_r in COLUMN_ROLES]


MEMO_SAVED = ("Созлашингиз эслаб қолинди: шундай тузилишдаги лист кейин келса, тизим уни ўзи шундай ўқийди — "
              "қайта кўрсатишингиз шарт эмас.")


@require_POST
def batch_forget(request, batch_id):
    """Листга қўлланган «аввалги созлама» нотўғри — унутилади, лист қайта автомат таҳлил қилинади."""
    batch = _get_work(request, Batch, id=batch_id)
    if batch.owner_id != request.user.pk:
        return redirect("batch", batch_id=batch.id)
    if batch.project_id and batch.stage in (Batch.Stage.QUEUED, Batch.Stage.CONVERTING, Batch.Stage.PRICING):
        messages.error(request, "Бу қисм ҳозир ишланяпти — тугагач ўзгартиринг")
        return redirect("batch", batch_id=batch.id)
    if forget_sheet(batch, request.POST.get("key", "")):
        analyze_batch(batch)
        batch.save()
        if batch.project_id and not batch.jobs.exists():
            batch.stage, batch.message = ready_stage(batch)
            batch.save(update_fields=["stage", "message"])
            settle(batch.project)
        messages.success(request, "Аввалги созлама унутилди — лист автомат ўқилди. Керак бўлса, қайта кўрсатинг.")
    return redirect("batch", batch_id=batch.id)


def batch_file(request, batch_id, i):
    """Объектдаги ``i``-файлнинг асл нусхасини юклаб олиш (тузатиб, қайта юклаш учун)."""
    batch = _get_work(request, Batch, id=batch_id)
    if not 0 <= i < len(batch.files):
        raise Http404("Файл топилмади")
    path = batch_original(batch, i)
    if path is None:
        raise Http404("Файл ҳали очилмаган")
    name = batch.files[i].get("name") or path.name
    if Path(name).suffix.lower() != path.suffix.lower():
        name = Path(name).stem + path.suffix
    _audit_download(request, batch, "object", name, what="асл файл")
    return FileResponse(open(path, "rb"), as_attachment=True, filename=name)


@require_POST
def batch_file_replace(request, batch_id, i):
    """Битта файлни (локал ёки RES) тузатилганига алмаштириш — объектнинг қолгани ўзгармайди."""
    batch = _get_work(request, Batch, id=batch_id)
    f = request.FILES.get("file")
    try:
        if not f:
            raise UploadError("Файлни танланг")
        if batch.project_id and batch.stage in (Batch.Stage.QUEUED, Batch.Stage.CONVERTING, Batch.Stage.PRICING):
            raise UploadError("Бу қисм ҳозир ишланяпти — тугагач алмаштиринг")
        old = batch.files[i]["name"] if 0 <= i < len(batch.files) else ""
        replace_batch_file(batch, i, f)
        audit(request, Event.Kind.UPLOAD, work=batch, work_kind="object", detail=f"файл алмаштирилди: {old} → {f.name}")
        msg = f"«{f.name}» юкланди ва листлари қайта ўқилди." + _after_swap(batch, i)
        messages.success(request, msg)
    except UploadError as e:
        messages.error(request, str(e))
        return _swap_reply(request, batch, error=str(e))
    return _swap_reply(request, batch, msg=msg)


@require_POST
def batch_file_undo(request, batch_id, i):
    """Битта файлнинг охирги алмаштиришини бекор қилиш — олдинги нусха қайтади, боғлиқ сметалар қайта ҳисобланади."""
    batch = _get_work(request, Batch, id=batch_id)
    try:
        if batch.project_id and batch.stage in (Batch.Stage.QUEUED, Batch.Stage.CONVERTING, Batch.Stage.PRICING):
            raise UploadError("Бу қисм ҳозир ишланяпти — тугагач қайтаринг")
        cur = batch.files[i]["name"] if 0 <= i < len(batch.files) else ""
        name = undo_batch_file(batch, i)
        audit(request, Event.Kind.UPLOAD, work=batch, work_kind="object", detail=f"файл қайтарилди: {cur} → {name}")
        msg = f"«{name}» — олдинги нусха қайтарилди." + _after_swap(batch, i)
        messages.success(request, msg)
    except UploadError as e:
        messages.error(request, str(e))
        return _swap_reply(request, batch, error=str(e))
    return _swap_reply(request, batch, msg=msg)


def _swap_reply(request, batch, msg="", error=""):
    """Файл алмаштириш / қайтариш жавоби. Саҳифа ичидан (fileops.js, фонда) — JSON: саҳифа ўзи янгиланади,
    хабар (messages) янгиланган қисмда чиқади. Оддий форма — олдингидек йўналтириш."""
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"ok": not error, "message": msg, "error": error}, status=400 if error else 200)
    return _back(request, reverse("batch", args=[batch.id]) + "#structure")


def _after_swap(batch, i) -> str:
    """Файл алмашгач/қайтгач: нархланмаган қисм — «тайёр» ҳолатига; нархланган — фақат шу файлга боғлиқ
    сметалар қайта ҳисобланади (бутун папка эмас). Хабарга қўшимча матн."""
    if not batch.jobs.exists():
        if batch.project_id:
            batch.stage, batch.message = ready_stage(batch)
            batch.save(update_fields=["stage", "message"])
            settle(batch.project)
        return ""
    done, missing = refresh_file_jobs(batch, i)
    out = f" {len(done)} та смета шу файл билан қайта ҳисобланяпти (қўлда ўзгаришлар сақланди)." if done else ""
    if missing:
        out += (f" {len(missing)} та сметанинг листи янги файлда йўқ — уларни қисмни очиб, жуфтликни тўғирлаб"
                " қайта нархланг.")
    return out


def _back(request, fallback: str):
    """``next`` — шу сайт ичидаги манзил бўлса ўша ерга (папка саҳифасидан босилган амаллар), акс ҳолда ``fallback``."""
    from django.utils.http import url_has_allowed_host_and_scheme
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(nxt)
    return redirect(fallback)


@require_POST
def batch_confirm(request, batch_id):
    batch = _get_work(request, Batch, id=batch_id)
    decisions = {}
    for n in range(len(batch.pairs)):
        decisions[n] = {
            "include": request.POST.get(f"include_{n}") == "1",
            "res": request.POST.get(f"res_{n}", ""),
            "label": request.POST.get(f"label_{n}", ""),
        }
    xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    left = 0 if nf_auto_of(batch.owner_id) else manual_left(batch)
    if left:  # «Қўлда»: ҳамма листни смета­чи ўзи белгилаши керак
        msg = f"«Қўлда» усули: яна {left} та листнинг турини белгиланг (Локал / Ресурс / Ўтказиш)."
        if xhr:
            return JsonResponse({"ok": False, "error": msg}, status=400)
        messages.error(request, msg)
        return redirect("batch", batch_id=batch.id)
    if batch.project_id:
        queue_part(batch, decisions)  # папка қисми: фонда нархланади, жараён папка саҳифасида жонли
        url = reverse("project", args=[batch.project_id])
    else:
        try:
            jobs = confirm_batch(batch, decisions)
        except Exception as e:  # noqa: BLE001
            if xhr:
                return JsonResponse({"ok": False, "error": f"Нархлашда хато: {e}"}, status=500)
            raise
        # битта смета — тўғри унинг саҳифасига; бир нечта — объект натижалари
        url = reverse("job", args=[jobs[0].id]) if len(jobs) == 1 else reverse("batch", args=[batch.id])
    if xhr:
        return JsonResponse({"ok": True, "url": url})
    return redirect(url)


def batch_download(request, batch_id):
    batch = _get_work(request, Batch, id=batch_id)
    try:
        path = build_combined(batch)
    except UploadError as e:
        raise Http404(str(e))
    _audit_download(request, batch, "object", path.name)
    return FileResponse(open(path, "rb"), as_attachment=True, filename=path.name)


def job_detail(request, job_id):
    job = _get_work(request, Job, id=job_id)
    if job.recalc_pending:
        recalc.resume(Job.objects.filter(pk=job.pk))
    ctx = {"job": job, "modes": MODE_LABELS.items(), "crumbs": crumbs_for(job=job),
           "siblings": list(job.batch.jobs.order_by("batch_order")) if job.batch_id else [],
           # база танлови фақат RES'сиз (базадан нархланган) сметада — RES'лисида база ишлатилмайди
           **(_base_ctx(job.base_period) if not job.res_path else {"base_on": False})}
    if (job.status == Job.Status.DONE and job.mode == "smart" and not job.auto_cleared
            and job.recalc_seq <= job.recalc_done and job.owner_id == request.user.pk and nf_auto_of(job.owner_id)):
        # олдин нархланган смета: бўлим номлари (бирлиги ҳам, таклифи ҳам йўқ) бир марта ўзи тозаланади
        a = [r["row"] for r in _not_found_stage(job.result.get("rows", []))["group_a"]]
        job.auto_cleared = True
        if a:
            recalc.request(job, cleared=sorted(set(job.cleared or []) | set(a)))
        else:
            job.save(update_fields=["auto_cleared"])
    elif (job.status == Job.Status.DONE and job.mode == "smart" and job.recalc_seq <= job.recalc_done
          and job.owner_id == request.user.pk and nf_auto_of(job.owner_id)):
        # «Автоматик» усул: олдин қолиб кетган нарх таклифлари (масалан, бирлиги бошқа) — бир йўла қабул қилинади
        extra = open_suggestions(job)
        if extra:
            recalc.request(job, prices={**(job.prices or {}), **extra})
    if job.status == Job.Status.DONE:
        res = job.result
        totals = res["totals"]
        mode = res.get("mode", "compat")
        labels = column_labels(job) if mode == "smart" else dict(CATEGORY_LABELS)
        res_groups = _res_groups(res, labels) if mode == "smart" else []
        # жадвал нархланган сметанинг ўзидан (Excel кўриниши) очилади
        view = request.GET.get("view") or ("xl_out" if job.output_path else "res" if res_groups else "local")
        if view not in XL_FILES and not res_groups:
            view = "local"
        ctx.update(
            view=view,
            grid_view="local" if view == "local" or not res_groups else ("res" if view in XL_FILES else view),
            xl_file=XL_FILES.get(view),
            xl_files=[(v, label) for v, (_f, label) in XL_FILES.items() if v != "xl_res" or job.res_path],
            res_groups=res_groups,
            result_mode=mode,
            # «N та қатор нархланмади» — ён панелда ва сводкада бор, бу ерда такрорламаймиз
            warnings=[w for w in res.get("warnings", []) if "нархланмади" not in w and not w.startswith("Текширувдан")],
            checks=res.get("checks", []),
            check_groups=check_groups(res.get("checks", []), res["rows"]),
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
            percents_changed=mode == "smart" and {**DEFAULT_PERCENTS, **(job.percents or {})} != DEFAULT_PERCENTS,
            pct_memory=_pct_memory(job) if mode == "smart" else None,
            s_nds=totals.get("s_nds"),
            has_errors=any(isinstance(v, str) for v in totals.values()),
            rows=res["rows"],
            problems=res["problems"],
            category_counts=[(c, label, sum(1 for r in res["rows"] if _is_resource(r) and r.get("category", "K") == c))
                             for c, label in labels.items()],
            category_choices=labels.items(),
            # L/M таклифлари 2-босқич карталарида; бу ерда қолганлари (масалан I/J/K)
            category_suggestions=[s for s in _category_suggestions(res["rows"]) if mode != "smart" or s["cat"] not in LM_COLUMNS],
            override_count=sum(1 for r in res["rows"] if r.get("override")),
            bez_nds=totals.get("bez_nds"),
            not_priced=res["stats"].get("NOT_PRICED", res["stats"].get("NON_NUMERIC", 0)),
            sugg_price_count=sum(1 for r in res["rows"] if r.get("suggested_price") is not None and not r.get("manual")),
            manual_count=sum(1 for r in res["rows"] if r.get("manual")),
            sugg_cat_count=sum(1 for r in res["rows"] if r.get("suggested_category")),
            nf=_not_found_stage(res["rows"]) if mode == "smart" else None,
            lm=_lm_stage(res["rows"], res_groups, labels, _accepted_lm(job)) if mode == "smart" else None,
        )
        ctx["sugg_marks"] = _sugg_marks(res["rows"]) if mode == "smart" else []
        ctx["price_marks"] = _price_marks(res["rows"]) if mode == "smart" else []
        ctx["stages"] = _stages(job, ctx["nf"], ctx["lm"]) if mode == "smart" else []
        # саҳифа қайси босқичда очилади: биринчи тугамагани (compat — жадвал)
        ctx["default_tab"] = next((st["n"] for st in ctx["stages"] if st["state"] == "current"), 3 if ctx["stages"] else 2)
    return render(request, "jobs/job.html", ctx)


def _sugg_marks(rows):
    """Устун таклифлари — Excel кўринишида белгилаш ва бирма-бир ўтиш учун: қатор, қаерга (L, M, …),
    ҳозир қаерда, сабаби. ``_lm_stage`` дан кейин чақирилади (``lm_target`` шу ерда қўйилади)."""
    out = []
    for r in rows:
        to = r.get("lm_target") or r.get("suggested_category")
        if to and to != r.get("category") and _is_resource(r):
            out.append({"row": r["row"], "to": to, "from": r.get("category") or "", "name": r.get("name") or "",
                        "why": r.get("suggest_reason") or ("ресурс файлида шу бўлимда" if r.get("lm_target") else ""),
                        "h": r["h"] if isinstance(r.get("h"), (int, float)) and not isinstance(r["h"], bool) else None})
    return out


def _price_marks(rows):
    """Excel кўринишида G катагидан нарх ёзиш учун: нархланмаган (қизил) ва қўлда нархланган қаторлар —
    номи, бирлиги, миқдори, ресурсдаги энг яқин таклиф. ``_not_found_stage`` дан кейин (``near`` шу ерда тўлади)."""
    out = []
    for r in rows:
        unpriced = r.get("status") in UNPRICED_STATUSES and not r.get("cleared") and _has_unit(r)
        if not (unpriced or r.get("manual")):
            continue
        near = r.get("near") or {}
        out.append({"row": r["row"], "name": r.get("name") or "", "unit": r.get("unit") or "",
                    "qty": r.get("qty") if isinstance(r.get("qty"), (int, float, str)) else None,
                    "g": r.get("g") if isinstance(r.get("g"), (int, float, str)) else None,
                    "manual": bool(r.get("manual")), "sugg": r.get("suggested_price"),
                    "near": {"name": near.get("name"), "unit": near.get("unit"), "price": near.get("price"),
                             "score": near.get("score"), "same_unit": near.get("same_unit", True)} if near else None})
    return out


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
    job = _get_work(request, Job, id=job_id)
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

    from resbase.lm import accepted_rows
    categories, accepted = dict(job.categories or {}), accepted_rows(job)
    for r in rows:
        accepted.discard(r)
        if not category or category == known[r]["default_category"]:
            categories.pop(str(r), None)  # дастур аниқлагани билан бир хил — қўлда белги шарт эмас
        else:
            categories[str(r)] = category
            if category in ("L", "M") and known[r].get("suggested_category") == category:
                accepted.add(r)  # таклифни қабул қилди — ускуна базасига овоз эмас
    job.lm_accepted = sorted(accepted)
    recalc.request(job, categories=categories)
    return JsonResponse({"ok": True, "queued": True, "changed": len(rows), "overrides": len(job.categories)})


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
    job = _get_work(request, Job, id=job_id)
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
        recalc.request(job, prices=prices)
    return JsonResponse({"ok": True, "queued": bool(changed), "changed": changed, "skipped": len(skipped)})


def _has_unit(row) -> bool:
    """Бирлик бор. Номи билан бир хил «бирлик» — C:D бирлаштирилган катак (бирлик эмас)."""
    unit = str(row.get("unit") or "").strip()
    return unit not in ("", "None") and unit != str(row.get("name") or "").strip()


def _movable(row) -> bool:
    """L/M га ўтказса бўладиган ресурс: бирлиги бор — ёки бирлиги ёзилмаган, лекин тартиб рақами ва рақамли
    нархи бор (масалан «309 | ЦЕНА | ВРУ1-21-10УХЛ4 | — | 1», RES'дан аниқ нархланган). Бўлим номи — йўқ."""
    if _has_unit(row):
        return True
    g = row.get("g")
    return (bool(str(row.get("num") or "").strip()) and isinstance(g, (int, float)) and not isinstance(g, bool)
            and g > 0)


def _not_found_stage(rows):
    """1-босқич: «ТОПИЛМАДИ» ёзилган қаторлар — бирлиги бор (ресурс, нархи йўқ) ва бирлиги йўқ
    (бўлим номи ва ш.к.) га ажратилган; аввал тозаланганлари алоҳида (қайтариш учун).
    Ҳар бир қаторга смета ичидаги ўрни қўшилади: қайси бўлим остида (``ctx_section``) ва қайси иш
    таркибида (``ctx_work`` — «12 Е0601-26-4 УСТРОЙСТВО…»), смета­чи Excel билан осон солиштирсин."""
    section, work = "", ""
    pending = []
    for r in rows:  # смета тартибида
        if not r.get("name"):
            continue
        r["is_heading"] = not _has_unit(r) and not str(r.get("num") or "").strip()
        if r["is_heading"]:
            r["ctx_section"], r["ctx_work"] = section, ""
            section, work = str(r["name"]).strip(), ""
        else:
            if r.get("status") == "WORK_HEADER":
                work = " ".join(str(x).strip() for x in (r.get("num"), r.get("code"), r.get("name")) if x)
            elif "." not in str(r.get("num") or "") and r.get("num"):
                work = ""  # мустақил ресурс («70 СЦЕНА …») — аввалги ишга тегишли эмас
            r["ctx_section"], r["ctx_work"] = section, work
        r["nf_kind"] = "unit" if _has_unit(r) else "nounit"
        if r.get("status") in UNPRICED_STATUSES and not r.get("cleared"):
            if not r.get("near") and r.get("suggested_price") is not None and r.get("picked_name"):
                # эски натижа (яқин ресурс сақланмаган): LOG'даги таклиф
                score = r.get("score")
                r["near"] = {"name": r["picked_name"], "unit": "", "price": r["suggested_price"],
                             "score": score if isinstance(score, (int, float)) else 0, "same_unit": True}
            if r.get("near"):
                # H формуласи каби: миқдор ЛОКАЛДАН (F, бўш бўлса E), нарх RES дан
                r["qty_col"] = "E" if r.get("qty_total") in (None, "") else "F"
                q, p = _as_price(r.get("qty")), _as_price(r["near"].get("price"))
                r["near_sum"] = q * p if q is not None and p is not None else None
            pending.append(r)
    # бўлим номи учун — остида нима бор: кейинги бўлимгача қаторлар сони ва биринчи иш
    named = [r for r in rows if r.get("name")]
    for i, r in enumerate(named):
        if not r.get("is_heading"):
            continue
        below = []
        for x in named[i + 1:]:
            if x.get("is_heading"):
                break
            below.append(x)
        first = next((x for x in below if x.get("status") == "WORK_HEADER"), below[0] if below else None)
        r["ctx_count"] = len(below)
        r["ctx_first"] = " ".join(str(x).strip() for x in (first.get("num"), first.get("name")) if x) if first else ""
    return {
        "pending": len(pending),
        "rows": pending,
        "with_unit": [r for r in pending if _has_unit(r)],
        "no_unit": [r for r in pending if not _has_unit(r)],
        # гуруҳлар (смета­чи тилида): A — бирлиги ҳам, таклифи ҳам йўқ; B — бирлиги бор, нарх йўқ (таклифли/таклифсиз)
        "group_a": [r for r in pending if not _has_unit(r) and not r.get("near")],
        "group_b_near": [r for r in pending if _has_unit(r) and r.get("near")],
        "group_b_none": [r for r in pending if _has_unit(r) and not r.get("near")],
        "cleared": [r for r in rows if r.get("cleared")],
        "cleared_a": sum(1 for r in rows if r.get("cleared") and not _has_unit(r)),
    }


@require_POST
def job_not_found(request, job_id):
    """JSON: ``{"rows": [...], "action": "clear" | "mark" | "restore"}``. clear — «ТОПИЛМАДИ» сўзини олиб ташлаш:
    бирлиги бор қаторда G бўш, қизил қолади; бирлиги йўқда G ва H бўш. mark — сўз олинади, G сариқ
    («кўриб чиқилди», нарх кейин). restore — қайтариш."""
    job = _get_work(request, Job, id=job_id)
    if job.status != Job.Status.DONE or job.mode != "smart":
        return JsonResponse({"ok": False, "error": "Фақат Smart режимдаги натижада"}, status=400)
    try:
        data = json.loads(request.body or b"{}")
        rows = {int(r) for r in data["rows"]}
        action = data["action"]
    except (ValueError, TypeError, KeyError):
        return JsonResponse({"ok": False, "error": "нотўғри сўров"}, status=400)
    by_row = {r["row"]: r for r in job.result.get("rows", [])}
    allowed = {r for r, x in by_row.items() if x.get("status") in UNPRICED_STATUSES or x.get("cleared")}
    if not rows or action not in ("clear", "mark", "restore") or not rows <= allowed:
        return JsonResponse({"ok": False, "error": f"нотўғри сўров: {sorted(rows - allowed)[:5] or action}"}, status=400)
    cleared, marked = set(job.cleared or []), set(job.marked or [])
    if action == "clear":  # сўз олинади: бирлиги борида G қизил, йўғида G ва H бўш
        cleared, marked = cleared | rows, marked - rows
    elif action == "mark":  # сўз олинади, G сариқ — «кўриб чиқилди, нарх кейин»
        cleared, marked = cleared - rows, marked | rows
    else:
        cleared, marked = cleared - rows, marked - rows
    recalc.request(job, cleared=sorted(cleared), marked=sorted(marked))
    return JsonResponse({"ok": True, "queued": True, "changed": len(rows)})


LM_COLUMNS = ("L", "M")


def _accepted_lm(job) -> set:
    from resbase.lm import accepted_rows
    try:
        return accepted_rows(job)
    except Exception:  # noqa: BLE001 — эски смета: маълумот йўқ
        return set()


def _lm_stage(rows, res_groups, labels, accepted=None):
    """2-босқич: L (кабель) ва M (ускуна) га ўтказиш керак бўлиши мумкин қаторлар — таклиф қилинганлар
    ва RES'да шу бўлимда турганлари. Ҳар бир қаторга ``lm_target`` белгиси қўйилади (жадвалда танлаш учун)."""
    by_row = {r["row"]: r for r in rows}
    targets = {}
    for r in rows:
        if r.get("suggested_category") in LM_COLUMNS:
            targets[r["row"]] = r["suggested_category"]
    for g in res_groups:
        if g["column"] in LM_COLUMNS:
            for n in g["to_move"]:
                targets.setdefault(n, g["column"])
    cards = []
    for c in LM_COLUMNS:
        # бўлим номлари (бирлиги йўқ), иш сарлавҳалари ва машинистлар ўтказилмайди
        pending = [by_row[n] for n in sorted(targets) if targets[n] == c and by_row[n].get("category") != c
                   and _movable(by_row[n]) and by_row[n].get("status") not in ZERO_STATUSES + ("CLEARED",)]
        for r in pending:
            r["lm_target"] = c
        in_col = [r for r in rows if _is_resource(r) and r.get("category") == c]
        num = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
        cards.append({"col": c, "label": labels.get(c, c), "rows": pending,
                      "done": len(in_col), "done_sum": sum(num(r.get("h")) for r in in_col),
                      # ўтказилганлар: тизим таклифи қабул қилингани ва смета­чи ўзи ўтказгани
                      "accepted": sum(1 for r in in_col if r["row"] in (accepted or ())),
                      "moved": sum(1 for r in in_col if r.get("override") and r["row"] not in (accepted or ())),
                      "sum": sum(r["h"] for r in pending if isinstance(r.get("h"), (int, float)) and not isinstance(r["h"], bool))})
    return {"cards": cards, "pending": sum(len(c["rows"]) for c in cards)}


@require_POST
def job_table_checked(request, job_id):
    """2-босқични якунлаш (``done=1``) ёки қайта очиш (``done=0``)."""
    job = _get_work(request, Job, id=job_id)
    job.table_checked = request.POST.get("done") == "1"
    job.save(update_fields=["table_checked"])
    return redirect(reverse("job", args=[job.id]) + ("#t-3" if job.table_checked else "#t-2"))


def _stages(job, nf, lm):
    """Смета­чи йўли: 1 — топилмаганлар, 2 — жадвал (кабель/ускунани ажратиш), 3 — сводка ва фоизлар (кейин юклаб олиш)."""
    s1 = not nf["pending"]
    s2 = s1 and job.table_checked
    return [
        {"n": 1, "anchor": "stage-nf", "title": "Топилмаганлар", "state": "done" if s1 else "current",
         "note": "тайёр" if s1 else f"{nf['pending']} та нархланмаган"},
        {"n": 2, "anchor": "stage-table", "title": "Жадвал",
         "state": "done" if s2 else ("current" if s1 else "locked"),
         "note": "текширилди" if s2 else (f"{lm['pending']} та кабель/ускуна таклифи" if lm["pending"]
                                          else "жадвални кўриш")},
        {"n": 3, "anchor": "svodka", "title": "Сводка ва фоизлар", "state": "current" if s2 else "locked",
         "note": "фоизлар, юклаб олиш"},
    ]


def _pct_memory(job):
    """Эслаб қолинган фоизлар: стандартдан фарқлилари ва шу смета улардан фарқ қиладими."""
    saved = remembered_percents(job.owner_id)
    labels = dict((k, t) for k, t, p in svodka_layout([]) if p is not None)
    diff = [{"text": labels.get(k, k), "pct": pct_text(v), "default": pct_text(DEFAULT_PERCENTS[k])}
            for k, v in saved.items() if k in DEFAULT_PERCENTS and v != DEFAULT_PERCENTS[k]]
    own = {**DEFAULT_PERCENTS, **(job.percents or {})}
    mine = {**DEFAULT_PERCENTS, **saved}
    return {"diff": diff, "differs": own != mine}


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
    job = _get_work(request, Job, id=job_id)
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
    recalc.request(job, categories=categories)
    return JsonResponse({"ok": True, "queued": True, "col": col})


@require_POST
def job_base(request, job_id):
    """Сметанинг ресурс базаси даври ўзгарди — қайта нархланади (смета­чи танлови ҳам эслаб қолинади)."""
    from resbase.services import parse_period
    job = _get_work(request, Job, id=job_id)
    job.base_period = parse_period(request.POST)
    remember_base_period(job.owner_id, job.base_period)
    recalc.request(job)
    return redirect("job", job_id=job.id)


@require_POST
def job_run(request, job_id):
    job = _get_work(request, Job, id=job_id)
    if "reset_percents" in request.POST:
        percents = dict(DEFAULT_PERCENTS)  # аниқ «стандарт» (бўш = эслаб қолинган фоизлар)
    elif any(k.startswith("pct_") for k in request.POST):
        percents = parse_percents(request.POST, job.percents)
    else:
        percents = None  # yuqoridagi forma: foizlar o'zgarmaydi
    recalc.request(job, request.POST.get("local_sheet"), request.POST.get("res_sheet"),
                   request.POST.get("fuzzy") or job.fuzzy, request.POST.get("mode"), percents)
    if percents is not None:
        remember_percents(job.owner_id, job.percents)
    return redirect("job", job_id=job.id)


@require_POST
def job_reprice(request, job_id):
    """«Қайта нархлаш» — бошидан: смета­чи қилган ишлар (қўлда нарх, қабул қилинган таклифлар, L/M ўтказишлар,
    тозалаш/белгилашлар, босқич белгилари) бекор, смета биринчи марта юкланганидек нархланади — «Автоматик»
    усулда таклифлар ва L/M яна ўзи қўйилади. Фоизлар, листлар ва устунлар сақланади. Саҳифада икки марта сўралади."""
    job = _get_work(request, Job, id=job_id)
    if request.POST.get("confirm") != "1":
        messages.error(request, "Қайта нархлаш тасдиқланмади")
        return redirect("job", job_id=job.id)
    job.auto_cleared, job.table_checked, job.lm_accepted = False, False, []
    job.save(update_fields=["auto_cleared", "table_checked", "lm_accepted"])
    recalc.request(job, categories={}, prices={}, cleared=[], marked=[])
    messages.success(request, f"«{job.batch_label or job.local_name}» — қўлда ўзгаришлар бекор қилинди, смета бошидан нархланяпти")
    return _back(request, reverse("job", args=[job.id]))


@require_POST
def job_percents(request, job_id):
    """JSON: ``{"pct": {"transport_mat": "5", ...}}`` | ``{"reset": true}`` | ``{"remembered": true}``.
    Фоиз ўзгариши билан қайта ҳисоблайди (тугма шарт эмас) ва уни эслаб қолади — кейинги сметалар
    шу фоизлар билан бошланади. Жавобда янги сводка (саҳифа қайта юкланмайди)."""
    job = _get_work(request, Job, id=job_id)
    if job.status != Job.Status.DONE or job.mode != "smart":
        return JsonResponse({"ok": False, "error": "Фоизлар фақат Smart режимда"}, status=400)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "нотўғри сўров"}, status=400)
    if data.get("reset"):
        percents = dict(DEFAULT_PERCENTS)
    elif data.get("remembered"):
        percents = remembered_percents(job.owner_id)
    elif isinstance(data.get("pct"), dict):
        # бўш катак — 0 (смета­чи ўчириб қўйди): «стандарт» эмас, акс ҳолда саҳифада бўш, Excel'да 2% бўлиб қолади
        percents = parse_percents({f"pct_{k}": str(v).strip() or "0" for k, v in data["pct"].items()}, job.percents)
    else:
        return JsonResponse({"ok": False, "error": "нотўғри сўров"}, status=400)
    recalc.request(job, percents=percents)
    remember_percents(job.owner_id, job.percents)
    return JsonResponse({"ok": True, "queued": True})


def _svodka_payload(job):
    """Сводка (фоизлар ва суммалар) — фоиз ўзгаргач саҳифа қайта юкланмай янгиланади."""
    totals = job.result["totals"]
    return {
        "rows": [{"key": s["key"], "value": som(s["value"]), "pct": pct_input(s["pct"]) if s["pct"] is not None else None,
                  "changed": s["pct"] is not None and s["pct"] != s["default"]} for s in _svodka_rows(job.result, "smart")],
        "s_nds": som(totals.get("s_nds")), "bez_nds": som(totals.get("bez_nds")),
        "changed": {**DEFAULT_PERCENTS, **(job.percents or {})} != DEFAULT_PERCENTS,
    }


def job_state(request, job_id):
    """Қайта ҳисоб ҳолати (саҳифа шуни сўраб туради): ``pending`` — навбатда ёки ҳисобланяпти; тугагач —
    натижа ҳолати ва smart режимда янги сводка."""
    job = _get_work(request, Job, id=job_id)
    if job.recalc_pending:
        recalc.resume(Job.objects.filter(pk=job.pk))
    data = {"pending": job.recalc_pending, "status": job.status, "error": job.error if job.status == Job.Status.FAILED else "",
            "running": bool(job.recalc_worker), "duration": job.duration,
            "elapsed": round((timezone.now() - job.recalc_requested).total_seconds()) if job.recalc_requested else None}
    if not job.recalc_pending and job.status == Job.Status.DONE and job.result.get("mode") == "smart":
        data["svodka"] = _svodka_payload(job)
    return JsonResponse(data)


# Нархлашдан кейинги текширув: тури -> (сарлавҳа, нима бўлди, нима қилиш керак) — смета­чига оддий тилда
CHECK_KINDS = {
    "no_qty": ("Миқдори йўқ — сумма 0 бўлиб қолди",
               "Бу қаторларда нарх топилди, лекин миқдор («Кол-во») катаги бўш ёки унда сон эмас, матн турибди. "
               "Сумма = миқдор × нарх бўлгани учун 0 чиқди ва смета жамига кирмади.",
               "Локал файлда шу қаторнинг миқдорини текширинг (одатда F — «по проектным данным»). Миқдор бошқа устунда "
               "бўлса — объект саҳифасида «Листлар тури» → «тузатиш» орқали устунларни кўрсатинг ва қайта нархланг. "
               "Қатор изоҳ ёки ортиқча бўлса — эътибор бермасангиз ҳам бўлади."),
    "amount": ("Сумма нотўғри ҳисобланди",
               "Сумма (H) миқдор × нархга тенг чиқмади.",
               "Одатда миқдор устуни нотўғри аниқланганда бўлади: объект саҳифасида устунларни текширинг ва қайта нархланг."),
    "column": ("Сумма I–M устунига тушмади",
               "Сумма иш ҳақи, машина, материал… устунларидан бирига ўтмади — жамида кўринмайди.",
               "Қаторни белгиланг ва пастки панелдан тўғри устунни танланг."),
    "overwrite": ("Смета маълумоти устидан ёзилди",
                  "Нарх ёки сумма локалнинг номи, бирлиги ёки миқдори турган устунга ёзилиб қолди.",
                  "Локал устунлари нотўғри аниқланган: объект саҳифасида устунларни кўрсатинг ва қайта нархланг."),
    "zero_prices": ("RES'даги нархлар 0",
                    "Ресурс файлида топилган нархларнинг деярли ҳаммаси 0 — RES нархланмаган бўлса керак.",
                    "Нархланган RES файлини юкланг ёки RES'нинг нарх устунини текширинг."),
    "zero_total": ("Жами сумма 0",
                   "Нархлар топилди, лекин смета жами 0 чиқди.",
                   "Миқдор ёки сумма устуни нотўғри аниқланган — объект саҳифасида устунларни текширинг."),
    "other": ("Бошқа муаммолар", "Натижа текширувидан ўтмади.", "Қаторни очиб текширинг."),
}
_CHECK_TEXT = [("миқдори йўқ", "no_qty"), ("кутилгани", "amount"), ("устунига тушмади", "column"),
               ("манба устуни", "overwrite"), ("нархи 0", "zero_prices"), ("жами сумма 0", "zero_total")]


def check_groups(checks, rows) -> list:
    """Текширув муаммолари турлари бўйича: сарлавҳа, сабаб, ечим ва қаторлар (номи, нархи) — техник матн ўрнига."""
    by_row = {r["row"]: r for r in rows}
    groups = {}
    for c in checks:
        kind = c.get("kind") or next((k for t, k in _CHECK_TEXT if t in c.get("problem", "")), "other")
        title, why, fix = CHECK_KINDS.get(kind, CHECK_KINDS["other"])
        g = groups.setdefault(kind, {"kind": kind, "title": title, "why": why, "fix": fix, "rows": [], "general": []})
        row = by_row.get(c.get("row"))
        if row:
            g["rows"].append({"row": row["row"], "name": row.get("name") or "", "unit": row.get("unit") or "",
                              "g": row.get("g"), "qty": row.get("qty")})
        else:
            g["general"].append(c.get("problem", ""))
    order = list(CHECK_KINDS)
    return sorted(groups.values(), key=lambda g: order.index(g["kind"]))


# Жадвалнинг «файл кўриниши»: ?view=… -> (файл, тугма номи)
XL_FILES = {
    "xl_out": ("out", "Нархланган смета"),
    "xl_local": ("local", "Юкланган локал"),
    "xl_res": ("res", "Ресурс файли"),
}


def job_sheet(request, job_id):
    """Excel листи HTML жадвал бўлиб (``f`` — out | local | res, ``s`` — лист номи)."""
    job = _get_work(request, Job, id=job_id)
    try:
        data = preview.sheet_data(job, request.GET.get("f"), request.GET.get("s") or None)
    except preview.NotAvailable as e:
        raise Http404(str(e))
    except Exception as e:  # кўриниш бузилса ҳам саҳифа ишласин
        return JsonResponse({"error": f"{type(e).__name__}: {e}"}, status=500)
    if request.GET.get("head"):
        data = preview.head(data)  # аввал боши — дарҳол кўринади, кейин бутун жадвал
    return JsonResponse(data)


def job_download(request, job_id):
    job = _get_work(request, Job, id=job_id)
    if job.status != Job.Status.DONE or not job.output_path:
        raise Http404("Натижа ҳали тайёр эмас")
    path = job.abs(job.output_path)
    if not path.is_file():
        raise Http404("Файл топилмади")
    _audit_download(request, job, "smeta", job.download_name)
    return FileResponse(open(path, "rb"), as_attachment=True, filename=job.download_name)


# ===================== калькулятор =====================
def calc_page(request):
    """Муҳандис калькулятори: ҳажм, масса, арматура, металл, девор, пардоз, бирликлар, нарх (ҳисоб — браузерда)."""
    return render(request, "jobs/calc.html", {"nav": "calc"})


# ===================== йўриқнома =====================
def help_page(request):
    """Йўриқнома: иш тартиби қадамма-қадам ва ҳар бир ҳолат / огоҳлантириш нимани билдиради."""
    from .guide import VIDEO_PAGES, videos
    labels = dict(VIDEO_PAGES)
    vids = [{**v, "label": labels.get(v["page"], "") if v["page"] else ""} for v in videos()]
    return render(request, "jobs/help.html", {"nav": "help", "groups": help_groups(), "flow": FLOW, "videos": vids})


@require_POST
def guide_seen(request):
    """Саҳифа қўлланмаси кўрилди (ёки ёпилди) — кейин ўзи очилмайди. ``all`` — «Мен олдин фойдаланганман»:
    бирорта саҳифада ўзи очилмасин. ``reset`` — ҳаммасини қайта кўрсатиш."""
    user = request.user
    if request.POST.get("reset"):
        user.guides_seen = []
    elif request.POST.get("all"):
        user.guides_seen = list(GUIDE_PAGES)
    else:
        page = request.POST.get("page", "")
        if page not in GUIDE_PAGES:
            raise Http404
        if page not in user.guides_seen:
            user.guides_seen = [*user.guides_seen, page]
    user.save(update_fields=["guides_seen"])
    if request.POST.get("reset"):
        return redirect("index")
    return JsonResponse({"ok": True})


# ===================== смета­чи созламалари (5-босқич) =====================
def user_settings(request):
    """Бир жойда: иш усули, нарх манбаи ва давр, йўриқнома ўзи очилиши, изоҳ белгилари, жадвал зичлиги.
    Юклаш саҳифасидаги танловлар ҳам шу сақланганлар — иккаласи бир хил қийматни кўрсатади."""
    from resbase.services import base_on, parse_period
    from .services import remember_ui_prefs, ui_prefs_of
    user = request.user
    guide_auto = not set(GUIDE_PAGES) <= set(user.guides_seen or [])
    if request.method == "POST":
        before = {"nf_mode": nf_auto_of(user), **ui_prefs_of(user), "guide": guide_auto}
        after = {"nf_mode": request.POST.get("nf_mode") != "manual",
                 "tips": request.POST.get("tips") == "1", "dense": request.POST.get("dense") == "1",
                 "guide": request.POST.get("guide") == "1"}
        remember_nf_auto(user, after["nf_mode"])
        remember_ui_prefs(user, after)
        if after["guide"] != guide_auto:  # ўзгармаган бўлса — кўрилганлар рўйхатига тегмаймиз
            user.guides_seen = [] if after["guide"] else list(GUIDE_PAGES)
            user.save(update_fields=["guides_seen"])
        changed = [k for k in after if after[k] != before[k]]
        if base_on():
            source = "base" if request.POST.get("source") == "base" else "res"
            if source != price_source_of(user):
                changed.append("source")
            remember_price_source(user, source)
            if "region" in request.POST or "year" in request.POST:
                remember_base_period(user, parse_period(request.POST))
        request.track_detail = ",".join(changed) or "—"  # панел «Фойдаланувчи йўли»: нима кўп ўзгартирилади
        messages.success(request, "Созламалар сақланди.")
        return redirect("user_settings")
    return render(request, "jobs/settings.html", {
        "nav": "settings", "nf_auto": nf_auto_of(user), "price_source": price_source_of(user),
        "ui_prefs": ui_prefs_of(user), "guide_auto": guide_auto, **_base_ctx(base_period_of(user))})
