from django.urls import path

from . import views

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("login/code/", views.login_code_view, name="login_code"),
    path("account/2fa/", views.twofa_setup_view, name="twofa_setup"),
    path("register/", views.register_view, name="register"),
    path("logout/", views.logout_view, name="logout"),
    path("account/verify/", views.verify_email_view, name="verify_email"),
    path("account/verify/<str:token>/", views.verify_link_view, name="verify_link"),
    path("password/reset/", views.reset_request_view, name="reset_request"),
    path("password/reset/code/", views.reset_code_view, name="reset_code"),
    path("password/reset/<uidb64>/<token>/", views.reset_confirm_view, name="reset_confirm"),
]
