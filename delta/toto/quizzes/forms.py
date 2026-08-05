"""Forms for the teacher back-office quiz editor.

The question editor exposes a single "answer format" control (single choice /
multiple choice / open answer) that is mapped onto the model's
``question_type`` + ``is_multiple_choice`` pair, and an inline formset of
answer options. Server-side validation closes the gaps the raw admin left open
(a choice question with no correct answer, an open question with no accepted
answer, empty option text).
"""

import json

from django import forms
from django.forms.models import BaseInlineFormSet, inlineformset_factory
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _


from toto.backoffice.utils import add_math_preview
from toto.cyprian.forms import CyprianRichTextField
from toto.verbena.forms import apply_oya_checkbox_styles, apply_oya_field_styles

from .models import Quiz, QuizAnswer, QuizAnswerTrait, QuizQuestion, QuizTrait


class QuizForm(forms.ModelForm):
    class Meta:
        model = Quiz
        fields = ["title", "slug", "description", "is_published", "is_official", "order"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": _("e.g. Klasa 1 — zbiory liczb")}),
            "slug": forms.TextInput(attrs={"placeholder": _("optional-custom-url")}),
            "description": forms.Textarea(attrs={
                "rows": 3,
                "placeholder": _("A short description of this task pool."),
            }),
            "order": forms.NumberInput(attrs={"min": 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        apply_oya_field_styles(self.fields, skip={"is_published", "is_official"})
        apply_oya_checkbox_styles(self.fields["is_published"])
        apply_oya_checkbox_styles(self.fields["is_official"])
        add_math_preview(self.fields["description"])

    def clean_slug(self):
        """Auto-slug from the title and keep it unique (Quiz.slug is not unique)."""
        slug = (self.cleaned_data.get("slug") or "").strip()
        if not slug:
            slug = slugify(self.cleaned_data.get("title") or "") or "quiz"
        base, i = slug, 2
        qs = Quiz.objects.filter(slug=slug)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        while qs.exists():
            slug = f"{base}-{i}"
            i += 1
            qs = Quiz.objects.filter(slug=slug)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
        return slug


class QuestionForm(forms.ModelForm):
    FORMAT_SINGLE = "single"
    FORMAT_MULTIPLE = "multiple"
    FORMAT_OPEN = "open"
    FORMAT_CHOICES = [
        (FORMAT_SINGLE, _("Single choice (one correct answer)")),
        (FORMAT_MULTIPLE, _("Multiple choice (several correct)")),
        (FORMAT_OPEN, _("Open answer (typed)")),
    ]

    answer_format = forms.ChoiceField(
        choices=FORMAT_CHOICES,
        label=_("Answer format"),
        widget=forms.RadioSelect,
    )

    # The worked solution is the one rich field in a task, and it is edited in
    # cyprian's editor — the same one the lesson notes use, so a teacher meets
    # one writing surface across the panel. Declared explicitly rather than
    # through Meta.widgets: the field class carries the sanitiser, which the
    # model's TextField knows nothing about.
    solution = CyprianRichTextField(
        required=False,
        placeholder=_("Worked solution — the full reasoning, with formulas and pictures."),
        min_height="18rem",
        help_text=_("Shown after any practice submission and in the graded "
                    "review. Falls back to the plain explanation when empty."),
    )

    class Meta:
        model = QuizQuestion
        fields = ["text", "solution", "hint", "explanation", "max_time", "order"]
        widgets = {
            "text": forms.Textarea(attrs={
                "rows": 3,
                "placeholder": _("Question stem. Use $…$ for inline math, $$…$$ for display math."),
            }),
            "hint": forms.Textarea(attrs={
                "rows": 2,
                "placeholder": _("Optional hint shown in a modal on the practice page."),
            }),
            "explanation": forms.Textarea(attrs={
                "rows": 2,
                "placeholder": _("Optional plain-text fallback shown when no rich solution is set."),
            }),
            "max_time": forms.NumberInput(attrs={"min": 0, "placeholder": _("minutes")}),
            "order": forms.NumberInput(attrs={"min": 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["max_time"].required = False
        # Seed the format control from the existing instance on edit.
        if self.instance and self.instance.pk:
            self.fields["answer_format"].initial = self._format_from_instance(self.instance)
        else:
            self.fields["answer_format"].initial = self.FORMAT_SINGLE
        apply_oya_field_styles(self.fields, skip={"solution", "answer_format"})

    @staticmethod
    def _format_from_instance(instance):
        if instance.question_type == QuizQuestion.TYPE_OPEN:
            return QuestionForm.FORMAT_OPEN
        return QuestionForm.FORMAT_MULTIPLE if instance.is_multiple_choice else QuestionForm.FORMAT_SINGLE

    def save(self, commit=True):
        obj = super().save(commit=False)
        fmt = self.cleaned_data.get("answer_format", self.FORMAT_SINGLE)
        if fmt == self.FORMAT_OPEN:
            obj.question_type = QuizQuestion.TYPE_OPEN
            obj.is_multiple_choice = False
        elif fmt == self.FORMAT_MULTIPLE:
            obj.question_type = QuizQuestion.TYPE_CHOICE
            obj.is_multiple_choice = True
        else:
            obj.question_type = QuizQuestion.TYPE_CHOICE
            obj.is_multiple_choice = False
        if commit:
            obj.save()
        return obj


class QuizAnswerForm(forms.ModelForm):
    # Serialized [{"trait": <id>, "weight": <int>}, …] from the collapsed
    # "Advanced" panel; reconciled onto QuizAnswerTrait rows on save.
    traits_data = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={"data-traits-data": "1"}),
    )

    class Meta:
        model = QuizAnswer
        fields = ["text", "is_correct", "order"]
        widgets = {
            "text": forms.TextInput(attrs={
                "placeholder": _("Answer text (math with $…$ allowed)"),
                "data-answer-text": "1",
            }),
            "is_correct": forms.CheckboxInput(attrs={
                "data-correct": "1",
                "@change": "onCorrectToggle($event.target)",
            }),
            "order": forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["text"].required = False  # blank extra rows are dropped in clean
        apply_oya_field_styles(self.fields, skip={"is_correct", "order", "traits_data"})
        if self.instance and self.instance.pk:
            self.fields["traits_data"].initial = self._traits_json(self.instance)

    @staticmethod
    def _traits_json(answer):
        rows = [
            {"trait": m.trait_id, "weight": m.weight}
            for m in answer.trait_mappings.all()
        ]
        return json.dumps(rows) if rows else ""

    def save(self, commit=True):
        answer = super().save(commit=commit)
        if commit:
            self._sync_traits(answer)
        return answer

    def _sync_traits(self, answer):
        raw = (self.cleaned_data.get("traits_data") or "").strip()
        desired = {}
        if raw:
            try:
                items = json.loads(raw)
            except (ValueError, TypeError):
                items = []
            for it in items or []:
                try:
                    tid = int(it.get("trait"))
                    weight = int(it.get("weight", 1))
                except (TypeError, ValueError, AttributeError):
                    continue
                if tid:
                    desired[tid] = weight
        existing = {m.trait_id: m for m in answer.trait_mappings.all()}
        for tid, mapping in existing.items():
            if tid not in desired:
                mapping.delete()
        valid_ids = set(
            QuizTrait.objects.filter(id__in=list(desired)).values_list("id", flat=True)
        )
        for tid, weight in desired.items():
            if tid not in valid_ids:
                continue
            mapping = existing.get(tid)
            if mapping is None:
                QuizAnswerTrait.objects.create(answer=answer, trait_id=tid, weight=weight)
            elif mapping.weight != weight:
                mapping.weight = weight
                mapping.save(update_fields=["weight"])


class BaseQuizAnswerFormSet(BaseInlineFormSet):
    """Validates the answer set against the question's answer format.

    The view sets ``self.answer_format`` from the POSTed format before
    ``is_valid()``; blank extra rows are ignored.
    """

    answer_format = QuestionForm.FORMAT_SINGLE

    def _live_rows(self):
        rows = []
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            cd = form.cleaned_data
            if cd.get("DELETE"):
                continue
            if (cd.get("text") or "").strip():
                rows.append(cd)
        return rows

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        fmt = self.answer_format
        rows = self._live_rows()
        if not rows:
            raise forms.ValidationError(_("Add at least one answer option."))
        if fmt == QuestionForm.FORMAT_OPEN:
            return  # every typed row is an accepted answer
        if len(rows) < 2:
            raise forms.ValidationError(_("A choice question needs at least two options."))
        correct = [r for r in rows if r.get("is_correct")]
        if fmt == QuestionForm.FORMAT_SINGLE and len(correct) != 1:
            raise forms.ValidationError(
                _("Mark exactly one option correct for a single-choice question."))
        if fmt == QuestionForm.FORMAT_MULTIPLE and len(correct) < 1:
            raise forms.ValidationError(_("Mark at least one option correct."))


def build_answer_formset(extra):
    return inlineformset_factory(
        QuizQuestion,
        QuizAnswer,
        form=QuizAnswerForm,
        formset=BaseQuizAnswerFormSet,
        extra=extra,
        can_delete=True,
    )
