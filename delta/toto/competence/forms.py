"""Teacher back-office forms for the skill tree (groups, badges, prerequisites)."""

from django import forms
from django.utils.translation import gettext_lazy as _

from toto.backoffice.utils import unique_slug
from toto.verbena.forms import apply_oya_field_styles

from .models import SkillBadge, SkillBadgePrerequisite, SkillGroup


class SkillGroupForm(forms.ModelForm):
    class Meta:
        model = SkillGroup
        fields = ["title", "description", "order"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": _("e.g. Klasa 1 — zbiory liczb")}),
            "description": forms.Textarea(attrs={"rows": 2}),
            "order": forms.NumberInput(attrs={"min": 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_oya_field_styles(self.fields)

    def save(self, commit=True):
        obj = super().save(commit=False)
        if not obj.slug:
            obj.slug = unique_slug(SkillGroup, obj.title, instance=obj)
        if commit:
            obj.save()
        return obj


class SkillBadgeForm(forms.ModelForm):
    prerequisites = forms.ModelMultipleChoiceField(
        queryset=SkillBadge.objects.none(),
        required=False,
        label=_("Prerequisite badges"),
        help_text=_("Badges a student must earn before this one."),
    )

    class Meta:
        model = SkillBadge
        fields = ["group", "title", "description", "icon", "order"]
        widgets = {
            "title": forms.TextInput(),
            "description": forms.Textarea(attrs={"rows": 2}),
            "icon": forms.TextInput(attrs={"placeholder": "fa-solid fa-award"}),
            "order": forms.NumberInput(attrs={"min": 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = SkillBadge.objects.select_related("group").order_by("group__order", "order", "title")
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
            self.fields["prerequisites"].initial = list(
                self.instance.prerequisite_links.values_list("prerequisite_id", flat=True)
            )
        self.fields["prerequisites"].queryset = qs
        self.fields["icon"].required = False
        apply_oya_field_styles(self.fields)

    def save(self, commit=True):
        obj = super().save(commit=False)
        if not obj.icon:
            obj.icon = "fa-solid fa-award"
        if not obj.slug:
            obj.slug = unique_slug(SkillBadge, obj.title, instance=obj, group=obj.group)
        if commit:
            obj.save()
            self._sync_prerequisites(obj)
        return obj

    def _sync_prerequisites(self, badge):
        desired = {b.id for b in self.cleaned_data.get("prerequisites", [])}
        desired.discard(badge.id)  # never self (also blocked by a DB check constraint)
        existing = {link.prerequisite_id: link for link in badge.prerequisite_links.all()}
        for pid, link in existing.items():
            if pid not in desired:
                link.delete()
        for pid in desired:
            if pid not in existing:
                SkillBadgePrerequisite.objects.get_or_create(badge=badge, prerequisite_id=pid)


class SkillBadgeQuickForm(forms.ModelForm):
    """Minimal badge create used by the course-module editor's inline quick-add."""

    class Meta:
        model = SkillBadge
        fields = ["group", "title"]

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.icon = obj.icon or "fa-solid fa-award"
        obj.slug = unique_slug(SkillBadge, obj.title, instance=obj, group=obj.group)
        if commit:
            obj.save()
        return obj
