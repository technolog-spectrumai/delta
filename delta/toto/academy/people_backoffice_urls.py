from django.urls import path

from . import backoffice_views as views

app_name = "backoffice_people"

urlpatterns = [
    path("", views.people_list, name="people-list"),
    path("<int:pk>/promote/", views.promote_teacher, name="promote-teacher"),
    path("<int:pk>/demote/", views.demote_teacher, name="demote-teacher"),
]
