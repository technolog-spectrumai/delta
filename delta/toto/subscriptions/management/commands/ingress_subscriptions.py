from __future__ import annotations

from decimal import Decimal

from toto.ingress import IngressCommand

from toto.subscriptions.models import SubscriptionPlan

# The four subscription tiers from the spec (F-09): buying a longer plan unlocks a
# bigger one-time loyalty discount — 1mo→5%, 3mo→10%, 6mo→15%, 12mo→20%.
PLANS = [
    {"code": "academy_monthly", "name": "Delta — abonament miesięczny",   "interval_months": 1,  "price": Decimal("49.00"),  "discount_percent": 5,  "order": 1},
    {"code": "academy_quarter", "name": "Delta — abonament 3-miesięczny", "interval_months": 3,  "price": Decimal("129.00"), "discount_percent": 10, "order": 2},
    {"code": "academy_half",    "name": "Delta — abonament 6-miesięczny", "interval_months": 6,  "price": Decimal("239.00"), "discount_percent": 15, "order": 3},
    {"code": "academy_year",    "name": "Delta — abonament roczny",       "interval_months": 12, "price": Decimal("399.00"), "discount_percent": 20, "order": 4},
]


class Command(IngressCommand):
    help = "Seed delta subscription plans (1/3/6/12-month, 5/10/15/20% loyalty discounts)."

    def process(self):
        # Only under FULL_INGRESS: a bare bring-up ships no plans, so academy
        # enrollment stays open (easy to demo the learning flow); a full seed
        # turns on subscription gating.
        if not self.full:
            return
        new = 0
        for spec in PLANS:
            code = spec["code"]
            defaults = {k: v for k, v in spec.items() if k != "code"}
            defaults["status"] = SubscriptionPlan.STATUS_ACTIVE
            _, created = SubscriptionPlan.objects.update_or_create(code=code, defaults=defaults)
            new += int(created)
        self.stdout.write(f"  💳  subscriptions: {len(PLANS)} plans ensured ({new} new)")
