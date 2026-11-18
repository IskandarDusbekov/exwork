from django.urls import path

from . import views

app_name = "news"

urlpatterns = [
    path("read/", views.read, name="read"),
]
