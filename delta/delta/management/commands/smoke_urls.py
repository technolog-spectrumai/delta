"""Three-persona URL smoke sweep — delta's local early-warning harness.

Enumerates every URL pattern of the host (plus the admin registry for the
staff persona), fills parameterized routes from seeded objects, probes each
with the Django test client (or over HTTP with --base-url) and grades the
response against per-persona expectations:

  * any 5xx fails; 404/400 fail unless explicitly overridden
  * an authenticated persona bounced to the login page fails
  * core product pages must return exactly 200 (MUST_200)
  * 405 passes (GET sweep hitting POST-only endpoints)
  * 401/403 pass for anon/student (the auth wall doing its job) and only
    warn for staff (token-auth APIs legitimately reject session staff)

Run via scripts/ui_test.sh (seeds a scratch DB first) or by hand against a
seeded DB:  python delta/manage.py smoke_urls [--filter academy:] [--list-routes]
"""

import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.test import Client
from django.urls import NoReverseMatch, URLPattern, URLResolver, get_resolver, reverse

# ---------------------------------------------------------------------------
# Static route policy
# ---------------------------------------------------------------------------

# Routes never probed. Key -> reason (reported in the summary).
SKIP = {
    "sso:password_reset_confirm": "unfillable uidb64/token pair",
    "backup:stored_backup_stream": "unfillable magic token",
    # markdownx's ImageUploadView is a BaseFormView with template_name left
    # commented out upstream: GET raises by third-party design, not a delta bug.
    "markdownx_upload": "third-party view 500s on GET by design",
    # Social login 404s by design until a provider's OAuth credentials are set.
    "sso:social_login": "social provider redirect; needs configured credentials",
    "sso:social_callback": "social provider callback; needs configured credentials",
}

# Staff-gated pages: the student persona being turned away is correct behavior.
STAFF_ONLY = {
    "academy:course-metrics",
    "quizzes:quiz-metrics",
    "sso:admin_test_login",
    # Teacher back office (Panel autorski) — teacher/staff only; student -> 404.
    "backoffice:dashboard",
    "backoffice_quizzes:quiz-list",
    "backoffice_quizzes:quiz-create",
    "backoffice_quizzes:quiz-edit",
    "backoffice_quizzes:quiz-delete",
    "backoffice_quizzes:question-list",
    "backoffice_quizzes:question-create",
    "backoffice_quizzes:question-reorder",
    "backoffice_quizzes:question-edit",
    "backoffice_quizzes:question-delete",
    # skills & badges
    "backoffice_skills:skill-overview", "backoffice_skills:group-create",
    "backoffice_skills:group-edit", "backoffice_skills:group-delete",
    "backoffice_skills:group-reorder", "backoffice_skills:badge-reorder",
    "backoffice_skills:badge-create", "backoffice_skills:badge-edit",
    "backoffice_skills:badge-delete", "backoffice_skills:badge-quick-create",
    # courses
    "backoffice_courses:course-list", "backoffice_courses:course-create",
    "backoffice_courses:course-edit", "backoffice_courses:course-delete",
    "backoffice_courses:module-list", "backoffice_courses:module-create",
    "backoffice_courses:module-reorder", "backoffice_courses:module-edit",
    "backoffice_courses:module-delete", "backoffice_courses:lesson-create",
    "backoffice_courses:lesson-reorder", "backoffice_courses:lesson-edit",
    "backoffice_courses:lesson-delete", "backoffice_courses:script-create",
    "backoffice_courses:script-edit", "backoffice_courses:script-delete",
    "backoffice_courses:roster", "backoffice_courses:enrol-student",
    "backoffice_courses:unenrol-student", "backoffice_courses:cohort-create",
    "backoffice_courses:cohort-edit", "backoffice_courses:cohort-delete",
    "backoffice_courses:cohort-add-member", "backoffice_courses:cohort-remove-member",
    "backoffice_courses:cohort-award-certificates",
    "backoffice_courses:student-detail", "backoffice_courses:student-complete",
    "backoffice_courses:student-uncomplete", "backoffice_courses:student-award-badge",
    "backoffice_courses:student-revoke-badge",
    # notes
    "backoffice_notes:page-list", "backoffice_notes:page-create",
    "backoffice_notes:tag-create", "backoffice_notes:page-edit",
    "backoffice_notes:page-delete", "backoffice_notes:section-create",
    "backoffice_notes:section-edit", "backoffice_notes:section-delete",
    # library
    "backoffice_library:reference-list", "backoffice_library:collection-create",
    "backoffice_library:collection-edit", "backoffice_library:collection-delete",
    "backoffice_library:reference-create", "backoffice_library:reference-edit",
    "backoffice_library:reference-delete",
    # people / teachers
    "backoffice_people:people-list", "backoffice_people:promote-teacher",
    "backoffice_people:demote-teacher",
    # welcome-page editor
    "backoffice_welcome:welcome-edit",
}

# (key) -> extra allowed statuses for authenticated personas (anon is already
# lenient). OIDC endpoints 400 without client_id/state — that is their contract;
# the status endpoints 400/404 without their required query params likewise.
OVERRIDES = {
    "sso:authorize": {400},
    "sso:consent": {400},
    "sso:admin_test_callback": {400},
    "vault:encrypt_status": {400},
    "vault:zip_status": {400},
    "events:event_availability_api": {404},
}

# Owner-scoped views: a persona that doesn't own the object is 404'd by
# design (ownership enforcement working) — key -> personas allowed to 404.
OWNER_404_OK = {
    "academy:personal-path-detail": {"staff"},
    "academy:personal-path-regenerate": {"staff"},
    "academy:personal-path-archive": {"staff"},
    "academy:personal-path-task-add": {"staff"},
    "academy:personal-path-step-toggle": {"staff"},
    "academy:personal-path-step-delete": {"staff"},
    "vault:api_file_detail": {"student"},
    "vault:api_file_content": {"student"},
    "vault:api_file_download": {"student"},
    "vault:bucket_connection_url": {"student"},
    "vault:copy_files": {"student"},
}

# Routes where a redirect to the login page is legitimate even when
# authenticated (password reset self-disables without an email backend).
LOGIN_REDIRECT_OK = {"sso:password_reset"}

# Teeth beyond "didn't 500": the product's core pages must render outright.
MUST_200 = {
    "anon": {
        "core:welcome", "sso:login", "academy:course-list", "quizzes:quiz-list",
        "palimpsest:page_list", "library:book_list", "subscriptions:plan_list",
        "api:health",
    },
    "student": {
        "core:dashboard", "academy:course-list", "academy:course-detail",
        "academy:student-home", "academy:student-progress", "academy:personal-path-list",
        "academy:personal-path-detail", "quizzes:quiz-detail",
        "subscriptions:my_subscription",
    },
    "staff": {
        "admin:index", "core:dashboard", "academy:course-list",
    },
}

# Probing these logs the persona out — use a throwaway client.
SESSION_DESTROYING = {"sso:logout", "core:logout", "api:logout"}

LOGIN_PATH = "/sso/login/"

SMOKE_STUDENT = "ui-smoke-student"


# ---------------------------------------------------------------------------
# Seeded-object helpers (defensive: missing model/field -> None -> skip)
# ---------------------------------------------------------------------------

def _model(label):
    try:
        return apps.get_model(label)
    except LookupError:
        return None


def _first(label, order="pk", **filters):
    model = _model(label)
    if model is None:
        return None
    try:
        return model.objects.filter(**filters).order_by(order).first()
    except Exception:
        return None


def _fk_name(model, target_model):
    for f in model._meta.fields:
        if f.is_relation and f.related_model is target_model:
            return f.name
    return None


def _student_path():
    """The smoke student's active PersonalPath (created by ensure_personas)."""
    PersonalPath = _model("academy.PersonalPath")
    if PersonalPath is None:
        return None
    return (
        PersonalPath.objects.filter(
            student__person__user__username=SMOKE_STUDENT, status="active"
        ).first()
        or PersonalPath.objects.filter(status="active").first()
    )


def _path_step():
    path = _student_path()
    Step = _model("academy.PersonalPathStep")
    if path is None or Step is None:
        return None
    fk = _fk_name(Step, type(path))
    if fk is None:
        return None
    return Step.objects.filter(**{fk: path}).order_by("pk").first()


# key -> (required, callable() -> kwargs | None). required=True + no object
# means the seed contract is broken -> FAIL; required=False -> skip w/ warning.
PARAM_SOURCES = {
    # academy — seeded by ingress_academy (FULL)
    "academy:course-detail": (True, lambda: _kw(_first("academy.Course", is_published=True), slug="slug")),
    "academy:course-metrics": (True, lambda: _kw(_first("academy.Course", is_published=True), slug="slug")),
    "academy:course-enroll": (True, lambda: _kw(_first("academy.Course", is_published=True), slug="slug")),
    "academy:teacher-detail": (True, lambda: _kw(_first("academy.Teacher"), pk="pk")),
    "academy:skill-tree-detail": (True, lambda: _kw(_first("competence.SkillGroup"), slug="slug")),
    "academy:script-detail": (False, lambda: _kw(_first("academy.Script"), pk="pk")),
    "academy:certificate-detail": (False, lambda: _kw(_first("academy.Certificate"), uuid="uuid")),
    "academy:certificate-sign": (False, lambda: _kw(_first("academy.Certificate"), uuid="uuid")),
    # personal paths — created for the student persona by ensure_personas
    "academy:personal-path-detail": (True, lambda: _kw(_student_path(), pk="pk")),
    "academy:personal-path-regenerate": (True, lambda: _kw(_student_path(), pk="pk")),
    "academy:personal-path-archive": (True, lambda: _kw(_student_path(), pk="pk")),
    "academy:personal-path-task-add": (True, lambda: _kw(_student_path(), pk="pk")),
    "academy:personal-path-step-toggle": (True, lambda: _kw2_step()),
    "academy:personal-path-step-delete": (True, lambda: _kw2_step()),
    # lesson media (enrollment-gated; unseeded -> skip)
    "academy:lesson-video": (False, lambda: _kw(_first("academy.Lesson", video_file__isnull=False), pk="pk")),
    "academy:lesson-notes": (False, lambda: _kw(_first("academy.Lesson", notes_file__isnull=False), pk="pk")),
    # quizzes — ingress_quizzes / ingress_academy
    "quizzes:quiz-detail": (True, lambda: _kw(_first("quizzes.Quiz", is_published=True), pk="pk")),
    "quizzes:quiz-practice": (True, lambda: _kw(_first("quizzes.Quiz", is_published=True), pk="pk")),
    "quizzes:quiz-metrics": (True, lambda: _kw(_first("quizzes.Quiz", is_published=True), pk="pk")),
    # quizzes back office (Panel autorski) — quiz-pk and question-pk routes
    "backoffice_quizzes:quiz-edit": (True, lambda: _kw(_first("quizzes.Quiz"), pk="pk")),
    "backoffice_quizzes:quiz-delete": (True, lambda: _kw(_first("quizzes.Quiz"), pk="pk")),
    "backoffice_quizzes:question-list": (True, lambda: _kw(_first("quizzes.Quiz"), pk="pk")),
    "backoffice_quizzes:question-create": (True, lambda: _kw(_first("quizzes.Quiz"), pk="pk")),
    "backoffice_quizzes:question-reorder": (True, lambda: _kw(_first("quizzes.Quiz"), pk="pk")),
    "backoffice_quizzes:question-edit": (True, lambda: _kw(_first("quizzes.QuizQuestion"), pk="pk")),
    "backoffice_quizzes:question-delete": (True, lambda: _kw(_first("quizzes.QuizQuestion"), pk="pk")),
    # back office — skills & badges (competence)
    "backoffice_skills:group-edit": (True, lambda: _kw(_first("competence.SkillGroup"), pk="pk")),
    "backoffice_skills:group-delete": (True, lambda: _kw(_first("competence.SkillGroup"), pk="pk")),
    "backoffice_skills:badge-reorder": (True, lambda: _kw(_first("competence.SkillGroup"), pk="pk")),
    "backoffice_skills:badge-edit": (False, lambda: _kw(_first("competence.SkillBadge"), pk="pk")),
    "backoffice_skills:badge-delete": (False, lambda: _kw(_first("competence.SkillBadge"), pk="pk")),
    # back office — courses (academy)
    "backoffice_courses:course-edit": (True, lambda: _kw(_first("academy.Course"), pk="pk")),
    "backoffice_courses:course-delete": (True, lambda: _kw(_first("academy.Course"), pk="pk")),
    "backoffice_courses:module-list": (True, lambda: _kw(_first("academy.Course"), pk="pk")),
    "backoffice_courses:module-create": (True, lambda: _kw(_first("academy.Course"), pk="pk")),
    "backoffice_courses:module-reorder": (True, lambda: _kw(_first("academy.Course"), pk="pk")),
    "backoffice_courses:module-edit": (False, lambda: _kw(_first("academy.CourseModule"), pk="pk")),
    "backoffice_courses:module-delete": (False, lambda: _kw(_first("academy.CourseModule"), pk="pk")),
    "backoffice_courses:lesson-create": (False, lambda: _kw(_first("academy.CourseModule"), pk="pk")),
    "backoffice_courses:lesson-reorder": (False, lambda: _kw(_first("academy.CourseModule"), pk="pk")),
    "backoffice_courses:script-create": (False, lambda: _kw(_first("academy.CourseModule"), pk="pk")),
    "backoffice_courses:lesson-edit": (False, lambda: _kw(_first("academy.Lesson"), pk="pk")),
    "backoffice_courses:lesson-delete": (False, lambda: _kw(_first("academy.Lesson"), pk="pk")),
    "backoffice_courses:script-edit": (False, lambda: _kw(_first("academy.Script"), pk="pk")),
    "backoffice_courses:script-delete": (False, lambda: _kw(_first("academy.Script"), pk="pk")),
    # roster & cohorts (course pk)
    "backoffice_courses:roster": (True, lambda: _kw(_first("academy.Course"), pk="pk")),
    "backoffice_courses:enrol-student": (True, lambda: _kw(_first("academy.Course"), pk="pk")),
    "backoffice_courses:unenrol-student": (True, lambda: _kw(_first("academy.Course"), pk="pk")),
    "backoffice_courses:cohort-create": (True, lambda: _kw(_first("academy.Course"), pk="pk")),
    # cohorts (cohort pk) — none seeded -> skip
    "backoffice_courses:cohort-edit": (False, lambda: _kw(_first("academy.Cohort"), pk="pk")),
    "backoffice_courses:cohort-delete": (False, lambda: _kw(_first("academy.Cohort"), pk="pk")),
    "backoffice_courses:cohort-add-member": (False, lambda: _kw(_first("academy.Cohort"), pk="pk")),
    "backoffice_courses:cohort-remove-member": (False, lambda: _kw(_first("academy.Cohort"), pk="pk")),
    "backoffice_courses:cohort-award-certificates": (False, lambda: _kw(_first("academy.Cohort"), pk="pk")),
    # per-student pages (course pk + student pk)
    "backoffice_courses:student-detail": (False, lambda: _kw_course_student()),
    "backoffice_courses:student-complete": (False, lambda: _kw_course_student()),
    "backoffice_courses:student-uncomplete": (False, lambda: _kw_course_student()),
    "backoffice_courses:student-award-badge": (False, lambda: _kw_course_student()),
    "backoffice_courses:student-revoke-badge": (False, lambda: _kw_course_student()),
    # back office — people / teachers (academy)
    "backoffice_people:promote-teacher": (False, lambda: _kw(_first("people.Person"), pk="pk")),
    "backoffice_people:demote-teacher": (False, lambda: _kw(_first("people.Person"), pk="pk")),
    # back office — notes (palimpsest)
    "backoffice_notes:page-edit": (True, lambda: _kw(_first("palimpsest.Page"), pk="pk")),
    "backoffice_notes:page-delete": (True, lambda: _kw(_first("palimpsest.Page"), pk="pk")),
    "backoffice_notes:section-create": (True, lambda: _kw(_first("palimpsest.Page"), pk="pk")),
    "backoffice_notes:section-edit": (False, lambda: _kw(_first("palimpsest.Section"), pk="pk")),
    "backoffice_notes:section-delete": (False, lambda: _kw(_first("palimpsest.Section"), pk="pk")),
    # back office — library
    "backoffice_library:reference-create": (True, lambda: {"kind": "book"}),
    "backoffice_library:reference-edit": (True, lambda: _lib_ref()),
    "backoffice_library:reference-delete": (True, lambda: _lib_ref()),
    "backoffice_library:collection-edit": (False, lambda: _kw(_first("library.LibraryCollection"), pk="pk")),
    "backoffice_library:collection-delete": (False, lambda: _kw(_first("library.LibraryCollection"), pk="pk")),
    # palimpsest — ingress_palimpsest
    "palimpsest:page_detail": (True, lambda: _kw(_first("palimpsest.Page"), slug="slug")),
    "palimpsest:page_update": (True, lambda: _kw(_first("palimpsest.Page"), slug="slug")),
    "palimpsest:page_delete": (True, lambda: _kw(_first("palimpsest.Page"), slug="slug")),
    "palimpsest:section_create": (True, lambda: _kw(_first("palimpsest.Page"), slug="slug")),
    "palimpsest:section_create_legacy": (True, lambda: _kw(_first("palimpsest.Page"), slug="slug")),
    "palimpsest:section_update": (True, lambda: _kw_section()),
    "palimpsest:section_delete": (True, lambda: _kw_section()),
    "palimpsest:section_update_legacy": (True, lambda: _kw_section()),
    "palimpsest:section_delete_legacy": (True, lambda: _kw_section()),
    "palimpsest:page_list_by_tag": (True, lambda: _kw(_first("palimpsest.Tag"), tag_slug="slug")),
    # library — ingress_library
    "library:book_detail": (True, lambda: _kw(_first("library.Book"), slug="slug")),
    "library:article_detail": (True, lambda: _kw(_first("library.Article"), slug="slug")),
    # subscriptions — ingress_subscriptions
    "subscriptions:subscribe": (True, lambda: _kw(_first("subscriptions.SubscriptionPlan", order="order"), code="code")),
    # socialhub — ingress_socialhub (FULL)
    "socialhub:profile_details": (True, lambda: _kw_person_slug()),
    "socialhub:api_profile_detail": (True, lambda: _kw_person_slug()),
    "socialhub:community_detail": (True, lambda: _kw(_first("socialhub.Community"), slug="slug")),
    "socialhub:administrata": (True, lambda: _kw(_first("socialhub.Community"), slug="slug")),
    "socialhub:constitution_detail": (True, lambda: _kw(_first("socialhub.Community"), slug="slug")),
    "socialhub:constitution_sign": (True, lambda: _kw(_first("socialhub.Community"), slug="slug")),
    "socialhub:community_chain_graph_data": (True, lambda: _kw(_first("socialhub.Community"), slug="slug")),
    "socialhub:community_news_create": (True, lambda: _kw(_first("socialhub.Community"), community_slug="slug")),
    "socialhub:community_org_chart_data_by_slug": (False, lambda: _kw(_first("socialhub.Community"), company_slug="slug")),
    "socialhub:api_community_detail": (True, lambda: _kw(_first("socialhub.Community"), slug="slug")),
    "socialhub:api_community_org_chart": (True, lambda: _kw(_first("socialhub.Community"), slug="slug")),
    "socialhub:community_news_update": (False, lambda: _kw(_first("socialhub.CommunityNewsPost"), pk="pk")),
    "socialhub:community_news_delete": (False, lambda: _kw(_first("socialhub.CommunityNewsPost"), pk="pk")),
    "socialhub:application_success": (False, lambda: _kw_any_username()),
    "socialhub:membership_verification": (False, lambda: _kw_any_username()),
    "socialhub:reference_request": (False, lambda: _kw(_first("socialhub.MembershipApplication"), application_id="pk")),
    "socialhub:reference_next": (False, lambda: _kw(_first("socialhub.MembershipApplication"), application_id="pk")),
    "socialhub:reference_accept": (False, lambda: _kw(_first("socialhub.Reference"), ref_id="pk")),
    "socialhub:reference_reject": (False, lambda: _kw(_first("socialhub.Reference"), ref_id="pk")),
    # vault — ingress_vault
    "vault:public_file": (False, lambda: _kw_public_file()),
    "vault:bucket_metrics": (True, lambda: _kw(_first("vault.Bucket"), bucket_slug="slug")),
    "vault:copy_files": (True, lambda: _kw(_first("vault.Bucket"), source_slug="slug")),
    "vault:copy_files_ajax": (True, lambda: _kw(_first("vault.Bucket"), source_slug="slug")),
    "vault:bucket_connection_url": (True, lambda: _kw(_first("vault.Bucket"), bucket_slug="slug")),
    "vault:gateway_page": (True, lambda: _kw_gateway()),
    "vault:gateway_upload": (True, lambda: _kw_gateway()),
    "vault:api_file_detail": (False, lambda: _kw(_first("vault.VaultFile"), key="key")),
    "vault:api_file_content": (False, lambda: _kw(_first("vault.VaultFile"), key="key")),
    "vault:api_file_download": (False, lambda: _kw(_first("vault.VaultFile"), key="key")),
    "vault:api_file_encrypt": (False, lambda: _kw(_first("vault.VaultFile"), key="key")),
    "vault:api_file_decrypt": (False, lambda: _kw(_first("vault.VaultFile"), key="key")),
    "vault:api_directory_delete": (True, lambda: _kw(_first("vault.VaultDirectory"), pk="pk")),
    # events — not seeded under BUILD_GEO=0 (ingress excluded by settings)
    "events:event_detail": (False, lambda: _kw(_first("events.Event"), pk="pk")),
    "events:event_plan": (False, lambda: _kw(_first("events.Event"), pk="pk")),
    "events:invite_respond": (False, lambda: _kw(_first("events.EventInvite"), pk="pk")),
    "events:api_detail": (False, lambda: _kw(_first("events.Event"), pk="pk")),
    "events:api_invite": (False, lambda: _kw(_first("events.Event"), pk="pk")),
    "events:api_invite_respond": (False, lambda: _kw(_first("events.EventInvite"), pk="pk")),
    # sso / gervazy
    "sso:admin_test_login": (False, lambda: _kw(_first("sso_master.RelyingParty"), pk="pk")),
    "gervazy:initialize_strongbox": (True, lambda: _kw(_first("gervazy.UserStrongbox"), pk="pk")),
    # vod — native player page for a Vault video/audio file (public sample if any)
    "vod:vault_file_play": (False, lambda: _kw(
        _first("vault.VaultFile", is_encrypted=False, is_public=True), file_pk="pk")),
}


def _kw(obj, **mapping):
    """{'kwarg': 'attr'} -> {'kwarg': obj.attr}, or None when obj is missing."""
    if obj is None:
        return None
    out = {}
    for kwarg, attr in mapping.items():
        value = getattr(obj, attr, None)
        if value in (None, ""):
            return None
        out[kwarg] = value
    return out


def _lib_ref():
    """{kind, pk} for a seeded library Book (back-office reference edit/delete)."""
    book = _first("library.Book")
    return {"kind": "book", "pk": book.pk} if book else None


def _kw_course_student():
    """{pk: <course>, student_pk: <student>} for the per-student back-office pages."""
    course = _first("academy.Course")
    student = _first("academy.Student")
    if course is None or student is None:
        return None
    return {"pk": course.pk, "student_pk": student.pk}


def _kw2_step():
    path, step = _student_path(), _path_step()
    if path is None or step is None:
        return None
    return {"pk": path.pk, "step_pk": step.pk}


def _kw_section():
    page = _first("palimpsest.Page")
    Section = _model("palimpsest.Section")
    if page is None or Section is None:
        return None
    fk = _fk_name(Section, type(page))
    section = Section.objects.filter(**{fk: page}).order_by("pk").first() if fk else None
    if section is None:
        return None
    return {"slug": page.slug, "pk": section.pk}


def _kw_person_slug():
    Person = _model("people.Person")
    if Person is None:
        return None
    person = Person.objects.exclude(slug="").exclude(slug__isnull=True).order_by("pk").first()
    return {"slug": person.slug} if person else None


def _kw_any_username():
    user = get_user_model().objects.order_by("pk").first()
    return {"username": user.username} if user else None


def _kw_public_file():
    vf = _first("vault.VaultFile", is_public=True) or _first("vault.VaultFile")
    if vf is None:
        return None
    bucket = getattr(vf, "bucket", None)
    key = getattr(vf, "key", None)
    if bucket is None or not key:
        return None
    return {"bucket_slug": bucket.slug, "key": key}


def _kw_gateway():
    gw = _first("vault.FileGateway")
    dir_pk = getattr(gw, "directory_id", None) if gw else None
    if dir_pk is None:
        directory = _first("vault.VaultDirectory")
        dir_pk = directory.pk if directory else None
    return {"dir_pk": dir_pk} if dir_pk else None


# ---------------------------------------------------------------------------
# URL discovery
# ---------------------------------------------------------------------------

@dataclass
class Probe:
    key: str            # "ns:name", literal path for unnamed routes
    path: str | None    # resolved path, None when skipped
    note: str = ""
    skipped: str = ""   # non-empty -> skip reason


def _walk(resolver, ns=()):
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            if entry.namespace == "admin":
                continue  # synthesized separately for the staff persona
            child = ns + ((entry.namespace,) if entry.namespace else ())
            yield from _walk(entry, child)
        else:
            yield ns, entry


def discover(stderr):
    """All non-admin probes, param routes filled from seeded objects."""
    probes = []
    for ns, entry in _walk(get_resolver()):
        converters = dict(getattr(entry.pattern, "converters", {}))
        params = set(converters) or set(entry.pattern.regex.groupindex)
        if entry.name:
            key = ":".join(ns + (entry.name,))
        else:
            key = None

        if key and key in SKIP:
            probes.append(Probe(key=key, path=None, skipped=SKIP[key]))
            continue

        if not params:
            if key:
                try:
                    probes.append(Probe(key=key, path=reverse(key)))
                except NoReverseMatch as exc:
                    probes.append(Probe(key=key, path=None,
                                        skipped=f"reverse failed: {exc}"))
            else:
                # Unnamed paramless routes (root/app redirects): literal path.
                literal = "/" + str(entry.pattern).lstrip("^").rstrip("$")
                probes.append(Probe(key=f"<unnamed {literal}>", path=literal))
            continue

        if key is None:
            probes.append(Probe(key=f"<unnamed:{entry.pattern}>", path=None,
                                skipped="unnamed parameterized route"))
            continue

        if key not in PARAM_SOURCES:
            # The mechanism that forces every future param route through the
            # harness: unmapped routes are failures, not silence.
            probes.append(Probe(key=key, path=None,
                                skipped="UNMAPPED: add to PARAM_SOURCES or SKIP"))
            continue

        required, source = PARAM_SOURCES[key]
        try:
            kwargs = source()
        except Exception as exc:  # a broken source is a harness bug — surface it
            probes.append(Probe(key=key, path=None, skipped=f"source error: {exc!r}"))
            continue
        if kwargs is None:
            reason = "seed contract broken: no object" if required else "no sample data"
            probes.append(Probe(key=key, path=None, skipped=reason))
            continue
        try:
            probes.append(Probe(key=key, path=reverse(key, kwargs=kwargs)))
        except NoReverseMatch as exc:
            probes.append(Probe(key=key, path=None, skipped=f"reverse failed: {exc}"))
    return probes


def admin_probes():
    """Staff-only surface synthesized from the admin registry."""
    probes = [Probe(key="admin:index", path=reverse("admin:index")),
              Probe(key="admin:login", path=reverse("admin:login"))]
    seen_apps = set()
    for model in admin.site._registry:
        o = model._meta
        for page in ("changelist", "add"):
            name = f"admin:{o.app_label}_{o.model_name}_{page}"
            try:
                probes.append(Probe(key=name, path=reverse(name)))
            except NoReverseMatch:
                continue
        if o.app_label not in seen_apps:
            seen_apps.add(o.app_label)
            probes.append(Probe(
                key=f"admin:app_list:{o.app_label}",
                path=reverse("admin:app_list", kwargs={"app_label": o.app_label})))
    return probes


# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------

def _person_user_field():
    User = get_user_model()
    Person = _model("people.Person")
    for f in Person._meta.fields:
        if f.is_relation and f.related_model is User:
            return f.name
    return None


def ensure_personas(stdout):
    """Idempotently create the student persona (+ its PersonalPath) and
    resolve the seeded staff superuser."""
    User = get_user_model()
    Person = _model("people.Person")

    student, _ = User.objects.get_or_create(
        username=SMOKE_STUDENT, defaults={"email": "smoke@localhost"})
    user_field = _person_user_field()
    person = Person.objects.filter(**{user_field: student}).first()
    if person is None:
        person = Person.objects.create(display_name="Smoke Student")
        setattr(person, user_field, student)
        person.save(update_fields=[user_field])

    Student = _model("academy.Student")
    student_row, _ = Student.objects.get_or_create(person=person)

    PersonalPath = _model("academy.PersonalPath")
    path = PersonalPath.objects.filter(student=student_row, status="active").first()
    if path is None:
        badge = _first("competence.SkillBadge")
        if badge is not None:
            path = PersonalPath.objects.create(
                student=student_row, goal_badge=badge,
                title=f"Smoke path to {badge.title}")
            try:
                from toto.academy.paths import generate_steps
                generate_steps(path)
            except Exception as exc:
                stdout.write(f"  note: generate_steps failed ({exc!r}); "
                             "step routes will be skipped")

    staff = User.objects.filter(is_superuser=True).order_by("pk").first()
    if staff is None:
        staff = User.objects.create_superuser("ui-smoke-admin", "", "ui-smoke-admin")
    # The admin needs a linked Person for community_profile-dependent pages.
    if Person.objects.filter(**{user_field: staff}).first() is None:
        admin_person = Person.objects.create(display_name=staff.username)
        setattr(admin_person, user_field, staff)
        admin_person.save(update_fields=[user_field])

    return student, staff


# ---------------------------------------------------------------------------
# Probing + grading
# ---------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_get(base_url, path):
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(base_url.rstrip("/") + path, timeout=20)
        return resp.status, resp.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location", "")


def grade(persona, key, status, location):
    """-> (verdict, note): verdict in {'pass', 'warn', 'fail'}."""
    must = key in MUST_200.get(persona, ())
    if status >= 500:
        return "fail", "server error"
    if must and status != 200:
        return "fail", f"core page must be 200, got {status}"
    if status in (400, 404):
        if status in OVERRIDES.get(key, ()):
            return "pass", f"{status} allowed"
        if status == 404 and persona == "student" and key in STAFF_ONLY:
            return "pass", "staff gate"
        if status == 404 and persona in OWNER_404_OK.get(key, ()):
            return "pass", "owner-scoped"
        return "fail", f"unexpected {status}"
    if status == 405:
        return "pass", "post-only"
    if 300 <= status < 400:
        if persona == "anon":
            return "pass", ""
        if location.startswith(LOGIN_PATH) and key not in LOGIN_REDIRECT_OK:
            if persona == "student" and key in STAFF_ONLY:
                return "pass", "staff gate"
            return "fail", f"authenticated persona sent to login ({location})"
        return "pass", ""
    if status in (401, 403):
        if persona in ("anon", "student"):
            note = "staff gate" if key in STAFF_ONLY else "auth wall"
            return "pass", note
        if status == 403 and key.startswith("admin:") and key.endswith("_add"):
            return "pass", "add disabled"
        return "warn", f"{status} for staff (token-auth API?)"
    return "pass", ""


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = "Smoke-sweep every URL of the host with anon/student/staff personas."

    def add_arguments(self, parser):
        parser.add_argument("--personas", default="anon,student,staff")
        parser.add_argument("--filter", dest="key_filter", default="")
        parser.add_argument("--fail-fast", action="store_true")
        parser.add_argument("--base-url", default="",
                            help="Probe over HTTP instead of in-process "
                                 "(anon persona only)")
        parser.add_argument("--list-routes", action="store_true")

    def handle(self, *args, **opts):
        personas = [p.strip() for p in opts["personas"].split(",") if p.strip()]
        if opts["base_url"]:
            personas = ["anon"]

        student, staff = ensure_personas(self.stdout)
        probes = discover(self.stderr)

        if opts["key_filter"]:
            probes = [p for p in probes if p.key.startswith(opts["key_filter"])]

        if opts["list_routes"]:
            for p in sorted(probes, key=lambda p: p.key):
                mark = f"SKIP({p.skipped})" if p.skipped else p.path
                self.stdout.write(f"  {p.key:55s} {mark}")
            return

        failures, warns, rows = [], [], []
        unmapped = [p for p in probes if p.skipped.startswith("UNMAPPED")]
        broken_seed = [p for p in probes if p.skipped.startswith("seed contract")]
        for p in unmapped + broken_seed:
            failures.append(("-", p.key, "-", p.skipped))

        for persona in personas:
            plist = list(probes)
            if persona == "staff":
                plist += admin_probes()
            elif not opts["base_url"]:
                plist.append(Probe(key="admin:index", path=reverse("admin:index")))

            client = self._client(persona, student, staff, opts["base_url"])
            counts = {"pass": 0, "warn": 0, "fail": 0, "skip": 0}
            for p in sorted(plist, key=lambda p: p.key):
                if p.skipped:
                    counts["skip"] += 1
                    continue
                status, location = self._get(client, p.path, persona, p.key,
                                             student, staff, opts["base_url"])
                verdict, note = grade(persona, p.key, status, location)
                counts[verdict] += 1
                if verdict == "fail":
                    failures.append((persona, p.key, f"GET {p.path} -> {status}", note))
                    if opts["fail_fast"]:
                        break
                elif verdict == "warn":
                    warns.append((persona, p.key, f"GET {p.path} -> {status}", note))
            rows.append((persona, counts))
            if opts["fail_fast"] and failures:
                break

        self.stdout.write("")
        self.stdout.write(f"{'persona':10s} {'pass':>6s} {'warn':>6s} {'skip':>6s} {'FAIL':>6s}")
        for persona, c in rows:
            self.stdout.write(f"{persona:10s} {c['pass']:6d} {c['warn']:6d} "
                              f"{c['skip']:6d} {c['fail']:6d}")

        skipped = sorted({(p.key, p.skipped) for p in probes
                          if p.skipped and not p.skipped.startswith(("UNMAPPED", "seed contract"))})
        if skipped:
            self.stdout.write("\nSKIPPED")
            for key, reason in skipped:
                self.stdout.write(f"  {key:55s} {reason}")
        if warns:
            self.stdout.write("\nWARNINGS")
            for persona, key, what, note in warns:
                self.stdout.write(f"  [{persona}] {key}  {what}  ({note})")
        if failures:
            self.stdout.write("\nFAILURES")
            for persona, key, what, note in failures:
                self.stdout.write(f"  [{persona}] {key}  {what}  ({note})")
            raise CommandError(f"{len(failures)} URL smoke failure(s)")
        self.stdout.write(self.style.SUCCESS("\nURL smoke sweep PASSED"))

    # -- plumbing ----------------------------------------------------------

    def _client(self, persona, student, staff, base_url):
        if base_url:
            return None
        client = Client(raise_request_exception=False)
        if persona == "student":
            client.force_login(student)
        elif persona == "staff":
            client.force_login(staff)
        return client

    def _get(self, client, path, persona, key, student, staff, base_url):
        if base_url:
            return http_get(base_url, path)
        if key in SESSION_DESTROYING and persona != "anon":
            throwaway = Client(raise_request_exception=False)
            throwaway.force_login(student if persona == "student" else staff)
            client = throwaway
        try:
            resp = client.get(path)
        except Exception as exc:  # crash outside the response cycle
            self.stderr.write(f"  [{persona}] {key} raised {exc!r}")
            return 599, ""
        return resp.status_code, resp.headers.get("Location", "")
