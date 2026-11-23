# i18n: ok-file — ички маълумот / тил номлари (ҳар бири ўз ёзувида)
"""Тизим тили: ўзбекча (кирилл) — асосий, ўзбекча (лотин) ва русча.

* Манба матнлар — кирилл ўзбекча (msgid), шунинг учун ``uz-cyrl`` учун таржима файли керак эмас.
* Лотин — ``manage.py i18n_latn`` кирилл матндан транслитерация қилиб ёзади (``web/translit.py``).
* Рус — ``locale/ru/LC_MESSAGES/django.po`` (қўлда таржима).

Тил браузер тилига қарамайди: фойдаланувчи танлаган (ҳисобида ёки cookie'да) ёки асосий — кирилл.
Бошқарув панели (``PANEL_URL``) ва Django admin доим кирилл ўзбекчада.
"""
import re

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponseRedirect
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

COOKIE = "mh_lang"
DEFAULT = "uz-cyrl"
CODES = ("uz-cyrl", "uz-latn", "ru")
# танлагичда кўринадиган номлар — ҳар бири ўз тилида (таржима қилинмайди)
CHOICES = [
    {"code": "uz-cyrl", "label": "Ўзбекча", "short": "Ўзб"},
    {"code": "uz-latn", "label": "Oʻzbekcha", "short": "Oʻzb"},
    {"code": "ru", "label": "Русский", "short": "Рус"},
]
# <html lang="…"> учун BCP 47 номлари
HTML_LANG = {"uz-cyrl": "uz-Cyrl", "uz-latn": "uz-Latn", "ru": "ru"}


def _fixed_prefixes():
    return ("/" + settings.PANEL_URL, "/" + settings.ADMIN_URL)


def pick(request) -> str:
    """Сўров учун тил: панел — доим асосий; бўлмаса ҳисобдаги танлов, cookie, асосий."""
    if request.path.startswith(_fixed_prefixes()):
        return DEFAULT
    user = getattr(request, "user", None)
    code = getattr(user, "language", "") if user is not None and user.is_authenticated else ""
    if code not in CODES:
        code = request.COOKIES.get(COOKIE, "")
    return code if code in CODES else DEFAULT


class LanguageMiddleware:
    """``LocaleMiddleware`` ўрнида: Accept-Language'га қарамайди (асосий тил — кирилл ўзбекча)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        code = pick(request)
        translation.activate(code)
        request.LANGUAGE_CODE = code
        response = self.get_response(request)
        response.headers.setdefault("Content-Language", HTML_LANG[code])
        translation.deactivate()
        return response


@login_not_required  # кириш саҳифасида ҳам алмаштириш мумкин
@require_POST
def set_language(request):
    """Тилни алмаштириш: cookie'га ва (кирган бўлса) ҳисобга ёзилади, ўша саҳифага қайтади."""
    code = request.POST.get("language", "")
    nxt = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/"
    if not url_has_allowed_host_and_scheme(nxt, {request.get_host()}, require_https=request.is_secure()):
        nxt = "/"
    response = HttpResponseRedirect(nxt)
    if code in CODES:
        response.set_cookie(COOKIE, code, max_age=365 * 24 * 3600, samesite="Lax",
                            secure=request.is_secure(), httponly=False)
        if request.user.is_authenticated and getattr(request.user, "language", None) != code:
            request.user.language = code
            request.user.save(update_fields=["language"])
    return response



def context(request):
    """Шаблонлар учун: жорий тил ва танлагич рўйхати."""
    code = getattr(request, "LANGUAGE_CODE", None) or translation.get_language() or DEFAULT
    if code not in CODES:
        code = DEFAULT
    return {"LANG": code, "LANG_HTML": HTML_LANG[code], "LANG_CHOICES": CHOICES,
            "LANG_CURRENT": next(c for c in CHOICES if c["code"] == code)}


def excel(fn):
    """Excel чиқишлар таржима қилинмайди (қарор: «ҳозиргидек қолсин») — ичида ҳамма матн асосий тилда (кирилл)."""
    import functools

    @functools.wraps(fn)
    def wrap(*a, **kw):
        with translation.override(DEFAULT):
            return fn(*a, **kw)
    return wrap


_PATTERNS: dict = {}
_HOLE = re.compile(r"\{(\d+)\}")


def _patterns(lang):
    """Каталогдаги «{0}» ли msgid'лар → (regex, таржима): сақланган (форматланган) хабарни таниш учун."""
    pats = _PATTERNS.get(lang)
    if pats is None:
        pats = []
        try:
            cat = translation.trans_real.translation(lang)._catalog
            items = list(cat.items())
        except Exception:  # noqa: BLE001
            items = []
        for mid, tr in items:
            if not isinstance(mid, str) or "{0}" not in mid or not isinstance(tr, str) or not tr \
                    or len(_HOLE.sub("", mid).strip()) < 3:  # «{0}» ёлғиз ёки «{0} {1}» — ҳар нарсага мос, олинмайди
                continue
            parts = _HOLE.split(mid)
            rx, order = "", []
            for i, p in enumerate(parts):
                if i % 2:
                    rx += "(.+?)"
                    order.append(int(p))
                else:
                    rx += re.escape(p)
            pats.append((re.compile(rx + r"\Z", re.S), order, tr))
        pats.sort(key=lambda x: -len(x[0].pattern))  # узунроқ (аниқроқ) намуна олдин
        _PATTERNS[lang] = pats
    return pats


def ui(text):
    """Базада сақланган хабар (фон вазифаси хатоси ва ҳ.к. — асосий тилда ёзилган) → жорий тилда.
    Аниқ мос msgid бўлса — таржимаси; «{0}» ли намунага тушса — қисмлари ўрнига қўйилади; бўлмаса — ўзи."""
    if not text:
        return text
    text = str(text)
    lang = translation.get_language() or DEFAULT
    if lang == DEFAULT:
        return text
    t = translation.gettext(text)
    if t != text:
        return t
    for rx, order, tr in _patterns(lang):
        m = rx.match(text)
        if m:
            vals = {n: ui(v) for n, v in zip(order, m.groups())}  # қисмлари ҳам (масалан «бирликка — C→E»)
            try:
                return tr.format(*[vals.get(i, "") for i in range(max(order) + 1)])
            except (IndexError, KeyError, ValueError):
                return text
    for sep in ("; ", " · ", "\n"):  # бир неча хабар бир қаторда қўшилган бўлса — ҳар бири алоҳида
        if sep in text:
            return sep.join(ui(p) for p in text.split(sep))
    return text


def ui_progress(p):
    """Фон вазифаси прогресси ({"step": матн, …}) — «step» жорий тилда (саҳифадаги JS ҳам таржима қилинган
    калитлар билан солиштиради, иккаласи бир хил gettext'дан ўтади)."""
    if not isinstance(p, dict) or not p.get("step"):
        return p
    return {**p, "step": ui(p["step"])}


def user_lang(user, fallback=None) -> str:
    """Фойдаланувчига юбориладиган хат тили: ўзи танлагани, бўлмаса — жорий сўров тили (ёки асосий)."""
    code = getattr(user, "language", "") or ""
    if code in CODES:
        return code
    cur = fallback or translation.get_language()
    return cur if cur in CODES else DEFAULT
