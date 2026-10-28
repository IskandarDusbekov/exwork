from django.core.cache import cache
from django.http import HttpResponseForbidden

from accounts.throttle import client_ip

from . import audit, sessions
from .models import Event


class BlockedIPMiddleware:
    """Блокланган IP'дан келган ҳар қандай сўров — 403 (кириш саҳифаси ҳам очилмайди)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ip = client_ip(request)
        if ip and ip in sessions.blocked_ips():
            if cache.add(f"panel:blocked_seen:{ip}", 1, 600):  # журналга 10 дақиқада бир марта
                audit.log(request, Event.Kind.BLOCKED_IP, actor=False, detail=request.path[:200])
            return HttpResponseForbidden("<h1>403</h1><p>Сизнинг манзилингиздан кириш блокланган.</p>")
        return self.get_response(request)


class SessionTrackMiddleware:
    """Кирган фойдаланувчининг сессияси: IP, қурилма, охирги фаоллик (панел учун)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            try:
                sessions.touch(request, user)
            except Exception:  # noqa: BLE001 — кузатув хатоси саҳифани бузмасин
                pass
        return self.get_response(request)
