from django.urls import path

from . import views, views_my

app_name = "texnazor"

urlpatterns = [
    path("", views.index, name="index"),
    path("new/", views.create, name="create"),
    path("my/", views_my.my_list, name="my_list"),
    path("my/new/", views_my.my_new, name="my_new"),
    path("my/<uuid:doc_id>/", views_my.my_detail, name="my_detail"),
    path("my/<uuid:doc_id>/edit/", views_my.my_edit, name="my_edit"),
    path("my/<uuid:doc_id>/state/", views_my.my_state, name="my_state"),
    path("my/<uuid:doc_id>/file/", views_my.my_file, name="my_file"),
    path("my/<uuid:doc_id>/delete/", views_my.my_delete, name="my_delete"),
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
    path("<uuid:obj_id>/period/<int:period_id>/f3/", views.f3_download, name="f3"),
    path("<uuid:obj_id>/period/<int:period_id>/f3/save/", views.f3_period, name="f3_period"),
    path("<uuid:obj_id>/f3/", views.f3_settings, name="f3_settings"),
    path("<uuid:obj_id>/delete/", views.delete, name="delete"),
    path("<uuid:obj_id>/period/<int:period_id>/delete/", views.delete_period, name="period_delete"),
    path("<uuid:obj_id>/correction/<int:correction_id>/revert/", views.revert_correction, name="revert"),
]
