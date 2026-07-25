from django.urls import path

from . import backoffice_views as views

app_name = "backoffice_courses"

urlpatterns = [
    path("", views.course_list, name="course-list"),
    path("new/", views.course_create, name="course-create"),
    path("<int:pk>/edit/", views.course_edit, name="course-edit"),
    path("<int:pk>/delete/", views.course_delete, name="course-delete"),
    path("<int:pk>/modules/", views.module_list, name="module-list"),
    path("<int:pk>/modules/new/", views.module_create, name="module-create"),
    path("<int:pk>/modules/reorder/", views.module_reorder, name="module-reorder"),
    path("modules/<int:pk>/edit/", views.module_edit, name="module-edit"),
    path("modules/<int:pk>/delete/", views.module_delete, name="module-delete"),
    path("modules/<int:pk>/lessons/new/", views.lesson_create, name="lesson-create"),
    path("modules/<int:pk>/lessons/reorder/", views.lesson_reorder, name="lesson-reorder"),
    path("modules/<int:pk>/scripts/new/", views.script_create, name="script-create"),
    path("lessons/<int:pk>/edit/", views.lesson_edit, name="lesson-edit"),
    path("lessons/<int:pk>/delete/", views.lesson_delete, name="lesson-delete"),
    path("scripts/<int:pk>/edit/", views.script_edit, name="script-edit"),
    path("scripts/<int:pk>/delete/", views.script_delete, name="script-delete"),
    # roster & enrolment
    path("<int:pk>/roster/", views.roster, name="roster"),
    path("<int:pk>/roster/enrol/", views.enrol_student, name="enrol-student"),
    path("<int:pk>/roster/unenrol/", views.unenrol_student, name="unenrol-student"),
    # cohorts
    path("<int:pk>/cohorts/new/", views.cohort_create, name="cohort-create"),
    path("cohorts/<int:pk>/edit/", views.cohort_edit, name="cohort-edit"),
    path("cohorts/<int:pk>/delete/", views.cohort_delete, name="cohort-delete"),
    path("cohorts/<int:pk>/members/add/", views.cohort_add_member, name="cohort-add-member"),
    path("cohorts/<int:pk>/members/remove/", views.cohort_remove_member, name="cohort-remove-member"),
]
