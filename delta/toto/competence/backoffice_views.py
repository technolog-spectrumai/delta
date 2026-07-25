"""Teacher back-office: author the skill tree (groups, badges, prerequisites)."""

from django.contrib import messages
from django.db.models import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from toto.backoffice.access import teacher_required
from toto.backoffice.shell import backoffice_render
from toto.backoffice.utils import apply_reorder, unique_slug

from .forms import SkillBadgeForm, SkillGroupForm
from .models import SkillBadge, SkillGroup

ACTIVE = "skills"
OVERVIEW = "backoffice_skills:skill-overview"


# --- overview --------------------------------------------------------------

@teacher_required
def skill_overview(request):
    groups = (
        SkillGroup.objects.prefetch_related("badges", "badges__prerequisite_links__prerequisite")
        .order_by("order", "title")
    )
    return backoffice_render(request, "competence/backoffice/overview.html", {
        "groups": groups,
    }, active=ACTIVE)


# --- reorder ---------------------------------------------------------------

@teacher_required
@require_POST
def group_reorder(request):
    if apply_reorder(SkillGroup.objects.order_by("order", "title"), request):
        return HttpResponse(status=204)
    return redirect(OVERVIEW)


@teacher_required
@require_POST
def badge_reorder(request, pk):
    group = get_object_or_404(SkillGroup, pk=pk)
    if apply_reorder(group.badges.order_by("order", "title"), request):
        return HttpResponse(status=204)
    return redirect(OVERVIEW)


# --- groups ----------------------------------------------------------------

@teacher_required
def group_create(request):
    form = SkillGroupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Skill group created."))
        return redirect(OVERVIEW)
    return backoffice_render(request, "backoffice/_generic_form.html", {
        "form": form, "title": _("New skill group"), "submit_label": _("Create group"),
        "icon": "fa-solid fa-layer-group", "cancel_url": reverse(OVERVIEW),
    }, active=ACTIVE)


@teacher_required
def group_edit(request, pk):
    group = get_object_or_404(SkillGroup, pk=pk)
    form = SkillGroupForm(request.POST or None, instance=group)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Skill group saved."))
        return redirect(OVERVIEW)
    return backoffice_render(request, "backoffice/_generic_form.html", {
        "form": form, "title": _("Edit skill group"), "submit_label": _("Save group"),
        "icon": "fa-solid fa-pen-to-square", "cancel_url": reverse(OVERVIEW),
    }, active=ACTIVE)


@teacher_required
def group_delete(request, pk):
    group = get_object_or_404(SkillGroup, pk=pk)
    if request.method == "POST":
        try:
            group.delete()
        except ProtectedError:
            messages.error(request, _("Cannot delete: a badge in this group is used by a course module."))
            return redirect(OVERVIEW)
        messages.success(request, _("Skill group deleted."))
        return redirect(OVERVIEW)
    return backoffice_render(request, "backoffice/_generic_confirm_delete.html", {
        "title": _("Delete skill group"), "object_label": group.title,
        "warning": _("This deletes the group and all its badges. This cannot be undone."),
        "cancel_url": reverse(OVERVIEW),
    }, active=ACTIVE)


# --- badges ----------------------------------------------------------------

@teacher_required
def badge_create(request):
    form = SkillBadgeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Badge created."))
        return redirect(OVERVIEW)
    return backoffice_render(request, "backoffice/_generic_form.html", {
        "form": form, "title": _("New badge"), "submit_label": _("Create badge"),
        "icon": "fa-solid fa-award", "cancel_url": reverse(OVERVIEW),
    }, active=ACTIVE)


@teacher_required
def badge_edit(request, pk):
    badge = get_object_or_404(SkillBadge, pk=pk)
    form = SkillBadgeForm(request.POST or None, instance=badge)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Badge saved."))
        return redirect(OVERVIEW)
    return backoffice_render(request, "backoffice/_generic_form.html", {
        "form": form, "title": _("Edit badge"), "submit_label": _("Save badge"),
        "icon": "fa-solid fa-pen-to-square", "cancel_url": reverse(OVERVIEW),
    }, active=ACTIVE)


@teacher_required
def badge_delete(request, pk):
    badge = get_object_or_404(SkillBadge, pk=pk)
    if request.method == "POST":
        try:
            badge.delete()
        except ProtectedError:
            messages.error(request, _("Cannot delete: this badge is unlocked by a course module."))
            return redirect(OVERVIEW)
        messages.success(request, _("Badge deleted."))
        return redirect(OVERVIEW)
    return backoffice_render(request, "backoffice/_generic_confirm_delete.html", {
        "title": _("Delete badge"), "object_label": str(badge),
        "cancel_url": reverse(OVERVIEW),
    }, active=ACTIVE)


@teacher_required
@require_POST
def badge_quick_create(request):
    """Inline badge creation used by the course-module editor (HTMX).

    Returns a ready-to-select <option> for the module's badge <select>.
    """
    title = (request.POST.get("qc_title") or "").strip()
    if not title:
        return HttpResponse(status=400)
    group_id = request.POST.get("qc_group") or None
    group = SkillGroup.objects.filter(pk=group_id).first() if group_id else None
    if group is None:
        group, _created = SkillGroup.objects.get_or_create(
            slug="ogolne", defaults={"title": "Ogólne"})
    badge = SkillBadge.objects.create(
        group=group, title=title, icon="fa-solid fa-award",
        slug=unique_slug(SkillBadge, title, group=group))
    return HttpResponse(
        format_html('<option value="{}" selected>{} / {}</option>',
                    badge.id, group.title, badge.title))
