from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("", views.profile, name="profile"),
    path("buy/<int:pk>/", views.buy, name="buy"),
    path("next/cancel/", views.cancel_next, name="cancel_next"),
    path("renew/", views.renew, name="renew"),
    path("topup/", views.topup, name="topup"),
    path("click/prepare/", views.click_prepare, name="click_prepare"),
    path("click/complete/", views.click_complete, name="click_complete"),
]
