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

# Video-on-demand player (native HTML5 player for Vault video/audio files).
if apps.is_installed("toto.vod"):
    urlpatterns.append(path("vod/", include("toto.vod.urls")))

# Teacher back office (Panel autorski) — a shell app plus one URL module per
# authoring module. Mounted only when the shell is installed; each module's
# routes are added only when its owning app is present.
if apps.is_installed("toto.backoffice"):
    urlpatterns.append(path("panel/", include("toto.backoffice.urls")))
    if apps.is_installed("toto.quizzes"):
        urlpatterns.append(
            path("panel/zadania/", include("toto.quizzes.backoffice_urls"))
        )
    if apps.is_installed("toto.competence"):
        urlpatterns.append(
            path("panel/kompetencje/", include("toto.competence.backoffice_urls"))
        )
    if apps.is_installed("toto.academy"):
        urlpatterns.append(
            path("panel/kursy/", include("toto.academy.backoffice_urls"))
        )
        urlpatterns.append(
            path("panel/osoby/", include("toto.academy.people_backoffice_urls"))
        )
        urlpatterns.append(
            path("panel/strona-powitalna/", include("toto.academy.welcome_backoffice_urls"))
        )
    if apps.is_installed("toto.palimpsest"):
        urlpatterns.append(
            path("panel/notatki/", include("toto.palimpsest.backoffice_urls"))
        )
    if apps.is_installed("toto.library"):
        urlpatterns.append(
            path("panel/biblioteka/", include("toto.library.backoffice_urls"))
        )

if apps.is_installed("django_prometheus"):
    urlpatterns.append(path("", include("django_prometheus.urls")))

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
