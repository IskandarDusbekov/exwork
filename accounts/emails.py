"""Фойдаланувчига юбориладиган кодли хатлар (паролни тиклаш, email тасдиқлаш): матн (оддий) ва HTML (катта,
аниқ код ва тугма) — бир хил мазмун. Панел «Почта» бўлимидаги кўриниш ҳам шу функцияларни ишлатади."""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.utils import translation

from web.i18n import user_lang
from django.utils.translation import gettext as _, gettext_lazy as _l

SUBJECT = _l("mhisobot: паролни тиклаш коди {code}")
VERIFY_SUBJECT = _l("mhisobot: email тасдиқлаш коди {code}")


def pretty_code(code: str) -> str:
    """482913 → «482 913» — кўз билан ўқиш ва териш осон."""
    return f"{code[:3]} {code[3:]}" if len(code) == 6 else code


def _message(subject: str, base_url: str, name: str, code: str, link: str, **text) -> tuple:
    ctx = {"name": name, "code": code, "code_pretty": pretty_code(code), "link": link,
           "minutes": settings.PASSWORD_RESET_CODE_MINUTES,
           "site": base_url.split("://", 1)[-1].rstrip("/"), "base": base_url.rstrip("/"),
           "logo": base_url.rstrip("/") + static("logo-512.png"), **text}
    return (subject.format(code=pretty_code(code)), render_to_string("accounts/code_email.txt", ctx),
            render_to_string("accounts/code_email.html", ctx))


def reset_message(base_url: str, name: str, code: str, link: str) -> tuple:
    """(мавзу, матн, html). ``base_url`` — https://mhisobot.uz (расм ва ҳаволалар учун)."""
    hours = settings.PASSWORD_RESET_TIMEOUT // 3600
    return _message(SUBJECT, base_url, name, code, link,
                    title=_("Паролни тиклаш"), intro=_("mhisobot тизимида паролингизни тиклаш сўралди."),
                    page=_("Паролни тиклаш"),
                    button_intro=_("Ёки бошқа қурилмадан бўлса — тугмани босиб, дарров янги пароль қўйинг:"),
                    button=_("Янги пароль қўйиш"), link_note=_("{0} соат амал қилади, бир марта ишлайди").format(hours),
                    ignore=_("Буни сиз сўрамаган бўлсангиз — хатни эътиборсиз қолдиринг, паролингиз ўзгармайди."))


def verify_message(base_url: str, name: str, code: str, link: str) -> tuple:
    hours = settings.EMAIL_VERIFY_LINK_HOURS
    return _message(VERIFY_SUBJECT, base_url, name, code, link,
                    title=_("Email манзилини тасдиқлаш"),
                    intro=_("mhisobot тизимида шу манзил билан ҳисоб очилди. Бу почта сизники эканини тасдиқланг."),
                    page=_("Email тасдиқлаш"),
                    button_intro=_("Ёки бошқа қурилмадан бўлса — тугмани босинг, email дарров тасдиқланади:"),
                    button=_("Email'ни тасдиқлаш"), link_note=_("{0} соат амал қилади").format(hours),
                    ignore=_("Ҳисобни сиз очмаган бўлсангиз — хатни эътиборсиз қолдиринг: тасдиқланмаган ҳисоб "
                           "шу почта орқали паролни тиклай олмайди."))


def _send(message: tuple, to: str) -> None:
    subject, text, html = message
    msg = EmailMultiAlternatives(str(subject), text, settings.DEFAULT_FROM_EMAIL, [to])
    msg.attach_alternative(html, "text/html")
    msg.send(fail_silently=False)


def send_reset(base_url: str, user, code: str, link: str) -> None:
    with translation.override(user_lang(user)):  # хат — фойдаланувчи тилида
        _send(reset_message(base_url, user.get_full_name() or user.email, code, link), user.email)


def send_verify(base_url: str, user, code: str, link: str) -> None:
    with translation.override(user_lang(user)):
        _send(verify_message(base_url, user.get_full_name() or user.email, code, link), user.email)
