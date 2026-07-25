"""Teacher back-office: library references (Book/Article/Audio/Video) + collections."""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from toto.backoffice.access import teacher_required
from toto.backoffice.shell import backoffice_render

from .backoffice_forms import (
    ArticleForm,
    AudioReferenceForm,
    BookForm,
    LibraryCollectionForm,
    VideoReferenceForm,
)
from .models import Article, AudioReference, Book, LibraryCollection, VideoReference

ACTIVE = "library"

# kind -> (model, form, human label, FA icon)
TYPES = {
    "book": (Book, BookForm, _("Book"), "fa-solid fa-book"),
    "article": (Article, ArticleForm, _("Article"), "fa-solid fa-file-lines"),
    "audio": (AudioReference, AudioReferenceForm, _("Audio"), "fa-solid fa-headphones"),
    "video": (VideoReference, VideoReferenceForm, _("Video"), "fa-solid fa-film"),
}


def _lookup(kind):
    if kind not in TYPES:
        raise Http404
    return TYPES[kind]


@teacher_required
def reference_list(request):
    items = []
    for kind, (model, _form, label, icon) in TYPES.items():
        for obj in model.objects.all():
            items.append({"kind": kind, "label": label, "icon": icon, "obj": obj})
    items.sort(key=lambda i: (i["obj"].title or "").lower())
    return backoffice_render(request, "library/backoffice/reference_list.html", {
        "items": items,
        "collections": LibraryCollection.objects.all(),
        "types": [{"kind": k, "label": v[2], "icon": v[3]} for k, v in TYPES.items()],
    }, active=ACTIVE)


@teacher_required
def reference_create(request, kind):
    model, form_cls, label, icon = _lookup(kind)
    form = form_cls(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Reference added."))
        return redirect("backoffice_library:reference-list")
    return backoffice_render(request, "backoffice/_generic_form.html", {
        "form": form, "title": _("New %(type)s") % {"type": label},
        "submit_label": _("Create"), "icon": icon,
        "cancel_url": reverse("backoffice_library:reference-list"),
    }, active=ACTIVE)


@teacher_required
def reference_edit(request, kind, pk):
    model, form_cls, label, icon = _lookup(kind)
    obj = get_object_or_404(model, pk=pk)
    form = form_cls(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Reference saved."))
        return redirect("backoffice_library:reference-list")
    return backoffice_render(request, "backoffice/_generic_form.html", {
        "form": form, "title": _("Edit %(type)s") % {"type": label},
        "submit_label": _("Save"), "icon": "fa-solid fa-pen-to-square",
        "cancel_url": reverse("backoffice_library:reference-list"),
    }, active=ACTIVE)


@teacher_required
def reference_delete(request, kind, pk):
    model, _form_cls, label, _icon = _lookup(kind)
    obj = get_object_or_404(model, pk=pk)
    if request.method == "POST":
        obj.delete()
        messages.success(request, _("Reference deleted."))
        return redirect("backoffice_library:reference-list")
    return backoffice_render(request, "backoffice/_generic_confirm_delete.html", {
        "title": _("Delete reference"), "object_label": obj.title,
        "cancel_url": reverse("backoffice_library:reference-list"),
    }, active=ACTIVE)


# --- collections -----------------------------------------------------------

@teacher_required
def collection_create(request):
    form = LibraryCollectionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Collection created."))
        return redirect("backoffice_library:reference-list")
    return backoffice_render(request, "backoffice/_generic_form.html", {
        "form": form, "title": _("New collection"), "submit_label": _("Create collection"),
        "icon": "fa-solid fa-layer-group",
        "cancel_url": reverse("backoffice_library:reference-list"),
    }, active=ACTIVE)


@teacher_required
def collection_edit(request, pk):
    collection = get_object_or_404(LibraryCollection, pk=pk)
    form = LibraryCollectionForm(request.POST or None, instance=collection)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Collection saved."))
        return redirect("backoffice_library:reference-list")
    return backoffice_render(request, "backoffice/_generic_form.html", {
        "form": form, "title": _("Edit collection"), "submit_label": _("Save collection"),
        "icon": "fa-solid fa-pen-to-square",
        "cancel_url": reverse("backoffice_library:reference-list"),
    }, active=ACTIVE)


@teacher_required
def collection_delete(request, pk):
    collection = get_object_or_404(LibraryCollection, pk=pk)
    if request.method == "POST":
        collection.delete()
        messages.success(request, _("Collection deleted."))
        return redirect("backoffice_library:reference-list")
    return backoffice_render(request, "backoffice/_generic_confirm_delete.html", {
        "title": _("Delete collection"), "object_label": collection.title,
        "cancel_url": reverse("backoffice_library:reference-list"),
    }, active=ACTIVE)
