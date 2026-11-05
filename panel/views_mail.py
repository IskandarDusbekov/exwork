"""Панел «Почта»: SMTP созламалари (кўриш — ходим, ўзгартириш — суперфойдаланувчи), синов хати, хат кўриниши."""
import re

from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.dateparse import parse_datetime
from django.utils.csp import CSP
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csp import csp_override
from django.views.decorators.http import require_POST

from accounts.emails import reset_message

from . import mail
from .audit import log as audit
from .models import Event
from .views import _ctx, staff_required, superuser_required

HOST_RE = re.compile(r"[A-Za-z0-9.\-]{3,253}")
EMAIL_RE = re.compile(r"[^@\s<>\"]+@[^@\s<>\"]+\.[^@\s<>\"]+")
SAMPLE_CODE = "482913"


def _sample(request):
    base = request.build_absolute_uri("/")
    return reset_message(base, request.user.get_full_name() or request.user.email, SAMPLE_CODE,
                         base.rstrip("/") + "/password/reset/MQ/намуна-ҳавола/")


@staff_required
def mail_page(request):
    cfg = mail.config()
    subject, text, _ = _sample(request)
    return render(request, "panel/mail.html", _ctx(
        request, "mail", cfg=cfg, has_password=bool(cfg["password"]), security=mail.SECURITY, presets=mail.PRESETS,
        sender=mail.from_address() if cfg["source"] != "none" else "", subject=subject, text=text,
        updated=parse_datetime(cfg["updated"]) if cfg["updated"] else None, me=request.user.email))


PREVIEW_CSP = {  # фақат панелнинг ўзи iframe'га олади; хатда скрипт йўқ
    "default-src": [CSP.NONE],
    "img-src": [CSP.SELF, "data:"],
    "style-src": [CSP.UNSAFE_INLINE],
    "frame-ancestors": [CSP.SELF],
}


@staff_required
@xframe_options_sameorigin
@csp_override(PREVIEW_CSP)
def mail_preview(request):
    """Хатнинг HTML кўриниши (панелдаги iframe учун)."""
    return HttpResponse(_sample(request)[2])


def _clean(post):
    host = post.get("host", "").strip()
    if host and not HOST_RE.fullmatch(host):
        raise ValueError("Сервер номи нотўғри (масалан: smtp.gmail.com)")
    try:
        port = int(post.get("port", "") or 0)
    except ValueError:
        raise ValueError("Порт — рақам (587 ёки 465)")
    if host and not 1 <= port <= 65535:
        raise ValueError("Порт 1–65535 оралиғида бўлсин")
    security = post.get("security", "tls")
    if security not in dict(mail.SECURITY):
        raise ValueError("Шифрлаш тури нотўғри")
    username = post.get("username", "").strip()
    if len(username) > 254:
        raise ValueError("Логин жуда узун")
    from_email = post.get("from_email", "").strip()
    if from_email and not EMAIL_RE.fullmatch(from_email):
        raise ValueError("«Кимдан» манзили email бўлсин (масалан: noreply@mhisobot.uz)")
    from_name = " ".join(post.get("from_name", "").split())[:60].replace('"', "").replace("<", "").replace(">", "")
    password = post.get("password", "")
    if len(password) > 200 or "\n" in password:
        raise ValueError("Пароль нотўғри")
    return {"host": host, "port": port, "security": security, "username": username, "from_email": from_email,
            "from_name": from_name}, password.replace(" ", "") if post.get("strip_spaces") else password


@require_POST
@superuser_required
def mail_save(request):
    if request.POST.get("action") == "env":
        mail.reset_to_env()
        audit(request, Event.Kind.ADMIN_SETTINGS, detail="Почта: панел созламалари ўчирилди (.env ишлатилади)")
        messages.success(request, "Панелдаги созламалар ўчирилди — энди сервердаги .env ишлатилади.")
        return redirect("panel:mail")
    try:
        data, password = _clean(request.POST)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("panel:mail")
    if not data["host"]:
        messages.error(request, "SMTP сервер номини киритинг (масалан: smtp.gmail.com)")
        return redirect("panel:mail")
    clear = request.POST.get("clear_password") == "1"
    mail.save(data, request.user, new_password=password or None, clear_password=clear)
    what = "пароль ўзгартирилди" if password else ("пароль ўчирилди" if clear else "пароль ўзгармади")
    audit(request, Event.Kind.ADMIN_SETTINGS,
          detail=f"Почта: {data['host']}:{data['port']} ({data['security']}), логин {data['username'] or '—'}, {what}")
    messages.success(request, "Сақланди. Энди «Синов хати» билан текшириб кўринг.")
    return redirect("panel:mail")


@require_POST
@superuser_required
def mail_test(request):
    to = request.POST.get("to", "").strip() or request.user.email
    if not EMAIL_RE.fullmatch(to):
        messages.error(request, "Синов хати учун тўғри email киритинг")
        return redirect("panel:mail")
    if not mail.configured():
        messages.error(request, "SMTP сервер созланмаган — аввал сақланг")
        return redirect("panel:mail")
    subject, text, html = _sample(request)
    msg = EmailMultiAlternatives("[синов] " + subject, text, None, [to])
    msg.attach_alternative(html, "text/html")
    try:
        msg.send(fail_silently=False)
    except Exception as e:  # noqa: BLE001 — SMTP хатосини ўзини кўрсатамиз
        reason = _explain(e)
        audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Почта: синов хати кетмади ({to}): {type(e).__name__}")
        messages.error(request, f"Хат кетмади: {reason}")
        return redirect("panel:mail")
    audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Почта: синов хати юборилди ({to})")
    messages.success(request, f"Синов хати {to} га юборилди. Келмаса — «Спам» папкасини текширинг.")
    return redirect("panel:mail")


def _explain(e) -> str:
    """SMTP хатосини тушунарли қилиш."""
    import smtplib
    import socket
    text = str(e)[:300]
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return ("логин ёки пароль қабул қилинмади. Gmail / Yandex / Mail.ru да оддий пароль эмас, «илова пароли» "
                f"(app password) керак. Сервер жавоби: {text}")
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "серверга уланиб бўлмади (вақт тугади) — порт ва шифрлаш турини текширинг (587 — STARTTLS, 465 — SSL)"
    if isinstance(e, (ConnectionRefusedError, socket.gaierror)):
        return f"сервер топилмади ёки уланишни рад этди — сервер номи ва портни текширинг ({text})"
    if isinstance(e, smtplib.SMTPSenderRefused):
        return f"«Кимдан» манзили рад этилди — у логин билан бир хил бўлсин ({text})"
    if "WRONG_VERSION_NUMBER" in text or "wrong version" in text.lower():
        return "шифрлаш тури портга мос эмас: 465 — SSL, 587 — STARTTLS"
    return text or type(e).__name__
