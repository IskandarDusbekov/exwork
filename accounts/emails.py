"""Фойдаланувчига юбориладиган хатлар: матн (оддий) ва HTML (катта, аниқ код ва тугма) — бир хил мазмун.
Панел «Почта» бўлимидаги кўриниш ҳам шу функцияни ишлатади."""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.templatetags.static import static

SUBJECT = "mhisobot: паролни тиклаш коди {code}"


def pretty_code(code: str) -> str:
    """482913 → «482 913» — кўз билан ўқиш ва териш осон."""
    return f"{code[:3]} {code[3:]}" if len(code) == 6 else code


def reset_message(base_url: str, name: str, code: str, link: str) -> tuple:
    """(мавзу, матн, html). ``base_url`` — https://mhisobot.uz (расм ва ҳаволалар учун)."""
    ctx = {"name": name, "code": code, "code_pretty": pretty_code(code), "link": link,
           "hours": settings.PASSWORD_RESET_TIMEOUT // 3600, "minutes": settings.PASSWORD_RESET_CODE_MINUTES,
           "site": base_url.split("://", 1)[-1].rstrip("/"), "base": base_url.rstrip("/"),
           "logo": base_url.rstrip("/") + static("logo-512.png")}
    return (SUBJECT.format(code=pretty_code(code)), render_to_string("accounts/reset_email.txt", ctx),
            render_to_string("accounts/reset_email.html", ctx))


def send_reset(base_url: str, user, code: str, link: str) -> None:
    subject, text, html = reset_message(base_url, user.get_full_name() or user.email, code, link)
    msg = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [user.email])
    msg.attach_alternative(html, "text/html")
    msg.send(fail_silently=False)
