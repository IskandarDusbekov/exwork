"""Администратор жавоб берганда фойдаланувчига хат: «жавоб келди, саҳифани очинг» (хабар матни хатда йўқ —
ҳисобга кириб ўқилади)."""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.templatetags.static import static

SUBJECT = "mhisobot: ёрдам хизматидан жавоб келди"


def reply_message(base_url: str, name: str, link: str) -> tuple:
    base = base_url.rstrip("/")
    ctx = {"name": name, "link": link, "site": base.split("://", 1)[-1], "logo": base + static("logo-512.png")}
    return (SUBJECT, render_to_string("support/reply_email.txt", ctx),
            render_to_string("support/reply_email.html", ctx))


def send_reply(base_url: str, user, link: str) -> None:
    subject, text, html = reply_message(base_url, user.get_full_name() or user.email, link)
    msg = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [user.email])
    msg.attach_alternative(html, "text/html")
    msg.send(fail_silently=False)
