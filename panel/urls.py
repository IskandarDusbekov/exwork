from django.urls import path

from . import views

app_name = "panel"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("live/", views.live, name="live"),
    path("stats/", views.stats, name="stats"),
    path("speed/", views.speed, name="speed"),
    path("ip/", views.ip_block, name="ip_block"),
    path("users/", views.users, name="users"),
    path("users/<int:pk>/", views.user_detail, name="user"),
    path("users/<int:pk>/toggle/<str:field>/", views.user_toggle, name="user_toggle"),
    path("users/<int:pk>/unlock/", views.user_unlock, name="user_unlock"),
    path("users/<int:pk>/sessions/end/", views.session_revoke, name="sessions_revoke"),
    path("users/<int:pk>/sessions/<int:sid>/end/", views.session_revoke, name="session_revoke"),
    path("work/<str:kind>/<uuid:work_id>/", views.work, name="work"),
    path("work/<str:kind>/<uuid:work_id>/file/<int:i>/", views.work_file, name="work_file"),
    path("work/<str:kind>/<uuid:work_id>/result/", views.work_result, name="work_result"),
    path("work/folder/<uuid:work_id>/stop/", views.work_stop, name="work_stop"),
    path("processes/", views.processes, name="processes"),
    path("files/", views.files, name="files"),
    path("log/", views.log, name="log"),
    path("errors/", views.errors, name="errors"),
    path("errors/test/", views.errors_test, name="errors_test"),
    path("errors/clear/", views.errors_clear, name="errors_clear"),
]
