"""SEO: панелдаги созлаш саҳифаси ва очиқ манзиллар — /robots.txt, /sitemap.xml, /og-image (panel.seo)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET, require_POST

from . import conf, seo
from .audit import log as audit
from .models import Event
from .views import _ctx, staff_required, superuser_required

# Қидирувга керак бўлмаган (кирилгандан кейинги) бўлимлар. Панел манзили бу ерга ЁЗИЛМАЙДИ — у сир.
PRIVATE = ("/login/code/", "/password/", "/account/", "/dashboard/", "/object/", "/job/", "/folder/", "/texnazor/",
           "/calc/", "/help/", "/delete/", "/rename/")


def public_pages():
    """sitemap.xml даги саҳифалар: (манзил, муҳимлиги)."""
    pages = [(reverse("index"), "1.0"), (reverse("login"), "0.5")]
    if conf.get("signup") == "open":
        pages.insert(1, (reverse("register"), "0.6"))
    return pages


@staff_required
def seo_page(request):
    v = seo.values()
    groups = [{"key": g, "title": t, "desc": d, "fields": [{"f": f, "value": v[f.key]} for f in seo.FIELDS if f.group == g]}
              for g, t, d in seo.GROUPS]
    base = request.build_absolute_uri("/").rstrip("/")
    n = len(v["description"])
    checks = [  # (номи, жойидами, изоҳ — фақат керак бўлганда)
        ("Қидирувда кўринишга рухсат", v["index"], "" if v["index"] else "Ёпиқ — Google ва Yandex сайтни натижага қўшмайди"),
        ("Google тасдиқлаш коди", bool(v["google"]), "" if v["google"] else "Search Console'дан олинг"),
        ("Яндекс тасдиқлаш коди", bool(v["yandex"]), "" if v["yandex"] else "Вебмастер'дан олинг"),
        ("Ижтимоий тармоқ расми", bool(seo.image_path()), "" if seo.image_path() else "Telegram ва бошқаларда ҳавола расм билан чиқади"),
        ("Ташриф статистикаси", bool(v["ga"] or v["metrika"]), "" if v["ga"] or v["metrika"] else "Ихтиёрий: Google Analytics ёки Метрика"),
        ("Тавсиф узунлиги", 70 <= n <= 160, f"{n} белги — яхшиси 70–160"),
    ]
    return render(request, "panel/seo.html", _ctx(
        request, "seo", groups=groups, v=v, checks=checks, base=base, host=request.get_host(),
        robots_url=base + reverse("robots_txt"), sitemap_url=base + reverse("sitemap_xml"),
        image_url=reverse("og_image") if seo.image_path() else "", updated=parse_datetime(v["updated"] or "") if v["updated"] else None,
        pages=[base + p for p, _ in public_pages()]))


@require_POST
@superuser_required
def seo_save(request):
    new = {}
    try:
        for f in seo.FIELDS:
            new[f.key] = seo.clean(f, request.POST.get(f.key, ""))
        new["index"] = request.POST.get("index") == "1"
        if request.FILES.get("image"):
            new["image"] = seo.save_image(request.FILES["image"])
        elif request.POST.get("image_delete") == "1":
            path = seo.image_path()
            if path:
                path.unlink(missing_ok=True)
            new["image"] = ""
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("panel:seo")
    changed = seo.save(new, request.user)
    if changed:
        audit(request, Event.Kind.ADMIN_SETTINGS, detail="SEO: " + ", ".join(changed))
        messages.success(request, "Сақланди: " + ", ".join(changed) + ". Қидирув тизимлари буни бир неча кундан кейин янгилайди.")
    else:
        messages.success(request, "Ўзгариш йўқ")
    return redirect("panel:seo")


# ===================== очиқ манзиллар =====================
@login_not_required
@require_GET
@cache_control(max_age=3600, public=True)
def robots_txt(request):
    v = seo.values()
    lines = ["User-agent: *"]
    if v["index"]:
        lines += ["Allow: /"] + [f"Disallow: {p}" for p in PRIVATE]
    else:
        lines.append("Disallow: /")
    lines += ["", f"Sitemap: {request.build_absolute_uri(reverse('sitemap_xml'))}", ""]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


@login_not_required
@require_GET
@cache_control(max_age=3600, public=True)
def sitemap_xml(request):
    v = seo.values()
    lastmod = (v["updated"] or "")[:10]
    items = []
    for path, prio in (public_pages() if v["index"] else []):
        items.append(f"  <url><loc>{request.build_absolute_uri(path)}</loc>"
                     + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "") + f"<priority>{prio}</priority></url>")
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(items) + "\n</urlset>\n")
    return HttpResponse(body, content_type="application/xml; charset=utf-8")


@login_not_required
@require_GET
@cache_control(max_age=86400, public=True)
def og_image(request):
    path = seo.image_path()
    if path is None:
        raise Http404
    return FileResponse(open(path, "rb"), content_type=seo.image_type(path))
