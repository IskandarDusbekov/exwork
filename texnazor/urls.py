from django.urls import path

from . import views

app_name = "texnazor"

urlpatterns = [
    path("", views.index, name="index"),
    path("new/", views.create, name="create"),
    path("<uuid:obj_id>/", views.detail, name="object"),
    path("<uuid:obj_id>/state/", views.state, name="state"),
    path("<uuid:obj_id>/search/", views.search, name="search"),
    path("<uuid:obj_id>/rows/", views.rows, name="rows"),
    path("<uuid:obj_id>/section/<int:section_id>/fill/", views.fill_section, name="fill"),
    path("<uuid:obj_id>/done/", views.set_done, name="done"),
    path("<uuid:obj_id>/fill/", views.fill_items, name="fill_items"),
    path("<uuid:obj_id>/item/<int:item_id>/correct/", views.correct, name="correct"),
    path("<uuid:obj_id>/period/<int:period_id>/paid/", views.set_paid, name="paid"),
    path("<uuid:obj_id>/period/", views.add_period, name="period_add"),
    path("<uuid:obj_id>/period/<int:period_id>/close/", views.close_period, name="period_close"),
    path("<uuid:obj_id>/period/<int:period_id>/f2/", views.f2_download, name="f2"),
    path("<uuid:obj_id>/delete/", views.delete, name="delete"),
    path("<uuid:obj_id>/period/<int:period_id>/delete/", views.delete_period, name="period_delete"),
    path("<uuid:obj_id>/correction/<int:correction_id>/revert/", views.revert_correction, name="revert"),
]
