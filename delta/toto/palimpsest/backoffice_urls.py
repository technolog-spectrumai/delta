from django.urls import path

from . import backoffice_views as views

app_name = "backoffice_notes"

urlpatterns = [
    path("", views.page_list, name="page-list"),
    path("new/", views.page_create, name="page-create"),
    path("tags/new/", views.tag_create, name="tag-create"),
    path("<int:pk>/edit/", views.page_edit, name="page-edit"),
    path("<int:pk>/delete/", views.page_delete, name="page-delete"),
    path("<int:pk>/sections/new/", views.section_create, name="section-create"),
    path("sections/<int:pk>/edit/", views.section_edit, name="section-edit"),
    path("sections/<int:pk>/delete/", views.section_delete, name="section-delete"),
]
