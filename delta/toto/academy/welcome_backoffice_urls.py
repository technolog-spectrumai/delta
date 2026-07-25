from django.urls import path

from . import backoffice_views as views

app_name = "backoffice_welcome"

urlpatterns = [
    path("", views.welcome_edit, name="welcome-edit"),
]
