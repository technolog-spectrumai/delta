from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    path("", views.plan_list, name="plan_list"),
    path("moje/", views.my_subscription, name="my_subscription"),
    path("<slug:code>/subskrybuj/", views.subscribe, name="subscribe"),
]
