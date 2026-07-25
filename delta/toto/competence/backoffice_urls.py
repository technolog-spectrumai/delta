from django.urls import path

from . import backoffice_views as views

app_name = "backoffice_skills"

urlpatterns = [
    path("", views.skill_overview, name="skill-overview"),
    path("groups/new/", views.group_create, name="group-create"),
    path("groups/<int:pk>/edit/", views.group_edit, name="group-edit"),
    path("groups/<int:pk>/delete/", views.group_delete, name="group-delete"),
    path("badges/new/", views.badge_create, name="badge-create"),
    path("badges/<int:pk>/edit/", views.badge_edit, name="badge-edit"),
    path("badges/<int:pk>/delete/", views.badge_delete, name="badge-delete"),
    path("badges/quick-create/", views.badge_quick_create, name="badge-quick-create"),
]
