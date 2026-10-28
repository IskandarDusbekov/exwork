"""Бошқарув панели (Django admin'дан алоҳида). Кўриш — ``is_staff``; ўзгартириш ва файл юклаб олиш — фақат
``is_superuser``. Панелдаги ҳар бир ўзгартириш ва юклаб олиш журналга ёзилади."""
from datetime import timedelta
from functools import wraps

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.models import AuthAttempt
from accounts.throttle import LOGIN_PER_EMAIL, LOGIN_PER_IP, WINDOW, client_ip
from jobs.folders import build_project_zip, stop_project
from jobs.models import Batch, Job, Project
from jobs.services import UploadError, build_combined
from jobs.views import PART_STATES, STAT_CARDS, _job_sums, work_items

from . import data, sessions
from .audit import log as audit
from .models import BlockedIP, Event, LoginSession

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
    return {"nav": "panel", "tab": tab, "can_act": request.user.is_superuser,
            "running_count": Project.objects.filter(status=Project.Status.PROCESSING).count(), **kw}


def _panel_items(user):
    """Фойдаланувчининг ишлари (дашборддаги каби), лекин ҳаволалар панелдаги кўринишга."""
    items = work_items(user)
    download = {"folder": "project_download", "object": "batch_download", "smeta": "job_download"}
    for it in items:
        work_id = it["url"].rstrip("/").rsplit("/", 1)[-1]
        it["view_url"] = it["url"]  # смета­чининг ўз саҳифаси (суперфойдаланувчига — текширув режимида)
        it["url"] = reverse("panel:work", args=[it["kind"], work_id])
        if it["total"] or it["state"] in ("done", "warn"):
            it["download_url"] = reverse(download[it["kind"]], args=[work_id])
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
    return render(request, "panel/overview.html", _ctx(
        request, "overview", live=data.live(),
        users_total=users.count(),
        users_new=users.filter(date_joined__gte=week).count(),
        users_online=users.filter(last_login__gte=day).count(),
        users_inactive=users.filter(is_active=False).count(),
        works={k: data.top_level(k).count() for k in data.KINDS},
        jobs_total=jobs.count(),
        jobs_priced=jobs.filter(status=Job.Status.DONE).count(),
        jobs_failed=jobs.filter(status=Job.Status.FAILED).count(),
        uploads_today=sum(data.top_level(k).filter(created__gte=day).count() for k in data.KINDS),
        failed_logins=Event.objects.filter(kind=Event.Kind.LOGIN_FAILED, created__gte=day).count(),
        disk=data.disk_total(),
        reg_days=data.per_day(users, "date_joined"),
        upload_days=data.uploads_per_day(),
        top=[{"user": names.get(uid), **c} for uid, c in top if uid in names],
        events=Event.objects.select_related("actor", "target")[:15],
    ))


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
    last = _last_sessions([u.pk for u in page])
    online = sessions.online_since()
    rows = [{"user": u, "c": counts.get(u.pk, {}), "disk": disk.get(u.pk, 0), "s": last.get(u.pk),
             "online": bool(last.get(u.pk) and not last[u.pk].ended and last[u.pk].last_seen >= online)} for u in page]
    return render(request, "panel/users.html", _ctx(request, "users", page=page, rows=rows, q=q, status=status,
                                                    total=User.objects.count(), here=request.get_full_path()))


@staff_required
def user_detail(request, pk):
    u = get_object_or_404(User, pk=pk)
    items = _panel_items(u)
    files = []
    for kind in data.KINDS:
        for w in data.top_level(kind).filter(owner=u).defer(*(["result"] if kind == "smeta" else [])):
            for i, f in enumerate(data.originals(kind, w)):
                files.append({**f, "i": i, "kind": kind, "work": w, "created": w.created})
    files.sort(key=lambda f: f["created"], reverse=True)
    events = Event.objects.filter(Q(actor=u) | Q(target=u) | Q(email=u.email)).select_related("actor", "target")[:50]
    since = timezone.now() - WINDOW
    return render(request, "panel/user.html", _ctx(
        request, "users", u=u, items=items, c=data.counts_by_user().get(u.pk, {}),
        disk=data.disk_by_user().get(u.pk, 0), files=files[:200], files_total=len(files),
        files_size=sum(f["size"] or 0 for f in files), events=events,
        locked=AuthAttempt.objects.filter(kind=AuthAttempt.Kind.LOGIN, email=u.email,
                                          created__gte=since).count() >= LOGIN_PER_EMAIL,
        sessions=u.login_sessions.all()[:30], online_since=sessions.online_since(),
        blocked_ips=sessions.blocked_ips(), my_ip=client_ip(request),
        ips=list(u.login_sessions.values_list("ip").annotate(n=Count("id")).order_by("-n")[:10]),
    ))


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


@staff_required
def work(request, kind, work_id):
    w = _work_or_404(kind, work_id)
    ctx = {"kind": kind, "kind_label": data.KIND_LABELS[kind], "w": w, "files": data.originals(kind, w)}
    if kind == "folder":
        parts = list(w.parts.prefetch_related("jobs").order_by("folder"))
        ctx["parts"] = [{"p": p, "state": PART_STATES.get(p.stage, (p.get_stage_display(), "slate")),
                         "jobs": list(p.jobs.all()), "total": _job_sums(list(p.jobs.all()))[0]} for p in parts]
        ctx["total"], ctx["not_priced"] = _job_sums([j for x in ctx["parts"] for j in x["jobs"]])
        ctx["skipped"] = sum(1 for f in w.files if f.get("skip"))
        ctx["working"] = w.status == Project.Status.PROCESSING
        ctx["has_result"] = any(x["jobs"] for x in ctx["parts"])
    elif kind == "object":
        ctx["jobs"] = list(w.jobs.all())
        ctx["total"], ctx["not_priced"] = _job_sums(ctx["jobs"])
        ctx["has_result"] = any(j.status == Job.Status.DONE for j in ctx["jobs"])
    else:
        ctx["total"], ctx["not_priced"] = _job_sums([w]) if w.status == Job.Status.DONE else (0, 0)
        ctx["stats"] = {k: v for k, v in (w.result.get("stats") or {}).items() if v}
        ctx["has_result"] = w.status == Job.Status.DONE and bool(w.output_path)
    ctx["events"] = Event.objects.filter(work_id=w.pk).select_related("actor")[:30]
    return render(request, "panel/work.html", _ctx(request, "users", **ctx))


@superuser_required
def work_file(request, kind, work_id, i):
    """Асл файлни юклаб олиш (журналга ёзилади)."""
    w = _work_or_404(kind, work_id)
    files = data.originals(kind, w)
    if not 0 <= i < len(files) or files[i]["abs"] is None:
        raise Http404("Файл топилмади")
    f = files[i]
    audit(request, Event.Kind.ADMIN_DOWNLOAD, target=w.owner, work=w, work_kind=kind, detail=f"асл файл: {f['path']}")
    return FileResponse(open(f["abs"], "rb"), as_attachment=True, filename=f["name"])


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
    audit(request, Event.Kind.ADMIN_DOWNLOAD, target=w.owner, work=w, work_kind=kind, detail=f"натижа: {path.name}")
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


def _group(qs, field):
    """[{"key": қиймат, "n": сони}] — кўпидан озига."""
    rows = (qs.exclude(**{f"{field}__isnull": True}).exclude(**{field: ""})
            .values_list(field).annotate(n=Count("id")).order_by("-n"))
    return [{"key": k, "n": n} for k, n in rows]
