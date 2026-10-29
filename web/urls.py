from django.conf import settings
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = admin.site.site_title = "Нархлаш — бошқарув"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path(settings.PANEL_URL, include("panel.urls")),
    path("", include("accounts.urls")),
    path("", include("jobs.urls")),
]
