from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from panel import views_seo

admin.site.site_header = admin.site.site_title = "mhisobot — бошқарув"

urlpatterns = [
    path("robots.txt", views_seo.robots_txt, name="robots_txt"),
    path("sitemap.xml", views_seo.sitemap_xml, name="sitemap_xml"),
    path("og-image", views_seo.og_image, name="og_image"),
    path(settings.ADMIN_URL, admin.site.urls),
    path(settings.PANEL_URL, include("panel.urls")),
    path("texnazor/", include("texnazor.urls")),
    path("", include("accounts.urls")),
    path("", include("jobs.urls")),
]
