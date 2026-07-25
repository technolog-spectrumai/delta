from django.urls import path

from . import backoffice_views as views

app_name = "backoffice_quizzes"

urlpatterns = [
    path("", views.quiz_list, name="quiz-list"),
    path("new/", views.quiz_create, name="quiz-create"),
    path("<int:pk>/edit/", views.quiz_edit, name="quiz-edit"),
    path("<int:pk>/delete/", views.quiz_delete, name="quiz-delete"),
    path("<int:pk>/questions/", views.question_list, name="question-list"),
    path("<int:pk>/questions/new/", views.question_create, name="question-create"),
    path("<int:pk>/questions/reorder/", views.question_reorder, name="question-reorder"),
    path("questions/<int:pk>/edit/", views.question_edit, name="question-edit"),
    path("questions/<int:pk>/delete/", views.question_delete, name="question-delete"),
]
