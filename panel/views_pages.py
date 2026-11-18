"""Панел → «Биз ҳақимизда ва шартлар»: очиқ саҳифалар /biz-haqimizda/ ва /shartlar/ матни (SiteSetting "about",
"terms"). Кўриш — ``is_staff``; ўзгартириш — суперфойдаланувчи. Ҳар бир сақлаш журналга ёзилади."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from jobs import pages, richtext

from . import conf
from .audit import log as audit
from .models import Event
from .views import _ctx, staff_required, superuser_required

TABS = (("about", "Биз ҳақимизда"), ("terms", "Фойдаланиш шартлари"))


def _line(v, n=300):
    return " ".join(str(v or "").split())[:n]


@staff_required
def pages_page(request):
    ptab = request.GET.get("tab") if request.GET.get("tab") in dict(TABS) else "about"
    users = get_user_model().objects
    t, a = pages.terms(), pages.about()
    return render(request, "panel/pages.html", _ctx(
        request, "pages", ptab=ptab, tabs=TABS, a=a, t=t, modes=pages.TERMS_MODES,
        facts6=(a["facts"] + [["", ""]] * 6)[:6],
        accepted=users.filter(terms_accepted_at__isnull=False).count(),
        accepted_now=users.filter(terms_version=t["version"]).count(), n_users=users.count()))


@require_POST
@superuser_required
def pages_save(request):
    P = request.POST
    if P.get("tab") == "terms":
        old = conf.raw("terms", {}) or {}
        version = int(old.get("version") or 1) + (1 if P.get("bump") == "1" else 0)
        mode = P.get("mode") if P.get("mode") in dict(pages.TERMS_MODES) else "checkbox"
        body = richtext.sanitize(P.get("body", ""))
        conf.put("terms", {"body": body, "mode": mode, "version": version,
                           "updated": timezone.localdate().strftime("%d.%m.%Y")}, request.user)
        audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Сайт: фойдаланиш шартлари сақланди ({version}-таҳрир, {mode})")
        messages.success(request, f"Шартлар сақланди: {version}-таҳрир" + (" (янги таҳрир)" if P.get("bump") == "1" else ""))
        return redirect(reverse("panel:pages") + "?tab=terms")

    team = []
    names, roles, bios, photos = (P.getlist(k) for k in ("t_name", "t_role", "t_bio", "t_photo"))
    for i, name in enumerate(names[:pages.MAX_TEAM]):
        name = _line(name, 80)
        if not name:
            continue
        photo = (photos[i] if i < len(photos) else "").strip()
        team.append({"name": name, "role": _line(roles[i] if i < len(roles) else "", 80),
                     "bio": str(bios[i] if i < len(bios) else "").replace("\r", "").strip()[:600],
                     "photo": photo if photo.startswith("/img/") and richtext.IMG_NAME.match(photo[5:]) else ""})
    facts = [[_line(a, 20), _line(b, 50)] for a, b in zip(P.getlist("f_num"), P.getlist("f_label")) if _line(a) or _line(b)][:6]
    email = _line(P.get("c_email"), 120)
    conf.put("about", {
        "lead": _line(P.get("lead"), 400) or pages.ABOUT_DEFAULT["lead"],
        "body": richtext.sanitize(P.get("body", "")),
        "facts": facts, "team": team,
        "contacts": {"phone": _line(P.get("c_phone"), 40), "email": email if "@" in email else "",
                     "telegram": _line(P.get("c_telegram"), 80), "address": _line(P.get("c_address"), 200)},
    }, request.user)
    audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Сайт: «Биз ҳақимизда» сақланди (жамоа: {len(team)})")
    messages.success(request, "«Биз ҳақимизда» сақланди")
    return redirect(reverse("panel:pages") + "?tab=about")
