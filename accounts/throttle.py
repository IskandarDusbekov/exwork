"""Уринишлар чеклови: битта email ёки IP'дан кўп хато кириш бўлса — вақтинча блок."""
import ipaddress
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import AuthAttempt, normalize_email

WINDOW = timedelta(minutes=15)
LOGIN_PER_EMAIL = 5     # 15 дақиқада битта ҳисобга 5 та хато пароль
LOGIN_PER_IP = 20       # битта IP'дан турли ҳисобларга
REGISTER_PER_IP = 5     # битта IP'дан 15 дақиқада нечта рўйхат
RESET_PER_IP = 5        # паролни тиклаш хати: битта IP'дан
RESET_PER_EMAIL = 3     # битта email'га (бировнинг почтасини хат билан тўлдириб бўлмасин)


def _valid_ip(value):
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request):
    """Прокси ортида бўлса (NARXLASH_TRUST_PROXY=N — проксилар сони) X-Forwarded-For'нинг охиридан N-манзил,
    акс ҳолда REMOTE_ADDR. Мижоз ўзи ёзган манзиллар рўйхат бошида бўлади — улар ҳисобга олинмайди.
    Масалан, фақат nginx — 1; Cloudflare + nginx — 2. Нотўғри қиймат бўлса — REMOTE_ADDR."""
    remote = request.META.get("REMOTE_ADDR") or None
    hops = getattr(settings, "NARXLASH_TRUST_PROXY", 0)
    if hops:
        parts = [p for p in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if p.strip()]
        if len(parts) >= hops:
            return _valid_ip(parts[-hops]) or remote
    return remote


def _recent(kind):
    return AuthAttempt.objects.filter(kind=kind, created__gte=timezone.now() - WINDOW)


def login_blocked(request, email) -> bool:
    recent = _recent(AuthAttempt.Kind.LOGIN)
    ip = client_ip(request)
    return (recent.filter(email=normalize_email(email)).count() >= LOGIN_PER_EMAIL
            or (ip is not None and recent.filter(ip=ip).count() >= LOGIN_PER_IP))


def register_blocked(request) -> bool:
    ip = client_ip(request)
    return ip is not None and _recent(AuthAttempt.Kind.REGISTER).filter(ip=ip).count() >= REGISTER_PER_IP


def reset_blocked(request, email) -> bool:
    recent = _recent(AuthAttempt.Kind.RESET)
    ip = client_ip(request)
    return (recent.filter(email=normalize_email(email)).count() >= RESET_PER_EMAIL
            or (ip is not None and recent.filter(ip=ip).count() >= RESET_PER_IP))


def record(request, kind, email=""):
    AuthAttempt.objects.filter(created__lt=timezone.now() - timedelta(days=1)).delete()  # эскилари керак эмас
    AuthAttempt.objects.create(kind=kind, email=normalize_email(email), ip=client_ip(request))


def clear_login_failures(email):
    AuthAttempt.objects.filter(kind=AuthAttempt.Kind.LOGIN, email=normalize_email(email)).delete()
