from django.urls import path

from . import backoffice_views as views

app_name = "backoffice_library"

urlpatterns = [
    path("", views.reference_list, name="reference-list"),
    path("collections/new/", views.collection_create, name="collection-create"),
    path("collections/<int:pk>/edit/", views.collection_edit, name="collection-edit"),
    path("collections/<int:pk>/delete/", views.collection_delete, name="collection-delete"),
    path("<str:kind>/new/", views.reference_create, name="reference-create"),
    path("<str:kind>/<int:pk>/edit/", views.reference_edit, name="reference-edit"),
    path("<str:kind>/<int:pk>/delete/", views.reference_delete, name="reference-delete"),
]
