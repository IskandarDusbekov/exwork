from django.urls import path

from . import views

app_name = "support"

urlpatterns = [
    path("", views.page, name="page"),
    path("messages/", views.messages_json, name="messages"),
    path("unread/", views.unread_json, name="unread"),
    path("send/", views.send, name="send"),
    path("file/<int:pk>/", views.attachment, name="file"),
]
