from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("job/<uuid:job_id>/", views.job_detail, name="job"),
    path("job/<uuid:job_id>/run/", views.job_run, name="job_run"),
    path("job/<uuid:job_id>/download/", views.job_download, name="job_download"),
]
