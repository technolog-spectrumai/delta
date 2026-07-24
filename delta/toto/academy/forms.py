from django import forms
from django.utils.translation import gettext_lazy as _

from toto.competence.models import SkillBadge, SkillGroup

from .paths import compute_gap_badges


class PersonalPathGoalForm(forms.Form):
    goal_badge = forms.ModelChoiceField(
        queryset=SkillBadge.objects.select_related("group").order_by(
            "group__order", "order", "title"
        ),
        required=False,
        label=_("Target skill"),
        help_text=_("A single skill badge to work toward."),
    )
    goal_group = forms.ModelChoiceField(
        queryset=SkillGroup.objects.order_by("order", "title"),
        required=False,
        label=_("Target skill tree"),
        help_text=_("A whole skill tree to master."),
    )

    def __init__(self, *args, student=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.student = student

    def clean(self):
        cleaned = super().clean()
        goal_badge = cleaned.get("goal_badge")
        goal_group = cleaned.get("goal_group")

        if bool(goal_badge) == bool(goal_group):
            raise forms.ValidationError(
                _("Pick exactly one goal: a target skill or a whole skill tree.")
            )

        if self.student is not None:
            gap = compute_gap_badges(
                self.student, goal_badge=goal_badge, goal_group=goal_group
            )
            if not gap:
                raise forms.ValidationError(
                    _("You have already earned everything on this goal.")
                )

        return cleaned


class PathTaskStepForm(forms.Form):
    title = forms.CharField(max_length=200, label=_("Task"))
    note = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}),
        required=False,
        label=_("Note"),
    )
