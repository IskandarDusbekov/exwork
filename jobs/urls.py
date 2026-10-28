from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("about/", views.landing, name="landing"),
    path("help/", views.help_page, name="help"),
    path("guide/seen/", views.guide_seen, name="guide_seen"),
    path("job/<uuid:job_id>/", views.job_detail, name="job"),
    path("folder/new/", views.project_create, name="project_create"),
    path("delete/<str:kind>/<uuid:work_id>/", views.work_delete, name="work_delete"),
    path("folder/<uuid:project_id>/", views.project_detail, name="project"),
    path("folder/<uuid:project_id>/price/", views.project_price, name="project_price"),
    path("folder/<uuid:project_id>/stop/", views.project_stop, name="project_stop"),
    path("folder/<uuid:project_id>/resume/", views.project_resume, name="project_resume"),
    path("folder/<uuid:project_id>/download/", views.project_download, name="project_download"),
    path("object/<uuid:batch_id>/", views.batch_detail, name="batch"),
    path("object/<uuid:batch_id>/sheet/", views.batch_sheet, name="batch_sheet"),
    path("object/<uuid:batch_id>/replan/", views.batch_replan, name="batch_replan"),
    path("object/<uuid:batch_id>/confirm/", views.batch_confirm, name="batch_confirm"),
    path("object/<uuid:batch_id>/download/", views.batch_download, name="batch_download"),
    path("job/<uuid:job_id>/run/", views.job_run, name="job_run"),
    path("job/<uuid:job_id>/categories/", views.job_categories, name="job_categories"),
    path("job/<uuid:job_id>/prices/", views.job_prices, name="job_prices"),
    path("job/<uuid:job_id>/not-found/", views.job_not_found, name="job_not_found"),
    path("job/<uuid:job_id>/table-checked/", views.job_table_checked, name="job_table_checked"),
    path("job/<uuid:job_id>/percents/", views.job_percents, name="job_percents"),
    path("job/<uuid:job_id>/columns/", views.job_columns, name="job_columns"),
    path("job/<uuid:job_id>/sheet/", views.job_sheet, name="job_sheet"),
    path("job/<uuid:job_id>/download/", views.job_download, name="job_download"),
]
