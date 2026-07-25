"""Shared render helper for every back-office page.

Both the shell (dashboard) and per-app modules (e.g. the quiz editor) render
through ``backoffice_render`` so the nav, active-tab highlight and platform
theming stay consistent. It injects the module registry + active tab and runs
the platform ``PageProcessor`` decoration that the oya chrome expects.
"""

from django.shortcuts import render

from toto.ui import PageProcessor

from .nav import get_modules

BACKOFFICE_TITLE = "Panel autorski"


def backoffice_render(request, template_name, context=None, *, active=None):
    context = dict(context or {})
    context.setdefault("backoffice_title", BACKOFFICE_TITLE)
    context["backoffice_modules"] = get_modules()
    context["active_module"] = active
    return render(request, template_name, PageProcessor().decorate(context, request))
