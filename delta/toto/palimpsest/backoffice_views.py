"""Teacher back-office: note pages (palimpsest) behind the teacher gate.

Reuses the existing PageForm/SectionForm/TagForm but renders inside the
back-office shell and requires a teacher/staff user (the public palimpsest CRUD
stays login-only and untouched). These pages back CourseModule.verbena_page.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from toto.backoffice.access import teacher_required
from toto.backoffice.shell import backoffice_render

from .forms import PageForm, SectionForm, TagForm
from .models import Page, Section

ACTIVE = "notes"


@teacher_required
def page_list(request):
    pages = Page.objects.prefetch_related("sections", "tags").order_by("-created_at")
    return backoffice_render(request, "palimpsest/backoffice/page_list.html", {
        "pages": pages,
    }, active=ACTIVE)


def _render_page_form(request, page, form):
    ctx = {"page": page, "form": form,
           "title": _("Edit page") if page else _("New page"),
           "submit_label": _("Save page") if page else _("Create page")}
    if page:
        ctx["sections"] = page.sections.order_by("order", "id")
    return backoffice_render(request, "palimpsest/backoffice/page_form.html", ctx, active=ACTIVE)


@teacher_required
def page_create(request):
    form = PageForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        page = form.save()
        messages.success(request, _("Page created. Add sections to it."))
        return redirect("backoffice_notes:page-edit", pk=page.pk)
    return _render_page_form(request, None, form)


@teacher_required
def page_edit(request, pk):
    page = get_object_or_404(Page, pk=pk)
    form = PageForm(request.POST or None, instance=page)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Page saved."))
        return redirect("backoffice_notes:page-edit", pk=page.pk)
    return _render_page_form(request, page, form)


@teacher_required
def page_delete(request, pk):
    page = get_object_or_404(Page, pk=pk)
    if request.method == "POST":
        page.delete()
        messages.success(request, _("Page deleted."))
        return redirect("backoffice_notes:page-list")
    return backoffice_render(request, "backoffice/_generic_confirm_delete.html", {
        "title": _("Delete page"), "object_label": page.title,
        "warning": _("This deletes the page and all its sections."),
        "cancel_url": reverse("backoffice_notes:page-list"),
    }, active=ACTIVE)


@teacher_required
def section_create(request, pk):
    page = get_object_or_404(Page, pk=pk)
    form = SectionForm(request.POST or None, initial={"order": page.sections.count() + 1})
    if request.method == "POST" and form.is_valid():
        section = form.save(commit=False)
        section.page = page
        section.save()
        form.save_m2m()
        messages.success(request, _("Section added."))
        return redirect("backoffice_notes:page-edit", pk=page.pk)
    return backoffice_render(request, "backoffice/_generic_form.html", {
        "form": form, "title": _("New section"), "submit_label": _("Add section"),
        "icon": "fa-solid fa-plus",
        "back_url": reverse("backoffice_notes:page-edit", kwargs={"pk": page.pk}),
        "back_label": page.title,
        "cancel_url": reverse("backoffice_notes:page-edit", kwargs={"pk": page.pk}),
    }, active=ACTIVE)


@teacher_required
def section_edit(request, pk):
    section = get_object_or_404(Section.objects.select_related("page"), pk=pk)
    form = SectionForm(request.POST or None, instance=section)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Section saved."))
        return redirect("backoffice_notes:page-edit", pk=section.page.pk)
    return backoffice_render(request, "backoffice/_generic_form.html", {
        "form": form, "title": _("Edit section"), "submit_label": _("Save section"),
        "icon": "fa-solid fa-pen-to-square",
        "cancel_url": reverse("backoffice_notes:page-edit", kwargs={"pk": section.page.pk}),
    }, active=ACTIVE)


@teacher_required
def section_delete(request, pk):
    section = get_object_or_404(Section.objects.select_related("page"), pk=pk)
    page = section.page
    if request.method == "POST":
        section.delete()
        messages.success(request, _("Section deleted."))
        return redirect("backoffice_notes:page-edit", pk=page.pk)
    return backoffice_render(request, "backoffice/_generic_confirm_delete.html", {
        "title": _("Delete section"), "object_label": section.title or _("(untitled section)"),
        "cancel_url": reverse("backoffice_notes:page-edit", kwargs={"pk": page.pk}),
    }, active=ACTIVE)


@teacher_required
def tag_create(request):
    form = TagForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Tag created."))
        return redirect("backoffice_notes:page-list")
    return backoffice_render(request, "backoffice/_generic_form.html", {
        "form": form, "title": _("New tag"), "submit_label": _("Create tag"),
        "icon": "fa-solid fa-tag", "cancel_url": reverse("backoffice_notes:page-list"),
    }, active=ACTIVE)
