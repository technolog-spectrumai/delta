"""Expose back-office access to every template so the global/academy chrome can
show a teacher-only entry link without each view having to compute it.
"""

from .access import is_backoffice_user


def backoffice_access(request):
    return {"can_access_backoffice": is_backoffice_user(getattr(request, "user", None))}
