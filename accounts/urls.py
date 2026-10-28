from django.urls import path

from . import views

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("register/", views.register_view, name="register"),
    path("logout/", views.logout_view, name="logout"),
    path("password/reset/", views.reset_request_view, name="reset_request"),
    path("password/reset/<uidb64>/<token>/", views.reset_confirm_view, name="reset_confirm"),
]
