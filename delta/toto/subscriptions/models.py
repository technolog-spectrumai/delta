"""
delta subscriptions — a lean, self-contained course-access + discount model.

This replaces the economy-coupled ``limbo/subscriptions`` app (which FK'd into
assets/accounts/invoices) with just what a math e-learning host needs:

* ``SubscriptionPlan``  — a purchasable plan (monthly / 3-/6-/12-month), each
  carrying a loyalty ``discount_percent`` (spec F-09: 5 / 10 / 15 / 20 %).
* ``Subscription``      — a person's active course access with a validity date
  (spec F-07: "rodzaj kursu z datą ważności").
* ``DiscountCode``      — a coupon shown in the user's profile (spec F-07/F-09:
  "twoje kody rabatowe").

There is no payment gateway here — subscribing is a free/admin-granted action for
now (payments are an open question in the spec). ``toto.academy`` soft-imports
``gates.is_subscribed`` / ``SubscriptionPlan`` to gate enrollment; if no
``academy*`` plan is active, enrollment is open.
"""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.db import models
from django.utils import timezone

from toto.people.models import Person


class SubscriptionPlan(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"
    STATUS_CHOICES = [(STATUS_ACTIVE, "Active"), (STATUS_INACTIVE, "Inactive")]

    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    code = models.SlugField(
        max_length=64,
        unique=True,
        help_text="Machine code, e.g. 'academy_monthly'. Academy gating matches "
                  "any plan whose code starts with 'academy'.",
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    interval_months = models.PositiveIntegerField(
        default=1, help_text="Access period length, in months.")
    price = models.DecimalField(
        max_digits=9, decimal_places=2, default=Decimal("0.00"),
        help_text="List price for one interval (PLN).")
    discount_percent = models.PositiveIntegerField(
        default=0,
        help_text="Loyalty discount this plan unlocks (spec F-09: 5/10/15/20%).")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "interval_months", "id"]

    def __str__(self):
        return f"{self.name} ({self.code})"

    @property
    def is_active(self):
        return self.status == self.STATUS_ACTIVE

    @property
    def discounted_price(self):
        if not self.discount_percent:
            return self.price
        factor = (Decimal(100) - Decimal(self.discount_percent)) / Decimal(100)
        return (self.price * factor).quantize(Decimal("0.01"))


class Subscription(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_EXPIRED = "expired"
    STATUS_CANCELED = "canceled"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_CANCELED, "Canceled"),
    ]

    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    started_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.person} → {self.plan.code} (to {self.expires_at:%Y-%m-%d})"

    @property
    def is_valid(self):
        return (
            self.status == self.STATUS_ACTIVE
            and self.expires_at is not None
            and self.expires_at > timezone.now()
        )

    def refresh_status(self):
        """Lazily flip an elapsed subscription to 'expired'."""
        if (
            self.status == self.STATUS_ACTIVE
            and self.expires_at
            and self.expires_at <= timezone.now()
        ):
            self.status = self.STATUS_EXPIRED
            self.save(update_fields=["status"])
        return self.status

    @classmethod
    def start_for(cls, person, plan, when=None):
        """Grant `person` access under `plan`, extending from any current validity.

        Also mints a one-time loyalty DiscountCode for the plan's discount tier.
        """
        now = when or timezone.now()
        current = (
            cls.objects.filter(person=person, status=cls.STATUS_ACTIVE, expires_at__gt=now)
            .order_by("-expires_at")
            .first()
        )
        base = current.expires_at if current else now
        expires = base + timedelta(days=30 * max(1, plan.interval_months))
        sub = cls.objects.create(person=person, plan=plan, started_at=now, expires_at=expires)
        if plan.discount_percent:
            DiscountCode.mint(person=person, percent=plan.discount_percent, plan=plan)
        return sub


class DiscountCode(models.Model):
    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    code = models.CharField(max_length=32, unique=True)
    percent = models.PositiveIntegerField(default=5)
    person = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.CASCADE,
        related_name="discount_codes",
        help_text="Owner — a loyalty coupon shown in this person's profile. "
                  "Blank = a generic promo code.")
    plan = models.ForeignKey(
        SubscriptionPlan, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="discount_codes",
        help_text="Optional plan the coupon was granted for.")
    is_used = models.BooleanField(default=False)
    issued_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self):
        return f"{self.code} (-{self.percent}%)"

    @property
    def is_redeemable(self):
        if self.is_used:
            return False
        if self.expires_at and self.expires_at <= timezone.now():
            return False
        return True

    @classmethod
    def mint(cls, person=None, percent=5, plan=None):
        code = f"DELTA{percent}-{uuid.uuid4().hex[:8].upper()}"
        return cls.objects.create(code=code, percent=percent, person=person, plan=plan)
