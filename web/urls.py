# i18n: ok-file — ички маълумот / тил номлари (ҳар бири ўз ёзувида)
from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path

from django.contrib.auth.decorators import login_not_required
from django.views.i18n import JavaScriptCatalog

from panel import track, views_seo
from web import i18n

admin.site.site_header = admin.site.site_title = "mhisobot — бошқарув"
admin.site.index_title = "Маълумотлар базаси"
admin.site.site_url = "/" + settings.PANEL_URL  # «Сайтни кўриш» — бошқарув панелига қайтади

urlpatterns = [
    path("robots.txt", views_seo.robots_txt, name="robots_txt"),
    path("sitemap.xml", views_seo.sitemap_xml, name="sitemap_xml"),
    path("og-image", views_seo.og_image, name="og_image"),
    re_path(r"^(?P<code>google[0-9a-f]{16})\.html$", views_seo.google_file, name="google_file"),
    re_path(r"^yandex_(?P<code>[0-9a-f]{8,64})\.html$", views_seo.yandex_file, name="yandex_file"),
    path("t/", track.beacon, name="track"),
    path("til/", i18n.set_language, name="set_language"),  # тилни алмаштириш (web/i18n.py)
    # JS матнлари таржимаси (gettext() static/js'да); кириш саҳифасида ҳам керак
    path("jsi18n/", login_not_required(JavaScriptCatalog.as_view()), name="jsi18n"),  # саҳифадаги қадамлар (фойдаланувчи йўли)
    path(settings.ADMIN_URL, admin.site.urls),
    path(settings.PANEL_URL, include("panel.urls")),
    path("texnazor/", include("texnazor.urls")),
    path("support/", include("support.urls")),
    path("news/", include("news.urls")),
    path("", include("accounts.urls")),
    path("", include("jobs.urls")),
]
