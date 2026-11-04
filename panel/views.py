"""Бошқарув панели (Django admin'дан алоҳида). Кўриш — ``is_staff``; ўзгартириш ва файл юклаб олиш — фақат
``is_superuser``. Панелдаги ҳар бир ўзгартириш ва юклаб олиш журналга ёзилади."""
import logging
from datetime import timedelta
from functools import wraps

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.models import AuthAttempt
from accounts.throttle import LOGIN_PER_EMAIL, LOGIN_PER_IP, WINDOW, client_ip
from jobs import preview
from jobs.folders import build_project_zip, stop_project
from jobs.models import Batch, Job, Project
from jobs.services import UploadError, build_combined
from jobs.views import PART_STATES, STAT_CARDS, _audit_download, _job_sums, light_jobs, work_items

from . import data, sessions, xl
from .audit import log as audit
from .logs import KEEP_DAYS
from .models import BlockedIP, Event, LoginSession, ServerLog

User = get_user_model()


def staff_required(view):
    """Панел борлиги ҳам оддий фойдаланувчига кўринмайди — 404."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        if not (request.user.is_active and request.user.is_staff):
            raise Http404
        return view(request, *args, **kwargs)
    return inner


def superuser_required(view):
    @wraps(view)
    def inner(request, *args, **kwargs):
        if not (request.user.is_active and request.user.is_staff):
            raise Http404
        if not request.user.is_superuser:
            raise PermissionDenied("Фақат суперфойдаланувчи")
        return view(request, *args, **kwargs)
    return inner


def _ctx(request, tab, **kw):
    day = timezone.now() - timedelta(days=1)
    return {"nav": "panel", "tab": tab, "can_act": request.user.is_superuser,
            "running_count": Project.objects.filter(status=Project.Status.PROCESSING).count(),
            "error_count": ServerLog.objects.filter(level__gte=logging.ERROR, created__gte=day).count(), **kw}


def _panel_items(user):
    """Фойдаланувчининг ишлари (дашборддаги каби), лекин ҳаволалар панелдаги кўринишга."""
    items = work_items(user)
    for it in items:
        work_id = it["url"].rstrip("/").rsplit("/", 1)[-1]
        it["view_url"] = it["url"]  # смета­чининг ўз саҳифаси (суперфойдаланувчига — текширув режимида)
        it["url"] = reverse("panel:work", args=[it["kind"], work_id])
        it["zip_url"] = reverse("panel:work_zip", args=[it["kind"], work_id])  # юклаган ҳамма асл файллари
        if it["total"] or it["state"] in ("done", "warn"):
            it["download_url"] = reverse("panel:work_result", args=[it["kind"], work_id])
    return items


def _back(request, default):
    """Амалдан кейин қайтиш: ўз сайтимиздаги саҳифа (фойдаланувчилар рўйхати ёки профил)."""
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect(default)


def _last_sessions(user_ids):
    """{user_id: охирги сессия} — IP, қурилма, онлайнми."""
    out = {}
    for s in LoginSession.objects.filter(user_id__in=user_ids).order_by("user_id", "-last_seen"):
        out.setdefault(s.user_id, s)
    return out


# ===================== умумий кўриниш =====================
@staff_required
def overview(request):
    now = timezone.now()
    day, week = now - timedelta(days=1), now - timedelta(days=7)
    users = User.objects.all()
    counts = data.counts_by_user()
    names = {u.pk: u for u in users}
    top = sorted(counts.items(), key=lambda kv: kv[1]["jobs"], reverse=True)[:5]
    jobs = Job.objects.all()
    live_ = data.live()
    return render(request, "panel/overview.html", _ctx(
        request, "overview", live=live_, attention=_attention(live_),
        users_total=users.count(),
        users_new=users.filter(date_joined__gte=week).count(),
        users_online=users.filter(last_login__gte=day).count(),
        users_inactive=users.filter(is_active=False).count(),
        works={k: data.top_level(k).count() for k in data.KINDS},
        tn_total=_tn_count(),
        jobs_total=jobs.count(),
        jobs_priced=jobs.filter(status=Job.Status.DONE).count(),
        jobs_failed=jobs.filter(status=Job.Status.FAILED).count(),
        disk=data.disk_total(),
        reg_days=data.per_day(users, "date_joined"),
        upload_days=data.uploads_per_day(),
        top=[{"user": names.get(uid), **c} for uid, c in top if uid in names and c["jobs"]],
        events=Event.objects.select_related("actor", "target")[:12],
    ))


def _tn_count():
    from texnazor.models import SiteObject
    return SiteObject.objects.count()


def _attention(live_) -> list:
    """«Эътибор талаб қилади»: ҳозир тузатиш ёки қараш керак бўлган нарсалар — ҳар бири ҳаволаси билан.
    Бўш рўйхат — ҳаммаси жойида."""
    from texnazor.models import SiteObject
    now = timezone.now()
    out = []
    if live_["workers"]["mode"] == "worker" and not live_["workers"]["alive"]:
        out.append(("rose", "Фондаги ишчи (worker) ишламаяпти — папкалар очилмайди ва нархланмайди",
                    reverse("panel:processes")))
    stalled = sum(1 for p in live_["procs"] if p["stalled"])
    if stalled:
        out.append(("rose", f"{stalled} та папка жараёни тўхтаб қолган (белги келмаяпти)", reverse("panel:processes")))
    n = ServerLog.objects.filter(level__gte=logging.ERROR, created__gte=now - timedelta(days=1)).count()
    if n:
        out.append(("rose", f"Охирги 24 соатда {n} та сервер хатоси", reverse("panel:errors") + "?level=error"))
    n = Job.objects.filter(status=Job.Status.FAILED, created__gte=now - timedelta(days=7)).count()
    if n:
        out.append(("amber", f"Охирги 7 кунда {n} та смета хато билан тугади", reverse("panel:processes")))
    n = SiteObject.objects.filter(status=SiteObject.Status.READY, layout__check__ok=False).count()
    if n:
        out.append(("amber", f"{n} та техназор офёртасини тизим тўлиқ тушунмади", reverse("panel:texnazor") + "?status=unread"))
    since = now - WINDOW
    n = len([r for r in _group(AuthAttempt.objects.filter(kind=AuthAttempt.Kind.LOGIN, created__gte=since), "email")
             if r["n"] >= LOGIN_PER_EMAIL])
    if n:
        out.append(("amber", f"{n} та ҳисоб кўп хато пароль сабабли вақтинча блокда", reverse("panel:log")))
    return [{"color": c, "text": t, "url": u} for c, t, u in out]


@staff_required
def live(request):
    """Умумий саҳифанинг «ҳозир» блоки — ҳар 3 сонияда шу HTML бўлаги билан алмаштирилади."""
    return render(request, "panel/_live.html", _ctx(request, "overview", live=data.live()))


STAT_PERIODS = (7, 30, 90)


@staff_required
def speed(request):
    """Тезлик: файлларни очиш (.xls → .xlsx, ўқиш, таҳлил) ва нархлаш (1000 қаторга неча сония)."""
    days = int(request.GET["days"]) if request.GET.get("days", "").isdigit() else 7
    days = days if days in STAT_PERIODS else 7
    return render(request, "panel/speed.html", _ctx(request, "speed", sp=data.speed(days), days=days,
                                                    periods=STAT_PERIODS, live=data.live()))


@staff_required
def stats(request):
    days = int(request.GET.get("days", 30)) if request.GET.get("days", "").isdigit() else 30
    days = days if days in STAT_PERIODS else 30
    labels = {k: label for cards in STAT_CARDS.values() for k, label, _c in cards}
    return render(request, "panel/stats.html", _ctx(request, "stats", s=data.stats(days), days=days,
                                                    periods=STAT_PERIODS, labels=labels))


# ===================== фойдаланувчилар =====================
@staff_required
def users(request):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    qs = User.objects.order_by("-date_joined")
    if q:
        qs = qs.filter(Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "blocked":
        qs = qs.filter(is_active=False)
    elif status == "staff":
        qs = qs.filter(is_staff=True)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    counts = data.counts_by_user()
    disk = data.disk_by_user()
    tn = _tn_by_user()
    last = _last_sessions([u.pk for u in page])
    online = sessions.online_since()
    rows = [{"user": u, "c": counts.get(u.pk, {}), "tn": tn.get(u.pk, 0), "disk": disk.get(u.pk, 0), "s": last.get(u.pk),
             "online": bool(last.get(u.pk) and not last[u.pk].ended and last[u.pk].last_seen >= online)} for u in page]
    return render(request, "panel/users.html", _ctx(request, "users", page=page, rows=rows, q=q, status=status,
                                                    total=User.objects.count(), here=request.get_full_path()))


def _tn_by_user() -> dict:
    """{owner_id: техназор объектлари сони}"""
    from texnazor.models import SiteObject
    return dict(SiteObject.objects.values("owner_id").annotate(n=Count("id")).values_list("owner_id", "n"))


USER_TABS = (("works", "Ишлари"), ("files", "Юклаган файллари"), ("access", "Кириш ва рухсат"), ("events", "Ҳаракатлари"))


@staff_required
def user_detail(request, pk):
    """Битта фойдаланувчи: юқорида — ким ва қисқа кўрсаткичлар; пастда — бўлимлар (таб): ишлари, файллари,
    кириш (қурилма, IP, блок, диск чегараси), ҳаракатлари. Фақат танланган бўлим ҳисобланади."""
    u = get_object_or_404(User, pk=pk)
    tab = request.GET.get("tab", "works")
    tab = tab if tab in dict(USER_TABS) else "works"
    since = timezone.now() - WINDOW
    ctx = dict(u=u, utab=tab, utabs=USER_TABS, c=data.counts_by_user().get(u.pk, {}),
               disk=data.disk_by_user().get(u.pk, 0), tn_n=_tn_by_user().get(u.pk, 0),
               locked=AuthAttempt.objects.filter(kind=AuthAttempt.Kind.LOGIN, email=u.email,
                                                 created__gte=since).count() >= LOGIN_PER_EMAIL,
               online=_last_sessions([u.pk]).get(u.pk))
    s = ctx["online"]
    ctx["is_online"] = bool(s and not s.ended and s.last_seen >= sessions.online_since())
    if tab == "works":
        ctx.update(items=_panel_items(u), tn=_tn_rows(u))
    elif tab == "files":
        files = []
        for kind in data.KINDS:
            qs = data.top_level(kind).filter(owner=u)
            for w in (qs.defer("result") if kind == "smeta" else qs):
                for i, f in enumerate(data.originals(kind, w)):
                    files.append({**f, "i": i, "kind": kind, "work": w, "created": w.created})
        files.sort(key=lambda f: f["created"], reverse=True)
        ctx.update(files=files[:300], files_total=len(files), files_size=sum(f["size"] or 0 for f in files))
    elif tab == "access":
        ctx.update(sessions=u.login_sessions.all()[:30], online_since=sessions.online_since(),
                   blocked_ips=sessions.blocked_ips(), my_ip=client_ip(request), disk_info=_disk_info(u),
                   ips=list(u.login_sessions.values_list("ip").annotate(n=Count("id")).order_by("-n")[:10]))
    else:
        ctx["events"] = Event.objects.filter(Q(actor=u) | Q(target=u) | Q(email=u.email)).select_related("actor", "target")[:100]
    return render(request, "panel/user.html", _ctx(request, "users", **ctx))


def _disk_info(u):
    from .views_settings import user_disk
    return user_disk(u)


def _tn_rows(u):
    from texnazor.models import SiteObject
    from .views_tn import object_rows
    return object_rows(SiteObject.objects.filter(owner=u).order_by("-created"))


USER_FLAGS = {"is_active": ("фаол", "блокланди"), "is_staff": ("панелга рухсат берилди", "панел рухсати олинди")}


@require_POST
@superuser_required
def user_toggle(request, pk, field):
    """Блоклаш / блокдан чиқариш, панелга рухсат. Ўзини ва бошқа суперфойдаланувчини ўзгартириб бўлмайди."""
    u = get_object_or_404(User, pk=pk)
    if field not in USER_FLAGS:
        raise Http404
    if u.pk == request.user.pk or u.is_superuser:
        raise PermissionDenied("Ўзингизни ёки суперфойдаланувчини ўзгартириб бўлмайди")
    value = not getattr(u, field)
    setattr(u, field, value)
    u.save(update_fields=[field])  # блокланган фойдаланувчи кейинги сўровдаёқ тизимдан чиқади
    on, off = USER_FLAGS[field]
    audit(request, Event.Kind.ADMIN_USER, target=u, detail=f"{u.email}: {on if value else off}")
    if field == "is_active" and value:
        AuthAttempt.objects.filter(kind=AuthAttempt.Kind.LOGIN, email=u.email).delete()
    if field == "is_active" and not value:
        sessions.revoke_all(u, "blocked")  # очиқ сессиялари дарҳол ёпилади
    return _back(request, reverse("panel:user", args=[u.pk]))


@require_POST
@superuser_required
def session_revoke(request, pk, sid=None):
    """Битта сессияни (ёки ``sid`` бўлмаса — ҳаммасини) тугатиш: фойдаланувчи тизимдан чиқади."""
    u = get_object_or_404(User, pk=pk)
    if u.pk == request.user.pk and sid is None:
        raise PermissionDenied("Ўзингизнинг барча сессияларингизни бу ердан тугатиб бўлмайди")
    if sid is None:
        n = sessions.revoke_all(u)
        audit(request, Event.Kind.ADMIN_SESSION, target=u, detail=f"{u.email}: барча сессиялар ({n})")
    else:
        s = get_object_or_404(LoginSession, pk=sid, user=u)
        sessions.revoke(s)
        audit(request, Event.Kind.ADMIN_SESSION, target=u, detail=f"{u.email}: {s.ip} · {s.browser} · {s.os}")
    return _back(request, reverse("panel:user", args=[u.pk]))


@require_POST
@superuser_required
def ip_block(request):
    """IP'ни блоклаш / блокдан чиқариш. Ўзингиз турган IP'ни блоклаб бўлмайди (панелдан қулфланиб қоласиз)."""
    ip = request.POST.get("ip", "").strip()
    try:
        from ipaddress import ip_address
        ip = str(ip_address(ip))
    except ValueError:
        raise Http404("Нотўғри IP")
    target = User.objects.filter(pk=request.POST.get("user")).first() if request.POST.get("user", "").isdigit() else None
    if request.POST.get("action") == "unblock":
        BlockedIP.objects.filter(ip=ip).delete()
        detail = f"{ip}: блокдан чиқарилди"
    else:
        if ip == client_ip(request):
            raise PermissionDenied("Ўзингиз турган IP'ни блоклаб бўлмайди")
        BlockedIP.objects.get_or_create(ip=ip, defaults={"created_by": request.user,
                                                         "reason": request.POST.get("reason", "")[:200]})
        # шу IP'дан очилган сессиялар ҳам ёпилади
        for s in LoginSession.objects.filter(ip=ip, ended__isnull=True):
            sessions.revoke(s, "blocked")
        detail = f"{ip}: блокланди"
    sessions.blocked_changed()
    audit(request, Event.Kind.ADMIN_IP, target=target, detail=detail)
    return _back(request, reverse("panel:log"))


@require_POST
@superuser_required
def user_unlock(request, pk):
    """Кўп хато кириш сабабли вақтинча блокни олиб ташлаш."""
    u = get_object_or_404(User, pk=pk)
    AuthAttempt.objects.filter(kind=AuthAttempt.Kind.LOGIN, email=u.email).delete()
    audit(request, Event.Kind.ADMIN_USER, target=u, detail=f"{u.email}: кириш блоки олиб ташланди")
    return redirect("panel:user", pk=u.pk)


# ===================== битта иш =====================
def _work_or_404(kind, work_id):
    if kind not in data.KINDS:
        raise Http404
    try:
        return data.get_work(kind, work_id)
    except ObjectDoesNotExist:
        raise Http404


KIND_HINTS = {
    "folder": "Папка — смета­чи бутун папка ёки ZIP/RAR архив юклаган; ҳар бир ички папка алоҳида қисм бўлиб нархланади.",
    "object": "Объект — бир нечта Excel файл бирга юкланган; тизим локал ва ресурс листларини жуфтлаб, ҳар жуфтликни смета қилади.",
    "smeta": "Смета — битта локал смета ва унинг ресурс файли (ёки ресурс базасидан) нархланган.",
}


def _light(qs):
    """Иш саҳифасидаги сметалар рўйхати учун — катта ``result`` JSON'сиз."""
    return qs.only("id", "created", "status", "owner_id", "batch_id", "batch_order", "local_name",
                   "local_sheet", "batch_label", "error", "res_path", *Job.SUMMARY_FIELDS)


@staff_required
def work(request, kind, work_id):
    """Битта иш: нима юкланган (асл файллар — кўриш ва юклаб олиш, ҳаммаси битта ZIP), нима чиққан
    (нархланган сметалар ва натижа), шу иш бўйича журнал."""
    w = _work_or_404(kind, work_id)
    files = data.originals(kind, w)
    for f, src in zip(files, xl.sources(kind, w, files)):
        f["viewable"] = src is not None
    ctx = {"kind": kind, "kind_label": data.KIND_LABELS[kind], "kind_hint": KIND_HINTS[kind], "w": w, "files": files,
           "files_ready": sum(1 for f in files if f["abs"]), "files_size": sum(f["size"] or 0 for f in files if f["abs"])}
    if kind == "folder":
        parts = list(w.parts.prefetch_related(Prefetch("jobs", _light(Job.objects))).order_by("folder"))
        ctx["parts"] = [{"p": p, "state": PART_STATES.get(p.stage, (p.get_stage_display(), "slate")),
                         "jobs": list(p.jobs.all()), "total": _job_sums(list(p.jobs.all()))[0]} for p in parts]
        ctx["total"], ctx["not_priced"] = _job_sums([j for x in ctx["parts"] for j in x["jobs"]])
        ctx["skipped"] = sum(1 for f in w.files if f.get("skip"))
        ctx["working"] = w.status == Project.Status.PROCESSING
        ctx["has_result"] = any(x["jobs"] for x in ctx["parts"])
        ctx["n_jobs"] = sum(len(x["jobs"]) for x in ctx["parts"])
    elif kind == "object":
        ctx["jobs"] = list(_light(w.jobs.all()))
        ctx["total"], ctx["not_priced"] = _job_sums(ctx["jobs"])
        ctx["has_result"] = any(j.status == Job.Status.DONE for j in ctx["jobs"])
        ctx["n_jobs"] = len(ctx["jobs"])
    else:
        ctx["total"], ctx["not_priced"] = _job_sums([w]) if w.status == Job.Status.DONE else (0, 0)
        ctx["stats"] = {k: v for k, v in (w.result.get("stats") or {}).items() if v}
        ctx["has_result"] = w.status == Job.Status.DONE and bool(w.output_path)
        ctx["n_jobs"] = 1
    ctx["events"] = Event.objects.filter(work_id=w.pk).select_related("actor")[:30]
    from .views_base import work_base_ctx
    ctx.update(work_base_ctx(kind, w))
    return render(request, "panel/work.html", _ctx(request, "users", **ctx))


@superuser_required
def work_file(request, kind, work_id, i):
    """Асл файлни юклаб олиш (журналга ёзилади)."""
    w = _work_or_404(kind, work_id)
    files = data.originals(kind, w)
    if not 0 <= i < len(files) or files[i]["abs"] is None:
        raise Http404("Файл топилмади")
    f = files[i]
    _audit_download(request, w, kind, f["path"], what="асл файл", force=True)
    return FileResponse(open(f["abs"], "rb"), as_attachment=True, filename=f["name"])


@superuser_required
def work_zip(request, kind, work_id):
    """Ҳамма асл файллар битта ZIP'да — папка (ёки ZIP/RAR) юклаган бўлса, ичидаги тузилиши билан (журналга ёзилади)."""
    w = _work_or_404(kind, work_id)
    tmp, n = xl.originals_zip(kind, w, data.originals(kind, w))
    if not n:
        tmp.close()
        raise Http404("Дискда асл файл қолмаган")
    name = f"{(w.local_name.rsplit('.', 1)[0] if kind == 'smeta' else w.name) or 'иш'}_асл.zip"
    _audit_download(request, w, kind, f"{name} ({n} та файл)", what="асл файллар (ZIP)", force=True)
    return FileResponse(tmp, as_attachment=True, filename=name, content_type="application/zip")


@superuser_required
def work_sheet(request, kind, work_id, i):
    """Асл файлни Excel кўринишида очиш (юклаб олмасдан). Кўрилгани журналга ёзилади."""
    w = _work_or_404(kind, work_id)
    files = data.originals(kind, w)
    if not 0 <= i < len(files):
        raise Http404("Файл топилмади")
    f, src = files[i], xl.sources(kind, w, files)[i]
    ctx = {"kind": kind, "w": w, "title": f["path"], "what": "Юкланган асл файл",
           "back": reverse("panel:work", args=[kind, w.pk]),
           "download": reverse("panel:work_file", args=[kind, w.pk, i]) if f["abs"] else None,
           "full": request.GET.get("all") == "1"}
    if src is None:
        ctx["error"] = ("Бу файлни кўрсатиб бўлмайди: у ҳали очилмаган (.xls фонда .xlsx'га ўгирилгач кўринади) "
                        "ёки дискда йўқ." + (" Юклаб олиб Excel'да очинг." if f["abs"] else ""))
    else:
        try:
            ctx["data"] = xl.render(*src, sheet=request.GET.get("s") or None, full=ctx["full"])
        except Exception as e:  # noqa: BLE001 — бузуқ файл саҳифани йиқитмасин
            ctx["error"] = f"Файлни очиб бўлмади: {type(e).__name__}: {e}"
    _audit_view(request, w, kind, f"асл файл кўрилди: {f['path']}")
    return render(request, "panel/sheet.html", _ctx(request, "users", **ctx))


JOB_FILES = {"out": "Нархланган натижа", "local": "Юкланган локал смета", "res": "Ресурс файли"}


def _job_top(job):
    """Смета қайси ишнинг ичида: (kind, юқори иш)."""
    if job.batch_id is None:
        return "smeta", job
    if job.batch.project_id is None:
        return "object", job.batch
    return "folder", job.batch.project


@superuser_required
def job_sheet(request, job_id, f):
    """Сметанинг (алоҳида, объект ёки папка ичидаги) натижаси / локали / ресурси — Excel кўринишида."""
    job = get_object_or_404(Job.objects.select_related("owner", "batch__project__owner", "batch__owner"), pk=job_id)
    if f not in JOB_FILES:
        raise Http404
    kind, top = _job_top(job)
    full = request.GET.get("all") == "1"
    ctx = {"kind": kind, "w": top, "job": job, "title": job.batch_label or job.local_name, "what": JOB_FILES[f],
           "back": reverse("panel:work", args=[kind, top.pk]), "full": full, "job_files": JOB_FILES, "jf": f,
           "download": reverse("panel:job_download", args=[job.pk]) if f == "out" and job.status == Job.Status.DONE else None}
    try:
        d = preview.sheet_data(job, f, request.GET.get("s") or None)
        ctx["data"] = d if full else preview.head(d, xl.VIEW_ROWS)
    except preview.NotAvailable as e:
        ctx["error"] = str(e)
    except Exception as e:  # noqa: BLE001
        ctx["error"] = f"Файлни очиб бўлмади: {type(e).__name__}: {e}"
    _audit_view(request, top, kind, f"{JOB_FILES[f].lower()} кўрилди: {job.local_name}")
    return render(request, "panel/sheet.html", _ctx(request, "users", **ctx))


@superuser_required
def job_download(request, job_id):
    """Объект ёки папка ичидаги битта сметанинг натижаси (журналга ёзилади)."""
    job = get_object_or_404(Job.objects.select_related("owner"), pk=job_id)
    path = job.abs(job.output_path) if job.output_path else None
    if job.status != Job.Status.DONE or not path or not path.is_file():
        raise Http404("Натижа йўқ")
    _audit_download(request, job, "smeta", job.download_name, force=True)
    return FileResponse(open(path, "rb"), as_attachment=True, filename=job.download_name)


def _audit_view(request, w, kind, detail):
    """Кўриш журнали: битта файлни 10 дақиқа ичида қайта очиш (лист алмаштириш) — битта ёзув."""
    from django.core.cache import cache
    if cache.add(f"panel:viewed:{request.user.pk}:{w.pk}:{hash(detail)}", 1, 600):
        audit(request, Event.Kind.ADMIN_VIEW, target=w.owner, work=w, work_kind=kind, detail=detail)


@superuser_required
def work_result(request, kind, work_id):
    """Натижани юклаб олиш (журналга ёзилади)."""
    w = _work_or_404(kind, work_id)
    try:
        if kind == "folder":
            path, name = build_project_zip(w), None
        elif kind == "object":
            path, name = build_combined(w), None
        else:
            if w.status != Job.Status.DONE or not w.output_path or not w.abs(w.output_path).is_file():
                raise Http404("Натижа йўқ")
            path, name = w.abs(w.output_path), w.download_name
    except UploadError as e:
        raise Http404(str(e))
    _audit_download(request, w, kind, path.name, force=True)
    return FileResponse(open(path, "rb"), as_attachment=True, filename=name or path.name)


@require_POST
@superuser_required
def work_stop(request, work_id):
    project = get_object_or_404(Project, id=work_id)
    stop_project(project)
    audit(request, Event.Kind.ADMIN_STOP, target=project.owner, work=project, work_kind="folder", detail=project.name)
    if request.POST.get("back") == "processes":
        return redirect("panel:processes")
    return redirect("panel:work", kind="folder", work_id=project.id)


# ===================== жараёнлар =====================
@staff_required
def processes(request):
    procs = data.processes()
    day = timezone.now() - timedelta(days=1)
    recent_failed = (Job.objects.filter(status=Job.Status.FAILED, created__gte=timezone.now() - timedelta(days=7))
                     .select_related("owner", "batch").defer("result")[:30])
    failed_parts = (Batch.objects.filter(stage=Batch.Stage.FAILED, project__isnull=False)
                    .select_related("owner", "project").order_by("-created")[:30])
    finished = (Project.objects.filter(status__in=[Project.Status.DONE, Project.Status.READY], created__gte=day)
                .select_related("owner")[:20])
    return render(request, "panel/processes.html", _ctx(
        request, "processes", procs=procs, workers=data.workers(), recent_failed=recent_failed, failed_parts=failed_parts, finished=finished,
        stale_minutes=data.stalled_after_minutes(), any_alive=any(p["alive"] for p in procs)))


# ===================== файллар =====================
@staff_required
def files(request):
    q = request.GET.get("q", "").strip().lower()
    owner = request.GET.get("user", "")
    rows = []
    for kind in data.KINDS:
        qs = data.top_level(kind).select_related("owner")
        if kind == "smeta":
            qs = qs.defer("result")
        if owner.isdigit():
            qs = qs.filter(owner_id=int(owner))
        for w in qs:
            for i, f in enumerate(data.originals(kind, w)):
                if q and q not in f["path"].lower():
                    continue
                rows.append({**f, "i": i, "kind": kind, "work": w, "created": w.created})
    rows.sort(key=lambda f: f["created"], reverse=True)
    page = Paginator(rows, 100).get_page(request.GET.get("page"))
    disk = data.disk_by_user()
    users_ = {u.pk: u for u in User.objects.filter(pk__in=disk.keys())}
    by_user = sorted(({"user": users_[k], "size": v} for k, v in disk.items() if k in users_),
                      key=lambda r: r["size"], reverse=True)
    return render(request, "panel/files.html", _ctx(
        request, "files", page=page, q=request.GET.get("q", ""), owner=owner, total=len(rows),
        total_size=sum(r["size"] or 0 for r in rows), disk=data.disk_total(), by_user=by_user,
        all_users=User.objects.order_by("email")))


# ===================== журнал ва хавфсизлик =====================
@staff_required
def log(request):
    kind = request.GET.get("kind", "")
    q = request.GET.get("q", "").strip()
    qs = Event.objects.select_related("actor", "target")
    if kind == "security":
        qs = qs.filter(kind__in=[Event.Kind.LOGIN_FAILED, Event.Kind.REGISTER])
    elif kind == "admin":
        qs = qs.filter(kind__startswith="admin_")
    elif kind in Event.Kind.values:
        qs = qs.filter(kind=kind)
    if q:
        qs = qs.filter(Q(actor__email__icontains=q) | Q(target__email__icontains=q) | Q(email__icontains=q)
                       | Q(ip__icontains=q) | Q(detail__icontains=q))
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    since = timezone.now() - WINDOW
    recent = AuthAttempt.objects.filter(kind=AuthAttempt.Kind.LOGIN, created__gte=since)
    locked_emails = [r for r in _group(recent, "email") if r["n"] >= LOGIN_PER_EMAIL]
    locked_ips = [r for r in _group(recent, "ip") if r["n"] >= LOGIN_PER_IP]
    day = timezone.now() - timedelta(days=1)
    top_ips = _group(Event.objects.filter(kind=Event.Kind.LOGIN_FAILED, created__gte=day), "ip")[:8]
    return render(request, "panel/log.html", _ctx(
        request, "log", page=page, kind=kind, q=q, kinds=Event.Kind.choices, locked_emails=locked_emails,
        locked_ips=locked_ips, top_ips=top_ips, window_minutes=int(WINDOW.total_seconds() // 60),
        blocked=BlockedIP.objects.select_related("created_by"), my_ip=client_ip(request),
        here=request.get_full_path()))


ERROR_LEVELS = [("", "Ҳаммаси"), ("error", "Хатолар"), ("warning", "Огоҳлантиришлар")]


@staff_required
def errors(request):
    """Сервер хатолари (logging → ServerLog): сайт ва worker; traceback билан."""
    level = request.GET.get("level", "")
    q = request.GET.get("q", "").strip()
    qs = ServerLog.objects.select_related("user")
    if level == "error":
        qs = qs.filter(level__gte=logging.ERROR)
    elif level == "warning":
        qs = qs.filter(level=logging.WARNING)
    if q:
        qs = qs.filter(Q(message__icontains=q) | Q(logger__icontains=q) | Q(path__icontains=q) | Q(traceback__icontains=q))
    day = timezone.now() - timedelta(days=1)
    recent = ServerLog.objects.filter(created__gte=day)
    from django.conf import settings
    return render(request, "panel/errors.html", _ctx(
        request, "errors", page=Paginator(qs, 50).get_page(request.GET.get("page")), level=level, q=q,
        levels=ERROR_LEVELS, keep_days=KEEP_DAYS, total=ServerLog.objects.count(),
        day_errors=recent.filter(level__gte=logging.ERROR).count(),
        day_warnings=recent.filter(level=logging.WARNING).count(),
        log_file=str(settings.LOG_DIR / "narxlash.log")))


@require_POST
@superuser_required
def errors_test(request):
    """Logging ishlayotganini tekshirish: bitta «Хато» yozuvi (panel va faylga)."""
    logging.getLogger("panel").error("Синов хатоси: %s панелдан текширди", request.user.email)
    audit(request, Event.Kind.ADMIN_LOGS, detail="синов хатоси ёзилди")
    return redirect("panel:errors")


@require_POST
@superuser_required
def errors_clear(request):
    n, _ = ServerLog.objects.all().delete()
    audit(request, Event.Kind.ADMIN_LOGS, detail=f"хатолар тозаланди ({n} та)")
    return redirect("panel:errors")


def _group(qs, field):
    """[{"key": қиймат, "n": сони}] — кўпидан озига."""
    rows = (qs.exclude(**{f"{field}__isnull": True}).exclude(**{field: ""})
            .values_list(field).annotate(n=Count("id")).order_by("-n"))
    return [{"key": k, "n": n} for k, n in rows]
