from django.contrib import admin

from .models import DiscountCode, Subscription, SubscriptionPlan


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "interval_months", "price", "discount_percent", "status", "order")
    list_filter = ("status", "interval_months")
    search_fields = ("name", "code")
    ordering = ("order", "interval_months")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("person", "plan", "status", "started_at", "expires_at")
    list_filter = ("status", "plan")
    search_fields = ("person__display_name", "plan__code")
    autocomplete_fields = ("person", "plan")
    date_hierarchy = "started_at"


@admin.register(DiscountCode)
class DiscountCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "percent", "person", "plan", "is_used", "issued_at", "expires_at")
    list_filter = ("is_used", "percent")
    search_fields = ("code", "person__display_name")
    autocomplete_fields = ("person", "plan")
