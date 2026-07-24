"""
Subscription gates — check and enforce course access.

    from toto.subscriptions.gates import is_subscribed, SubscriptionRequired

    if not is_subscribed(request.user, "academy"):
        raise SubscriptionRequired("academy", "Academy")

Rules
-----
- A user is subscribed to `plan_code` if they have an ACTIVE, unexpired
  Subscription to any plan whose code starts with `plan_code`.
- The person is resolved via user.community_profile; no profile → not subscribed.
- Fails safe: any error returns False (never blocks on an internal hiccup).
"""
from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class SubscriptionRequired(Exception):
    """Raised when a subscription is required but not active."""

    def __init__(self, plan_code: str, plan_name: str = "", upgrade_url: str = ""):
        self.plan_code = plan_code
        self.plan_name = plan_name or plan_code
        self.upgrade_url = upgrade_url
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"A {self.plan_name} subscription is required to access this feature."


def is_subscribed(user, plan_code: str) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    try:
        from .models import Subscription

        person = getattr(user, "community_profile", None)
        if person is None:
            return False
        return Subscription.objects.filter(
            person=person,
            plan__code__startswith=plan_code,
            status=Subscription.STATUS_ACTIVE,
            expires_at__gt=timezone.now(),
        ).exists()
    except Exception:
        return False


def require_subscription(plan_code: str, plan_name: str = "", redirect_to: str = ""):
    """Decorator for function-based views — redirect to the plans page if unmet."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not is_subscribed(request.user, plan_code):
                name = plan_name or plan_code.capitalize()
                messages.error(
                    request,
                    _("A %(name)s subscription is required. Subscribe to continue.")
                    % {"name": name},
                )
                target = redirect_to
                if not target:
                    try:
                        from django.urls import reverse
                        target = reverse("subscriptions:plan_list")
                    except Exception:
                        target = "/"
                return redirect(target)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
