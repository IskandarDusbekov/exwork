from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("job/<uuid:job_id>/", views.job_detail, name="job"),
    path("object/<uuid:batch_id>/", views.batch_detail, name="batch"),
    path("object/<uuid:batch_id>/replan/", views.batch_replan, name="batch_replan"),
    path("object/<uuid:batch_id>/confirm/", views.batch_confirm, name="batch_confirm"),
    path("object/<uuid:batch_id>/download/", views.batch_download, name="batch_download"),
    path("job/<uuid:job_id>/run/", views.job_run, name="job_run"),
    path("job/<uuid:job_id>/categories/", views.job_categories, name="job_categories"),
    path("job/<uuid:job_id>/prices/", views.job_prices, name="job_prices"),
    path("job/<uuid:job_id>/download/", views.job_download, name="job_download"),
]
