"""Сессиялар: IP ва қурилмани ёзиш, сессияни тугатиш, IP блоклаш."""
import re
from datetime import timedelta

from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.utils import timezone

from accounts.throttle import client_ip

from .models import BlockedIP, LoginSession

ONLINE_MINUTES = 5  # шунча вақт ичида сўров юборган — «онлайн»
_BLOCKED_KEY = "panel:blocked_ips"

_BROWSERS = [  # тартиб муҳим: Edge ва Opera ҳам «Chrome» деб ёзади
    ("YaBrowser", "Яндекс"), ("Edg", "Edge"), ("OPR", "Opera"), ("Opera", "Opera"), ("SamsungBrowser", "Samsung"),
    ("Firefox", "Firefox"), ("FxiOS", "Firefox"), ("CriOS", "Chrome"), ("Chrome", "Chrome"), ("Safari", "Safari"),
]
_OS = [
    (r"Windows NT 10", "Windows 10/11"), (r"Windows NT 6\.3", "Windows 8.1"), (r"Windows NT 6\.1", "Windows 7"),
    (r"Windows", "Windows"), (r"Android", "Android"), (r"iPhone|iPad|iPod", "iOS"), (r"Mac OS X", "macOS"),
    (r"CrOS", "ChromeOS"), (r"Linux", "Linux"),
]


def parse_ua(ua: str) -> dict:
    """User-Agent'дан браузер, тизим ва қурилма тури (тахминий — сарлавҳани мижоз ўзи юборади)."""
    ua = ua or ""
    browser = next((name for key, name in _BROWSERS if key in ua), "Номаълум")
    keys = "Version" if browser == "Safari" else "|".join(k for k, n in _BROWSERS if n == browser)
    m = re.search(rf"(?:{keys})/(\d+)", ua) if browser != "Номаълум" else None
    if m:
        browser = f"{browser} {m.group(1)}"
    os_ = next((name for pat, name in _OS if re.search(pat, ua)), "Номаълум")
    m = re.search(r"Android (\d+)", ua) or re.search(r"OS (\d+)_\d+ like Mac", ua)
    if m and os_ in ("Android", "iOS"):
        os_ = f"{os_} {m.group(1)}"
    low = ua.lower()
    if re.search(r"bot|crawl|spider|curl|wget|python-requests|httpx", low):
        device = "bot"
    elif "ipad" in low or "tablet" in low or ("android" in low and "mobile" not in low):
        device = "tablet"
    elif "mobi" in low or "iphone" in low:
        device = "phone"
    else:
        device = "computer"
    return {"browser": browser, "os": os_, "device": device}


def device_text(request) -> str:
    d = parse_ua(request.META.get("HTTP_USER_AGENT", ""))
    kind = {"phone": "телефон", "tablet": "планшет", "bot": "бот"}.get(d["device"], "компьютер")
    return f"{d['browser']} · {d['os']} · {kind}"


def touch(request, user):
    """Сессия бўйича охирги фаоллик (минутига бир марта ёзилади). Кузатувгача очилган сессиялар ҳам қўшилади."""
    key = request.session.session_key
    if not key or cache.get(f"panel:seen:{key}"):
        return
    cache.set(f"panel:seen:{key}", 1, 60)
    now = timezone.now()
    ip = client_ip(request)
    qs = LoginSession.objects.filter(session_key=key, user=user)
    if qs.filter(ended__isnull=True).update(last_seen=now, ip=ip):
        return
    if not qs.exists():
        start(request, user)


def start(request, user):
    ua = request.META.get("HTTP_USER_AGENT", "")[:500]
    LoginSession.objects.create(user=user, session_key=request.session.session_key or "", ip=client_ip(request),
                                user_agent=ua, last_seen=timezone.now(), **parse_ua(ua))


def end(session_key, reason):
    LoginSession.objects.filter(session_key=session_key, ended__isnull=True).update(
        ended=timezone.now(), end_reason=reason)


def revoke(session: LoginSession, reason="revoked"):
    """Сессияни тугатиш: фойдаланувчи кейинги сўровдаёқ кириш саҳифасига қайтади."""
    Session.objects.filter(session_key=session.session_key).delete()
    end(session.session_key, reason)


def revoke_all(user, reason="revoked") -> int:
    """Фойдаланувчининг барча сессиялари, жумладан кузатув бошланишидан олдин очилганлари."""
    n = 0
    for s in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        if s.get_decoded().get("_auth_user_id") == str(user.pk):
            s.delete()
            n += 1
    LoginSession.objects.filter(user=user, ended__isnull=True).update(ended=timezone.now(), end_reason=reason)
    return n


def blocked_ips() -> frozenset:
    return cache.get_or_set(_BLOCKED_KEY, lambda: frozenset(BlockedIP.objects.values_list("ip", flat=True)), 60)


def blocked_changed():
    cache.delete(_BLOCKED_KEY)


def online_since():
    return timezone.now() - timedelta(minutes=ONLINE_MINUTES)
