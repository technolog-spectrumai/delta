"""The single source of truth for who may enter the teacher back office.

A back-office user is anyone with an academy ``Teacher`` row OR Django
``is_staff`` (so admins/superusers are always in, even without a Teacher row).
The Teacher import is defensive so the shell does not hard-depend on academy
being installed — the ``is_staff`` branch is the fallback.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import Http404


def _has_teacher_row(user):
    person = getattr(user, "community_profile", None)
    if person is None:
        return False
    try:
        from toto.academy.models import Teacher
    except Exception:
        return False
    # Only an *active* Teacher row grants access, so a soft-demote (is_active=
    # False) closes the gate without deleting the row (which would strip course
    # ownership and certificate signatures via its SET_NULL back-references).
    return Teacher.objects.filter(person=person, is_active=True).exists()


def is_backoffice_user(user):
    """True if ``user`` may use the back office (teacher role or staff)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if user.is_staff:
        return True
    return _has_teacher_row(user)


def teacher_required(view):
    """Login + back-office gate for a view.

    Non-members get an ``Http404`` (existence hidden), mirroring the quizzes
    ``quiz_metrics`` gate and keeping the smoke_urls "student -> 404 = pass"
    staff-gate contract.
    """

    @wraps(view)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not is_backoffice_user(request.user):
            raise Http404
        return view(request, *args, **kwargs)

    return _wrapped
