from django.conf import settings
from django.urls import path
from django.views.generic.base import RedirectView

from . import media_views
from . import views as academy_views
from .views import (
    CourseDetailView,
    CourseListView,
    PersonalPathCreateView,
    PersonalPathDetailView,
    PersonalPathListView,
    ScriptDetailView,
    SkillForestView,
    SkillTreeDetailView,
    StudentHomeView,
    StudentProgressView,
    TeacherDetailView,
    certificate_detail,
    certificate_sign,
)

app_name = "academy"

urlpatterns = [
    path(
        "",
        RedirectView.as_view(
            url="courses/",
            permanent=not settings.DEBUG,
        ),
    ),

    # Courses
    path(
        "courses/",
        CourseListView.as_view(),
        name="course-list",
    ),
    path(
        "courses/<slug:slug>/",
        CourseDetailView.as_view(),
        name="course-detail",
    ),
    path(
        "home/",
        StudentHomeView.as_view(),
        name="student-home",
    ),
    path(
        "progress/",
        StudentProgressView.as_view(),
        name="student-progress",
    ),
    path(
        "teachers/<int:pk>/",
        TeacherDetailView.as_view(),
        name="teacher-detail",
    ),

    # Scripts
    path(
        "scripts/<int:pk>/",
        ScriptDetailView.as_view(),
        name="script-detail",
    ),

    # Lesson media (enrollment-gated; video streams with Range for seeking)
    path(
        "lessons/<int:pk>/video/",
        media_views.lesson_video,
        name="lesson-video",
    ),
    path(
        "lessons/<int:pk>/notes/",
        media_views.lesson_notes,
        name="lesson-notes",
    ),
    path(
        "lessons/<int:pk>/presentation/",
        academy_views.lesson_presentation,
        name="lesson-presentation",
    ),

    # Course metrics
    path(
        "courses/<slug:slug>/metrics/",
        academy_views.course_metrics,
        name="course-metrics",
    ),

    # Skill forest / trees
    path(
        "skills/",
        SkillForestView.as_view(),
        name="skill-forest",
    ),
    path(
        "skills/<slug:slug>/",
        SkillTreeDetailView.as_view(),
        name="skill-tree-detail",
    ),

    # Personalized learning paths
    path(
        "paths/",
        PersonalPathListView.as_view(),
        name="personal-path-list",
    ),
    path(
        "paths/new/",
        PersonalPathCreateView.as_view(),
        name="personal-path-create",
    ),
    path(
        "paths/<int:pk>/",
        PersonalPathDetailView.as_view(),
        name="personal-path-detail",
    ),
    path(
        "paths/<int:pk>/steps/<int:step_pk>/toggle/",
        academy_views.personal_path_step_toggle,
        name="personal-path-step-toggle",
    ),
    path(
        "paths/<int:pk>/steps/<int:step_pk>/delete/",
        academy_views.personal_path_step_delete,
        name="personal-path-step-delete",
    ),
    path(
        "paths/<int:pk>/tasks/add/",
        academy_views.personal_path_task_add,
        name="personal-path-task-add",
    ),
    path(
        "paths/<int:pk>/regenerate/",
        academy_views.personal_path_regenerate,
        name="personal-path-regenerate",
    ),
    path(
        "paths/<int:pk>/archive/",
        academy_views.personal_path_archive,
        name="personal-path-archive",
    ),

    # Self-enrollment (subscription-gated)
    path(
        "courses/<slug:slug>/enroll/",
        academy_views.course_enroll,
        name="course-enroll",
    ),

    # Certificates
    path(
        "certificates/<uuid:uuid>/",
        certificate_detail,
        name="certificate-detail",
    ),
    path(
        "certificates/<uuid:uuid>/sign/",
        certificate_sign,
        name="certificate-sign",
    ),
]
