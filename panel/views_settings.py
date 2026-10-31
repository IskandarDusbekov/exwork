"""Панел: созламалар (чекловлар, рўйхатдан ўтиш, синовдаги бўлимлар), фойдаланувчининг диск чегараси, захира нусха.
Кўриш — ``is_staff``; ўзгартириш ва юклаб олиш — суперфойдаланувчи. Ҳаммаси журналга ёзилади.
База нусхасини юклаб олиш — яна бир марта иловадаги код (ёки пароль) билан: ўғирланган сессия билан бутун
базани олиб кетиб бўлмасин."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts import totp
from accounts.models import TwoFactor

from . import backup, conf
from .audit import log as audit
from .data import dir_size
from .models import Event
from .views import _ctx, staff_required, superuser_required

User = get_user_model()


def _fmt(f, v):
    if f.kind == "bool":
        return "ҳа" if v else "йўқ"
    if f.kind == "choice":
        return dict(f.choices).get(v, v)
    return f"{v} {f.unit}".strip()


@staff_required
def settings_page(request):
    groups = {}
    for f in conf.FIELDS:
        groups.setdefault(f.group, []).append({"f": f, "value": conf.shown(f.key), "default": conf.default(f.key),
                                              "changed": conf.shown(f.key) != conf.default(f.key)})
    from django.db import connection
    fixed = [  # фақат серверда (.env) — панелдан ўзгармайди
        ("Икки босқичли кириш (панел / админ)", "ёқилган" if settings.NARXLASH_STAFF_2FA else "ЎЧИРИЛГАН", not settings.NARXLASH_STAFF_2FA,
         "NARXLASH_STAFF_2FA"),
        ("Ишлаб чиқиш режими (DEBUG)", "ёқилган" if settings.DEBUG else "ўчирилган", settings.DEBUG, "DJANGO_DEBUG"),
        ("База", connection.vendor, connection.vendor != "postgresql" and not settings.DEBUG, "DATABASE_URL"),
        ("Фондаги ишлар", "алоҳида worker" if settings.NARXLASH_TASKS == "worker" else "сайт ичида", False, "NARXLASH_TASKS"),
        ("Хат юбориш (email, паролни тиклаш)", "созланган" if settings.NARXLASH_EMAIL_ON else "созланмаган — хатлар кетмайди",
         not settings.NARXLASH_EMAIL_ON and not settings.DEBUG, "EMAIL_HOST"),
        ("Сессия муддати", f"{settings.SESSION_COOKIE_AGE // 86400} кун", False, "—"),
    ]
    return render(request, "panel/settings.html", _ctx(request, "settings", groups=groups, fixed=fixed))


@require_POST
@superuser_required
def settings_save(request):
    try:
        changes = {f.key: conf.parse(f, request.POST.get(f.key, "0" if f.kind == "bool" else "")) for f in conf.FIELDS}
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("panel:settings")
    done = conf.save(changes, request.user)
    for label, old, new in done:
        f = next(x for x in conf.FIELDS if x.label == label)
        audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"{label}: {_fmt(f, old)} → {_fmt(f, new)}")
    messages.success(request, f"Сақланди ({len(done)} та ўзгариш)" if done else "Ўзгариш йўқ")
    return redirect("panel:settings")


@require_POST
@superuser_required
def user_quota(request, pk):
    """Фойдаланувчининг диск чегараси: бўш — умумий созлама, 0 — чексиз."""
    u = get_object_or_404(User, pk=pk)
    raw = request.POST.get("quota_mb", "").replace(" ", "").strip()
    if raw and not raw.isdigit():
        messages.error(request, "Диск чегараси — бутун сон (МБ) ёки бўш")
        return redirect("panel:user", pk=u.pk)
    old = u.quota_mb
    u.quota_mb = int(raw) if raw else None
    u.save(update_fields=["quota_mb"])
    text = lambda v: "умумий" if v is None else ("чексиз" if v == 0 else f"{v} МБ")  # noqa: E731
    audit(request, Event.Kind.ADMIN_USER, target=u, detail=f"{u.email}: диск чегараси {text(old)} → {text(u.quota_mb)}")
    messages.success(request, "Диск чегараси сақланди")
    return redirect("panel:user", pk=u.pk)


def user_disk(u) -> dict:
    """Фойдаланувчи саҳифаси учун: ишлатилган / чегара (нархлаш + техназор)."""
    from jobs.models import Batch, Job, Project
    from texnazor.models import MyOfyorta, SiteObject
    used = sum(dir_size(o.dir) for m in (Project, Batch, Job, SiteObject, MyOfyorta) for o in m.objects.filter(owner=u).only("id"))
    limit = conf.user_quota(u)
    return {"used": used, "limit": limit, "pct": min(100, 100 * used / limit) if limit else 0, "own": u.quota_mb,
            "default": conf.get("user_quota_mb")}


# ===================== захира нусха =====================
@staff_required
def backups(request):
    return render(request, "panel/backups.html", _ctx(request, "backups", b=backup.summary(),
                                                      has_2fa=TwoFactor.objects.filter(user=request.user).exists()))


@require_POST
@superuser_required
def backup_make(request):
    if backup.start(request.user):
        audit(request, Event.Kind.ADMIN_BACKUP, detail="база нусхаси олинмоқда (панелдан)")
        messages.success(request, "База нусхаси олинмоқда — бир неча дақиқа. Саҳифани янгиланг.")
    else:
        messages.error(request, "Аввалги нусха ҳали тугамади")
    return redirect("panel:backups")


def _second_factor(request) -> bool:
    """Иловадаги код (икки босқичли кириш ёқилган бўлса) ёки пароль."""
    secret = request.POST.get("code", "")
    tf = TwoFactor.objects.filter(user=request.user).first()
    if tf:
        step = totp.verify(tf.secret, secret, tf.last_step)
        return bool(step and TwoFactor.objects.filter(pk=tf.pk, last_step__lt=step).update(last_step=step))
    return request.user.check_password(secret)


@require_POST
@superuser_required
def backup_download(request, name):
    path = backup.path_of(name)
    if path is None:
        raise Http404("Нусха топилмади")
    if not _second_factor(request):
        audit(request, Event.Kind.ADMIN_BACKUP, detail=f"база нусхаси: код нотўғри ({name})")
        messages.error(request, "Код нотўғри — нусха берилмади")
        return redirect("panel:backups")
    audit(request, Event.Kind.ADMIN_DOWNLOAD, detail=f"база нусхаси: {name}")
    return FileResponse(open(path, "rb"), as_attachment=True, filename=name)
