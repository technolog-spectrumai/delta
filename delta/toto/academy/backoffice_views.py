"""Teacher back-office: author courses (Course -> Module -> Lesson / Script)."""

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from toto.backoffice.access import teacher_required
from toto.backoffice.shell import backoffice_render

from .backoffice_forms import (
    CourseForm,
    LessonForm,
    ModuleForm,
    ScriptForm,
    ScriptSectionFormSet,
)
from toto.competence.models import SkillGroup

from .models import Course, CourseModule, Lesson, Script, Teacher

ACTIVE = "courses"


def _teacher(request):
    person = getattr(request.user, "community_profile", None)
    return Teacher.objects.filter(person=person).first() if person else None


def _apply_reorder(items, item_id, direction):
    """Swap one item up/down and normalise every ``order`` to 1..n."""
    ids = [i.id for i in items]
    try:
        idx = ids.index(int(item_id))
    except (TypeError, ValueError):
        idx = None
    if idx is not None:
        swap = idx - 1 if direction == "up" else idx + 1
        if 0 <= swap < len(items):
            items[idx], items[swap] = items[swap], items[idx]
    for i, obj in enumerate(items, start=1):
        if obj.order != i:
            obj.order = i
            obj.save(update_fields=["order"])


def _renumber_sections(formset):
    """Assign section order by form position (the section form has no order field)."""
    i = 1
    for form in formset.forms:
        if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
            continue
        obj = form.instance
        if obj.pk:
            if obj.order != i:
                obj.order = i
                obj.save(update_fields=["order"])
            i += 1


# --- courses ---------------------------------------------------------------

@teacher_required
def course_list(request):
    courses = Course.objects.annotate(n_modules=Count("modules")).order_by("order", "title")
    return backoffice_render(request, "academy/backoffice/course_list.html", {
        "courses": courses,
    }, active=ACTIVE)


@teacher_required
def course_create(request):
    form = CourseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        course = form.save(commit=False)
        course.author = request.user
        course.owner = _teacher(request)
        course.save()
        messages.success(request, _("Course created. Now add its modules."))
        return redirect("backoffice_courses:module-list", pk=course.pk)
    return backoffice_render(request, "backoffice/_generic_form.html", {
        "form": form, "title": _("New course"), "submit_label": _("Create course"),
        "icon": "fa-solid fa-plus", "cancel_url": reverse("backoffice_courses:course-list"),
    }, active=ACTIVE)


@teacher_required
def course_edit(request, pk):
    course = get_object_or_404(Course, pk=pk)
    form = CourseForm(request.POST or None, instance=course)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Course saved."))
        return redirect("backoffice_courses:module-list", pk=course.pk)
    return backoffice_render(request, "backoffice/_generic_form.html", {
        "form": form, "title": _("Edit course"), "submit_label": _("Save course"),
        "icon": "fa-solid fa-pen-to-square",
        "cancel_url": reverse("backoffice_courses:module-list", kwargs={"pk": course.pk}),
    }, active=ACTIVE)


@teacher_required
def course_delete(request, pk):
    course = get_object_or_404(Course, pk=pk)
    if request.method == "POST":
        course.delete()
        messages.success(request, _("Course deleted."))
        return redirect("backoffice_courses:course-list")
    return backoffice_render(request, "backoffice/_generic_confirm_delete.html", {
        "title": _("Delete course"), "object_label": course.title,
        "warning": _("This deletes the course with all its modules, lessons and scripts."),
        "cancel_url": reverse("backoffice_courses:course-list"),
    }, active=ACTIVE)


# --- modules ---------------------------------------------------------------

@teacher_required
def module_list(request, pk):
    course = get_object_or_404(Course, pk=pk)
    modules = course.modules.select_related("unlocks_badge").order_by("order", "id")
    return backoffice_render(request, "academy/backoffice/module_list.html", {
        "course": course, "modules": modules,
    }, active=ACTIVE)


def _render_module_form(request, course, module, form):
    ctx = {
        "course": course, "module": module, "form": form,
        "title": _("Edit module") if module else _("New module"),
        "submit_label": _("Save module") if module else _("Create module"),
        "skill_groups": SkillGroup.objects.order_by("order", "title"),
    }
    if module:
        ctx["lessons"] = module.lessons.order_by("order", "id")
        ctx["scripts"] = module.scripts.all()
    return backoffice_render(request, "academy/backoffice/module_form.html", ctx, active=ACTIVE)


@teacher_required
def module_create(request, pk):
    course = get_object_or_404(Course, pk=pk)
    form = ModuleForm(request.POST or None, course=course)
    if request.method == "POST" and form.is_valid():
        module = form.save(commit=False)
        module.owner = _teacher(request)
        module.save()
        form.save_m2m()
        messages.success(request, _("Module created."))
        return redirect("backoffice_courses:module-edit", pk=module.pk)
    return _render_module_form(request, course, None, form)


@teacher_required
def module_edit(request, pk):
    module = get_object_or_404(CourseModule.objects.select_related("course"), pk=pk)
    form = ModuleForm(request.POST or None, instance=module, course=module.course)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Module saved."))
        return redirect("backoffice_courses:module-edit", pk=module.pk)
    return _render_module_form(request, module.course, module, form)


@teacher_required
def module_delete(request, pk):
    module = get_object_or_404(CourseModule.objects.select_related("course"), pk=pk)
    course = module.course
    if request.method == "POST":
        module.delete()
        messages.success(request, _("Module deleted."))
        return redirect("backoffice_courses:module-list", pk=course.pk)
    return backoffice_render(request, "backoffice/_generic_confirm_delete.html", {
        "title": _("Delete module"), "object_label": module.title,
        "warning": _("This deletes the module with its lessons and scripts."),
        "cancel_url": reverse("backoffice_courses:module-list", kwargs={"pk": course.pk}),
    }, active=ACTIVE)


@teacher_required
@require_POST
def module_reorder(request, pk):
    course = get_object_or_404(Course, pk=pk)
    _apply_reorder(list(course.modules.order_by("order", "id")),
                   request.POST.get("item"), request.POST.get("direction"))
    return redirect("backoffice_courses:module-list", pk=course.pk)


# --- lessons ---------------------------------------------------------------

@teacher_required
def lesson_create(request, pk):
    module = get_object_or_404(CourseModule, pk=pk)
    form = LessonForm(request.POST or None, module=module)
    if request.method == "POST" and form.is_valid():
        lesson = form.save(commit=False)
        lesson.owner = _teacher(request)
        lesson.save()
        form.save_m2m()
        messages.success(request, _("Lesson added."))
        return redirect("backoffice_courses:module-edit", pk=module.pk)
    return backoffice_render(request, "backoffice/_generic_form.html", {
        "form": form, "title": _("New lesson"), "submit_label": _("Add lesson"),
        "icon": "fa-solid fa-plus",
        "back_url": reverse("backoffice_courses:module-edit", kwargs={"pk": module.pk}),
        "back_label": module.title,
        "cancel_url": reverse("backoffice_courses:module-edit", kwargs={"pk": module.pk}),
    }, active=ACTIVE)


@teacher_required
def lesson_edit(request, pk):
    lesson = get_object_or_404(Lesson.objects.select_related("module"), pk=pk)
    form = LessonForm(request.POST or None, instance=lesson, module=lesson.module)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Lesson saved."))
        return redirect("backoffice_courses:module-edit", pk=lesson.module.pk)
    return backoffice_render(request, "backoffice/_generic_form.html", {
        "form": form, "title": _("Edit lesson"), "submit_label": _("Save lesson"),
        "icon": "fa-solid fa-pen-to-square",
        "cancel_url": reverse("backoffice_courses:module-edit", kwargs={"pk": lesson.module.pk}),
    }, active=ACTIVE)


@teacher_required
def lesson_delete(request, pk):
    lesson = get_object_or_404(Lesson.objects.select_related("module"), pk=pk)
    module = lesson.module
    if request.method == "POST":
        lesson.delete()
        messages.success(request, _("Lesson deleted."))
        return redirect("backoffice_courses:module-edit", pk=module.pk)
    return backoffice_render(request, "backoffice/_generic_confirm_delete.html", {
        "title": _("Delete lesson"), "object_label": lesson.title,
        "cancel_url": reverse("backoffice_courses:module-edit", kwargs={"pk": module.pk}),
    }, active=ACTIVE)


@teacher_required
@require_POST
def lesson_reorder(request, pk):
    module = get_object_or_404(CourseModule, pk=pk)
    _apply_reorder(list(module.lessons.order_by("order", "id")),
                   request.POST.get("item"), request.POST.get("direction"))
    return redirect("backoffice_courses:module-edit", pk=module.pk)


# --- scripts (rich instructional pages) ------------------------------------

@teacher_required
def script_create(request, pk):
    module = get_object_or_404(CourseModule, pk=pk)
    form = ScriptForm(request.POST or None, module=module)
    formset = ScriptSectionFormSet(request.POST or None, instance=Script())
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        script = form.save(commit=False)
        script.author = getattr(request.user, "community_profile", None)
        script.save()
        formset.instance = script
        formset.save()
        _renumber_sections(formset)
        messages.success(request, _("Script added."))
        return redirect("backoffice_courses:module-edit", pk=module.pk)
    return backoffice_render(request, "academy/backoffice/script_form.html", {
        "module": module, "form": form, "formset": formset,
        "title": _("New script"), "submit_label": _("Create script"),
    }, active=ACTIVE)


@teacher_required
def script_edit(request, pk):
    script = get_object_or_404(Script.objects.select_related("module"), pk=pk)
    form = ScriptForm(request.POST or None, instance=script, module=script.module)
    formset = ScriptSectionFormSet(request.POST or None, instance=script)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        form.save()
        formset.save()
        _renumber_sections(formset)
        messages.success(request, _("Script saved."))
        return redirect("backoffice_courses:module-edit", pk=script.module.pk)
    return backoffice_render(request, "academy/backoffice/script_form.html", {
        "module": script.module, "script": script, "form": form, "formset": formset,
        "title": _("Edit script"), "submit_label": _("Save script"),
    }, active=ACTIVE)


@teacher_required
def script_delete(request, pk):
    script = get_object_or_404(Script.objects.select_related("module"), pk=pk)
    module = script.module
    if request.method == "POST":
        script.delete()
        messages.success(request, _("Script deleted."))
        return redirect("backoffice_courses:module-edit", pk=module.pk)
    return backoffice_render(request, "backoffice/_generic_confirm_delete.html", {
        "title": _("Delete script"), "object_label": script.title,
        "cancel_url": reverse("backoffice_courses:module-edit", kwargs={"pk": module.pk}),
    }, active=ACTIVE)
