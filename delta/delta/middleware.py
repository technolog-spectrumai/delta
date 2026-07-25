"""Role-aware post-login landing.

The vendored login view always redirects to ``core:dashboard`` (a generic
app-grid). Editing it is out of scope (it lives in the toto-base subtree), so
this middleware makes the landing role-aware without touching vendored code:

* ``user_logged_in`` sets a one-shot session flag.
* On the very next visit to ``core:dashboard`` (the default post-login target)
  the flag is consumed and the user is redirected to their role's home —
  teachers to the authoring panel, learners to the student home. Staff and
  superusers keep the generic dashboard (their ops links live there).

Because it only acts while the one-shot flag is set, ordinary later visits to
the dashboard are untouched and the common request path stays free of work.
"""

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse

_PENDING = "role_landing_pending"


@receiver(user_logged_in)
def _flag_role_landing(sender, request, user, **kwargs):
    if request is not None and hasattr(request, "session"):
        request.session[_PENDING] = True


def _role_landing_url(user):
    # Staff/superusers keep the generic ops dashboard.
    if user.is_staff or user.is_superuser:
        return None
    person = getattr(user, "community_profile", None)
    if person is None:
        return None
    from toto.academy.models import Teacher
    try:
        if Teacher.objects.filter(person=person, is_active=True).exists():
            return reverse("backoffice:dashboard")
        return reverse("academy:student-home")
    except NoReverseMatch:
        return None


class RoleAwareLandingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (request.method == "GET" and user is not None and user.is_authenticated
                and request.session.get(_PENDING)):
            try:
                dashboard_path = reverse("core:dashboard")
            except NoReverseMatch:
                dashboard_path = None
            if dashboard_path and request.path == dashboard_path:
                request.session.pop(_PENDING, None)
                target = _role_landing_url(user)
                if target and target != request.path:
                    return redirect(target)
        return self.get_response(request)
