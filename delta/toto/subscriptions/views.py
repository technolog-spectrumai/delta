from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from .models import DiscountCode, Subscription, SubscriptionPlan


def _person(request):
    return getattr(request.user, "community_profile", None)


def plan_list(request):
    plans = SubscriptionPlan.objects.filter(status=SubscriptionPlan.STATUS_ACTIVE)
    person = _person(request)
    active = None
    if person:
        active = (
            Subscription.objects
            .filter(person=person, status=Subscription.STATUS_ACTIVE, expires_at__gt=timezone.now())
            .select_related("plan")
            .order_by("-expires_at")
            .first()
        )
    context = {"plans": plans, "active_subscription": active}
    return render(request, "subscriptions/plan_list.html", PageProcessor().decorate(context, request))


@login_required
def my_subscription(request):
    """The profile 'Subscription' view — active course, validity and discount codes (spec F-07)."""
    person = _person(request)
    active = None
    history = []
    codes = []
    if person:
        subs = list(Subscription.objects.filter(person=person).select_related("plan"))
        for s in subs:
            s.refresh_status()
        active = next((s for s in subs if s.is_valid), None)
        history = subs
        codes = [c for c in DiscountCode.objects.filter(person=person) if c.is_redeemable]
    context = {"active_subscription": active, "history": history, "discount_codes": codes}
    return render(request, "subscriptions/my_subscription.html", PageProcessor().decorate(context, request))


@login_required
@require_POST
def subscribe(request, code):
    """Grant access under a plan. No payment gateway yet — free/admin-granted (spec open question)."""
    plan = get_object_or_404(SubscriptionPlan, code=code, status=SubscriptionPlan.STATUS_ACTIVE)
    person = _person(request)
    if not person:
        messages.error(request, "You need a community profile to subscribe.")
        return redirect("subscriptions:plan_list")
    sub = Subscription.start_for(person, plan)
    messages.success(
        request,
        f"Subscribed to {plan.name}. Access valid until {sub.expires_at:%Y-%m-%d}.",
    )
    return redirect("subscriptions:my_subscription")
