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


class StaffTwoFactorMiddleware:
    """Панел ва Django админ — фақат икки босқичли кириш билан (``is_staff``). Ёқилмаган бўлса — ўрнатиш саҳифаси,
    ёқилган, лекин шу сессияда код киритилмаган бўлса (масалан админнинг ўз кириш формаси орқали) — код саҳифаси.
    Оддий фойдаланувчига панел барибир 404 (бу ерда ҳеч нарса ошкор бўлмайди)."""

    def __init__(self, get_response):
        self.get_response = get_response
        from django.conf import settings
        self.prefixes = tuple("/" + u.lstrip("/") for u in (settings.PANEL_URL, settings.ADMIN_URL))

    def __call__(self, request):
        from django.conf import settings
        user = getattr(request, "user", None)
        if (getattr(settings, "NARXLASH_STAFF_2FA", True) and user is not None and user.is_authenticated and user.is_staff
                and request.path.startswith(self.prefixes)):
            from django.shortcuts import redirect
            from django.urls import reverse
            from django.utils.http import urlencode

            from accounts.models import TwoFactor
            from accounts.views import PENDING, VERIFIED, _pending
            if request.session.get(VERIFIED) != user.pk:
                nxt = request.get_full_path() if request.method == "GET" else ""
                if TwoFactor.objects.filter(user=user).exists():
                    _pending(request, user, nxt)
                    return redirect("login_code")
                request.session.pop(PENDING, None)
                return redirect(reverse("twofa_setup") + ("?" + urlencode({"next": nxt}) if nxt else ""))
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
