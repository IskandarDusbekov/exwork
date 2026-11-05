"""Сайтга кириш уринишлари: ким (IP, фойдаланувчи), қайси саҳифага, неча марта, қандай жавоб олди.

Ҳар бир сўров базага алоҳида ёзилмайди — жараён хотирасида (IP, тур, саҳифа, жавоб, соат) бўйича йиғилади ва
``FLUSH_SECONDS`` да ёки ``FLUSH_SIZE`` хил ёзув тўпланганда бир марта ``AccessStat`` га қўшилади (count += n).
Шунда бот кетма-кет 1000 марта урилса ҳам базада битта қатор: «1000 марта». Бир нечта gunicorn жараёни ҳар бири
ўзиникини қўшади — йиғинди тўғри чиқади.

Турлари (биринчи мос келгани): блокланган IP → бегона Host → сканер (заифлик қидирувчи манзиллар: .php, .env,
wp-admin…) → хато кириш (пароль/код) → CSRF рад этилиши → бот (User-Agent) → кирган фойдаланувчи → меҳмон."""
import atexit
import logging
import re
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

log = logging.getLogger(__name__)

FLUSH_SECONDS = 60
FLUSH_SIZE = 300
MAX_BUFFER = 5000  # база ишламаса — хотира тўлиб кетмасин
KEEP_DAYS = 30

SCANNER_RE = re.compile(
    r"\.(?:php\d?|aspx?|jsp|cgi|env|git|svn|hg|ds_store|bak|old|sql|swp|ini|log|yml|yaml|htaccess|htpasswd)(?:$|[/?.])"
    r"|wp-(?:admin|login|content|includes|json)|wordpress|xmlrpc|phpmyadmin|/pma/|/mysql|cgi-bin|/vendor/"
    r"|/actuator|/boaform|/hnap1|/shell|/cmd|/eval|/etc/passwd|\.\./|/owa/|/autodiscover|/solr|/manager/html"
    r"|/telescope|/_ignition|/druid|/jenkins|/geoserver|/remote/|/sdk$|/setup\.|/config\.|/\.aws|/server-status"
    r"|/administrator|/user/login|/console", re.I)
BOT_RE = re.compile(r"bot|crawl|spider|slurp|curl|wget|python|httpx|aiohttp|go-http|java/|libwww|scan|masscan|"
                    r"zgrab|nmap|nikto|sqlmap|headless|okhttp|axios|node-fetch|censys|expanse|shodan", re.I)
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_NUM_RE = re.compile(r"(?<=/)\d+(?=/|$)")

_buf = {}  # key -> [count, first_seen, last_seen, user_agent]
_lock = threading.Lock()
_flushing = threading.Lock()
_state = {"last": time.monotonic(), "cleaned": 0.0}


def _setting(name, default):
    return getattr(settings, name, default)


def _bad_host(request):
    from django.http.request import split_domain_port, validate_host
    raw = request.META.get("HTTP_HOST") or request.META.get("SERVER_NAME", "")
    domain, _ = split_domain_port(raw)
    hosts = settings.ALLOWED_HOSTS
    if settings.DEBUG and not hosts:
        hosts = [".localhost", "127.0.0.1", "[::1]"]
    return (not domain or not validate_host(domain, hosts)), raw[:200]


def classify(request, status):
    """(тур, host) — ``AccessStat.Kind`` қиймати."""
    from .models import AccessStat
    K = AccessStat.Kind
    marked = getattr(request, "access_kind", "")
    if marked == K.BLOCKED:
        return K.BLOCKED, ""
    bad, host = _bad_host(request)
    if bad:
        return K.BAD_HOST, host
    if SCANNER_RE.search(request.path):
        return K.SCANNER, ""
    if marked in (K.LOGIN_FAIL, K.CSRF):
        return marked, ""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return K.USER, ""
    ua = request.META.get("HTTP_USER_AGENT", "")
    if not ua or BOT_RE.search(ua):
        return K.BOT, ""
    return K.GUEST, ""


def _path(request, kind):
    from .models import AccessStat
    if kind in (AccessStat.Kind.SCANNER, AccessStat.Kind.BAD_HOST):
        p = request.get_full_path()  # сканер нимани қидиргани — сўров қатори билан
    else:
        p = _NUM_RE.sub(":n", _UUID_RE.sub(":id", request.path))  # /object/<uuid>/ — бир хил саҳифа
    return p[:300]


def _skip(request):
    static = "/" + settings.STATIC_URL.lstrip("/")
    return request.path.startswith(static) or request.path == "/favicon.ico"


def record(request, response):
    """Битта сўровни хотирага қўшади (базага ёзмайди). Хато бўлса — жимгина ўтказиб юборади."""
    if not _setting("NARXLASH_ACCESS_LOG", True) or _skip(request):
        return
    status = getattr(response, "status_code", None)
    kind, host = classify(request, status)
    user = getattr(request, "user", None)
    uid = user.pk if user is not None and getattr(user, "is_authenticated", False) else None
    from accounts.throttle import client_ip
    now = timezone.now()
    key = (now.replace(minute=0, second=0, microsecond=0), client_ip(request), str(kind),
           (request.method or "")[:10], _path(request, kind), status, uid, host)
    ua = request.META.get("HTTP_USER_AGENT", "")[:300]
    with _lock:
        v = _buf.get(key)
        if v is None:
            if len(_buf) >= MAX_BUFFER:
                return
            _buf[key] = [1, now, now, ua]
        else:
            v[0] += 1
            v[2], v[3] = now, ua
        due = (len(_buf) >= _setting("NARXLASH_ACCESS_FLUSH_SIZE", FLUSH_SIZE)
               or time.monotonic() - _state["last"] >= _setting("NARXLASH_ACCESS_FLUSH_SECONDS", FLUSH_SECONDS))
    if due:
        flush()


def pending():
    with _lock:
        return sum(v[0] for v in _buf.values())


def flush():
    """Тўпланганини базага қўшади. Бир вақтда фақат битта оқим ёзади; база хатосида ёзувлар хотирага қайтади."""
    global _buf
    if not _flushing.acquire(blocking=False):
        return 0
    try:
        with _lock:
            items, _buf = _buf, {}
            _state["last"] = time.monotonic()
        if not items:
            return 0
        try:
            _write(items)
        except Exception:  # noqa: BLE001
            log.debug("access flush failed", exc_info=True)
            with _lock:  # кейинги сафар яна уриниб кўрамиз
                for key, v in items.items():
                    if key in _buf:
                        _buf[key][0] += v[0]
                    elif len(_buf) < MAX_BUFFER:
                        _buf[key] = v
            return 0
        if time.monotonic() - _state["cleaned"] > 6 * 3600:
            _state["cleaned"] = time.monotonic()
            cleanup()
        return len(items)
    finally:
        _flushing.release()


def _write(items):
    from django.db import transaction
    from django.db.models import F

    from .models import AccessStat
    with transaction.atomic():  # савепоинт: хато бўлса бошқа транзакцияни бузмайди
        for (hour, ip, kind, method, path, status, uid, host), (n, first, last, ua) in items.items():
            where = dict(hour=hour, ip=ip, kind=kind, method=method, path=path, status=status, user_id=uid, host=host)
            done = AccessStat.objects.filter(**where).update(count=F("count") + n, last_seen=last, user_agent=ua)
            if not done:
                AccessStat.objects.create(**where, count=n, first_seen=first, last_seen=last, user_agent=ua)


def cleanup(days=KEEP_DAYS):
    from .models import AccessStat
    try:
        AccessStat.objects.filter(hour__lt=timezone.now() - timedelta(days=days)).delete()
    except Exception:  # noqa: BLE001
        pass


atexit.register(lambda: flush())


class AccessLogMiddleware:
    """Энг биринчи middleware: блокланган, бегона Host ва хато бўлган сўровлар ҳам ҳисобга тушади."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            record(request, response)
        except Exception:  # noqa: BLE001 — кузатув саҳифани бузмасин
            log.debug("access record failed", exc_info=True)
        return response


def csrf_failure(request, reason=""):
    """CSRF рад этилиши — Django'нинг ўз саҳифаси, лекин сўров «CSRF» тури билан белгиланади."""
    from django.views.csrf import csrf_failure as default
    request.access_kind = "csrf"
    return default(request, reason=reason)
