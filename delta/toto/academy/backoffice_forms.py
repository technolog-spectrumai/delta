"""Teacher back-office forms for the course graph:
Course -> CourseModule -> Lesson / Script(+ScriptSection).
"""

from django import forms
from django.forms.models import inlineformset_factory
from django.utils.translation import gettext_lazy as _

from trix_editor.widgets import TrixEditorWidget

from toto.backoffice.utils import unique_slug
from toto.competence.models import SkillBadge
from toto.palimpsest.models import Page
from toto.quizzes.models import Quiz
from toto.verbena.forms import apply_oya_checkbox_styles, apply_oya_field_styles

from .models import Course, CourseModule, Lesson, Script, ScriptSection


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ["title", "description", "is_published", "is_virtual", "order"]
        labels = {
            "is_published": _("Published (visible to students)"),
            "is_virtual": _("Virtual (curriculum item, hidden from the public list)"),
        }
        widgets = {
            "title": forms.TextInput(),
            "description": forms.Textarea(attrs={"rows": 3}),
            "order": forms.NumberInput(attrs={"min": 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_oya_field_styles(self.fields, skip={"is_published", "is_virtual"})
        apply_oya_checkbox_styles(self.fields["is_published"])
        apply_oya_checkbox_styles(self.fields["is_virtual"])

    def save(self, commit=True):
        obj = super().save(commit=False)
        if not obj.slug:
            obj.slug = unique_slug(Course, obj.title, instance=obj)
        if commit:
            obj.save()
        return obj


class ModuleForm(forms.ModelForm):
    class Meta:
        model = CourseModule
        fields = ["title", "description", "unlocks_badge", "verbena_page",
                  "exam", "attached_quizzes", "lecture_video_url", "order"]
        labels = {
            "unlocks_badge": _("Unlocks badge"),
            "verbena_page": _("Notes / handout page"),
            "exam": _("Module exam (official quiz)"),
            "attached_quizzes": _("Attached quizzes"),
            "lecture_video_url": _("Lecture video URL"),
        }
        widgets = {
            "title": forms.TextInput(),
            "description": forms.Textarea(attrs={"rows": 2}),
            "lecture_video_url": forms.URLInput(attrs={"placeholder": "https://…"}),
            "order": forms.NumberInput(attrs={"min": 0}),
            "attached_quizzes": forms.SelectMultiple(),
        }

    def __init__(self, *args, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.course = course or (self.instance.course if self.instance.pk else None)
        # Each module in a course must unlock a DISTINCT badge (unique constraint):
        # offer only badges not already claimed by another module of this course.
        used = SkillBadge.objects.filter(unlocking_modules__course=self.course)
        if self.instance.pk:
            used = used.exclude(unlocking_modules=self.instance)
        self.fields["unlocks_badge"].queryset = (
            SkillBadge.objects.exclude(pk__in=used.values("pk"))
            .select_related("group").order_by("group__order", "order", "title")
        )
        self.fields["verbena_page"].queryset = Page.objects.order_by("title")
        self.fields["verbena_page"].required = False
        self.fields["exam"].queryset = Quiz.objects.filter(is_official=True).order_by("title")
        self.fields["exam"].required = False
        self.fields["attached_quizzes"].queryset = Quiz.objects.order_by("title")
        apply_oya_field_styles(self.fields, skip={"attached_quizzes"})

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.course = self.course
        if not obj.slug:
            obj.slug = unique_slug(CourseModule, obj.title, instance=obj, course=self.course)
        if commit:
            obj.save()
            self.save_m2m()
        return obj


class LessonForm(forms.ModelForm):
    # Non-model upload fields; the views turn these into VaultFiles and set the
    # video_file / notes_file FKs. Kept out of Meta so ModelForm ignores them.
    _UPLOAD_FIELDS = ("video_upload", "notes_upload", "remove_video", "remove_notes")

    video_upload = forms.FileField(
        required=False, label=_("Lesson video"),
        help_text=_("Voice-narrated MP4/WebM. Leave blank to keep the current file."),
        widget=forms.ClearableFileInput(attrs={"accept": "video/*", "class": "block w-full text-sm"}),
    )
    notes_upload = forms.FileField(
        required=False, label=_("Lesson notes (PDF)"),
        help_text=_("Downloadable / printable PDF notes."),
        widget=forms.ClearableFileInput(attrs={"accept": "application/pdf,.pdf", "class": "block w-full text-sm"}),
    )
    remove_video = forms.BooleanField(required=False, label=_("Remove current video"))
    remove_notes = forms.BooleanField(required=False, label=_("Remove current notes"))

    class Meta:
        model = Lesson
        fields = ["title", "summary", "order", "attached_quizzes"]
        labels = {"attached_quizzes": _("Attached quizzes")}
        widgets = {
            "title": forms.TextInput(),
            "summary": forms.Textarea(attrs={"rows": 3}),
            "order": forms.NumberInput(attrs={"min": 0}),
            "attached_quizzes": forms.SelectMultiple(),
        }

    def __init__(self, *args, module=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.module = module or (self.instance.module if self.instance.pk else None)
        self.fields["attached_quizzes"].queryset = Quiz.objects.order_by("title")
        apply_oya_field_styles(
            self.fields, skip={"attached_quizzes", *self._UPLOAD_FIELDS})
        apply_oya_checkbox_styles(self.fields["remove_video"])
        apply_oya_checkbox_styles(self.fields["remove_notes"])

    @staticmethod
    def _detect(uploaded):
        import mimetypes
        from toto.vault.models import VaultFile
        mime, _ = mimetypes.guess_type(uploaded.name)
        return VaultFile.detect_type(mime or "", uploaded.name)

    def clean_video_upload(self):
        uploaded = self.cleaned_data.get("video_upload")
        if uploaded and self._detect(uploaded) != "video":
            raise forms.ValidationError(_("Please upload a video file (MP4, WebM, …)."))
        return uploaded

    def clean_notes_upload(self):
        uploaded = self.cleaned_data.get("notes_upload")
        if uploaded and self._detect(uploaded) != "pdf":
            raise forms.ValidationError(_("Please upload a PDF file."))
        return uploaded

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.module = self.module
        if not obj.slug:
            obj.slug = unique_slug(Lesson, obj.title, instance=obj, module=self.module)
        if commit:
            obj.save()
            self.save_m2m()
        return obj


class ScriptForm(forms.ModelForm):
    class Meta:
        model = Script
        fields = ["title", "description"]
        widgets = {
            "title": forms.TextInput(),
            "description": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, module=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.module = module or (self.instance.module if self.instance.pk else None)
        apply_oya_field_styles(self.fields)

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.module = self.module
        if not obj.slug:
            obj.slug = unique_slug(Script, obj.title, instance=obj)
        if commit:
            obj.save()
        return obj


class ScriptSectionForm(forms.ModelForm):
    class Meta:
        model = ScriptSection
        fields = ["title", "content"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": _("Section heading (optional)")}),
            "content": TrixEditorWidget(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_oya_field_styles(self.fields, skip={"content"})


ScriptSectionFormSet = inlineformset_factory(
    Script, ScriptSection, form=ScriptSectionForm, extra=3, can_delete=True)
