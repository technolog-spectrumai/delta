"""The back-office module registry — one entry per authoring module.

The dashboard renders these as cards and the shell renders them as nav tabs.
New modules append an entry here; a module whose backing app is not installed
is dropped so the office degrades gracefully. ``live=False`` renders a
"coming soon" placeholder so the growing surface is visible from day one.
"""

from django.apps import apps
from django.utils.translation import gettext_lazy as _

# key, label (English msgid -> pl catalog), icon, url_name (namespaced route,
# reversed lazily in templates), live, app (drop card if not installed), blurb.
_MODULES = [
    {
        "key": "quizzes",
        "label": _("Tasks"),
        "icon": "fa-solid fa-circle-question",
        "url_name": "backoffice_quizzes:quiz-list",
        "live": True,
        "app": "toto.quizzes",
        "blurb": _("Write and organise quiz tasks: single/multiple choice and "
                   "open answers, with worked solutions and live math."),
    },
    {
        "key": "courses",
        "label": _("Courses"),
        "icon": "fa-solid fa-book-open-reader",
        "url_name": "backoffice_courses:course-list",
        "live": True,
        "app": "toto.academy",
        "blurb": _("Author courses, modules and lessons."),
    },
    {
        "key": "skills",
        "label": _("Skills & badges"),
        "icon": "fa-solid fa-award",
        "url_name": "backoffice_skills:skill-overview",
        "live": True,
        "app": "toto.competence",
        "blurb": _("Build the skill tree: groups, badges and prerequisites that "
                   "course modules unlock."),
    },
    {
        "key": "notes",
        "label": _("Notes"),
        "icon": "fa-solid fa-file-lines",
        "url_name": "backoffice_notes:page-list",
        "live": True,
        "app": "toto.palimpsest",
        "blurb": _("Curate lesson notes and study materials."),
    },
    {
        "key": "library",
        "label": _("Library"),
        "icon": "fa-solid fa-book-open",
        "url_name": "backoffice_library:reference-list",
        "live": True,
        "app": "toto.library",
        "blurb": _("Manage the bibliography and references."),
    },
    {
        "key": "people",
        "label": _("People"),
        "icon": "fa-solid fa-chalkboard-user",
        "url_name": "backoffice_people:people-list",
        "live": True,
        "app": "toto.academy",
        "blurb": _("Promote people to teachers so they can use the panel."),
    },
    {
        "key": "welcome",
        "label": _("Welcome page"),
        "icon": "fa-solid fa-door-open",
        "url_name": "backoffice_welcome:welcome-edit",
        "live": True,
        "app": "toto.academy",
        "blurb": _("Edit the invitation text students and teachers see on the home page."),
    },
]


def get_modules():
    """Installed back-office modules, in display order."""
    return [m for m in _MODULES if apps.is_installed(m["app"])]
