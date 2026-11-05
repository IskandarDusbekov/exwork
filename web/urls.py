from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path

from panel import views_seo

admin.site.site_header = admin.site.site_title = "mhisobot — бошқарув"
admin.site.index_title = "Маълумотлар базаси"
admin.site.site_url = "/" + settings.PANEL_URL  # «Сайтни кўриш» — бошқарув панелига қайтади

urlpatterns = [
    path("robots.txt", views_seo.robots_txt, name="robots_txt"),
    path("sitemap.xml", views_seo.sitemap_xml, name="sitemap_xml"),
    path("og-image", views_seo.og_image, name="og_image"),
    re_path(r"^(?P<code>google[0-9a-f]{16})\.html$", views_seo.google_file, name="google_file"),
    re_path(r"^yandex_(?P<code>[0-9a-f]{8,64})\.html$", views_seo.yandex_file, name="yandex_file"),
    path(settings.ADMIN_URL, admin.site.urls),
    path(settings.PANEL_URL, include("panel.urls")),
    path("texnazor/", include("texnazor.urls")),
    path("", include("accounts.urls")),
    path("", include("jobs.urls")),
]
