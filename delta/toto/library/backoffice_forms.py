"""Teacher back-office forms for library references and collections."""

from django import forms

from toto.backoffice.utils import unique_slug
from toto.palimpsest.models import Tag
from toto.verbena.forms import apply_oya_field_styles

from .models import (
    Article,
    AudioReference,
    Book,
    LibraryCollection,
    VideoReference,
)

SHARED = ["title", "authors", "year", "doi", "url", "abstract", "tags"]


class _LibraryItemForm(forms.ModelForm):
    """Shared behaviour for the four reference types (default widgets suffice)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tags"].queryset = Tag.objects.order_by("name")
        apply_oya_field_styles(self.fields, skip={"tags"})

    def save(self, commit=True):
        obj = super().save(commit=False)
        if not obj.slug:
            obj.slug = unique_slug(type(obj), obj.title, instance=obj)
        if commit:
            obj.save()
            self.save_m2m()
        return obj


class BookForm(_LibraryItemForm):
    class Meta:
        model = Book
        fields = SHARED + ["publisher", "edition", "isbn"]


class ArticleForm(_LibraryItemForm):
    class Meta:
        model = Article
        fields = SHARED + ["journal", "volume", "issue", "pages"]


class AudioReferenceForm(_LibraryItemForm):
    class Meta:
        model = AudioReference
        fields = SHARED + ["platform", "duration"]


class VideoReferenceForm(_LibraryItemForm):
    class Meta:
        model = VideoReference
        fields = SHARED + ["platform", "director", "duration"]


class LibraryCollectionForm(forms.ModelForm):
    class Meta:
        model = LibraryCollection
        fields = ["title", "description", "books", "articles", "audio", "videos"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_oya_field_styles(
            self.fields, skip={"books", "articles", "audio", "videos"})

    def save(self, commit=True):
        obj = super().save(commit=False)
        if not obj.slug:
            obj.slug = unique_slug(LibraryCollection, obj.title, instance=obj)
        if commit:
            obj.save()
            self.save_m2m()
        return obj
