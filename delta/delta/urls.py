from django.apps import apps
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect
from django.urls import path, include


admin.site.site_header = "Delta — administracja"
admin.site.index_title = "Delta"
admin.site.site_title = "Delta"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("core/", include("toto.core.urls", namespace="core")),
    path("", lambda request: redirect("core:welcome")),
    path("", include("toto.sso_master.urls", namespace="sso")),
    path("backup/", include("toto.backup.urls", namespace="backup")),
    path("vault/", include("toto.vault.urls", namespace="vault")),
    path("socialhub/", include("toto.socialhub.urls", namespace="socialhub")),
    path("gervazy/", include("toto.gervazy.urls", namespace="gervazy")),
    path("events/", include("toto.events.urls", namespace="events")),
    path("markdownx/", include("markdownx.urls")),
    path("trix-editor/", include("trix_editor.urls")),
    path("api/", include("toto.api.urls", namespace="api")),
]

# Education apps — included only when present in INSTALLED_APPS, so the route table
# stays correct if an app is ever toggled off. competence has no URLs of its own
# (it surfaces through academy's skill views + a person-profile plugin).
_education = [
    ("toto.academy",       "nauka/",         "toto.academy.urls",       "academy"),
    ("toto.quizzes",       "zadania/",       "toto.quizzes.urls",       "quizzes"),
    ("toto.palimpsest",    "notatki/",       "toto.palimpsest.urls",    "palimpsest"),
    ("toto.library",       "biblioteka/",    "toto.library.urls",       "library"),
    ("toto.subscriptions", "subskrypcje/",   "toto.subscriptions.urls", "subscriptions"),
]
for app_label, prefix, module, namespace in _education:
    if apps.is_installed(app_label):
        urlpatterns.append(path(prefix, include(module, namespace=namespace)))

if apps.is_installed("django_prometheus"):
    urlpatterns.append(path("", include("django_prometheus.urls")))

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
