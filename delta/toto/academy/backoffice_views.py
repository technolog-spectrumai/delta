"""Teacher back-office: author courses (Course -> Module -> Lesson / Script)."""

from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from toto.backoffice.access import teacher_required
from toto.backoffice.shell import backoffice_render
from toto.backoffice.utils import apply_reorder, unique_slug
from toto.backoffice.vault_upload import create_vault_file
from toto.people.models import Person

from .backoffice_forms import (
    CohortForm,
    CourseForm,
    LessonForm,
    ModuleForm,
    ScriptForm,
    ScriptSectionFormSet,
)
from toto.competence.models import SkillBadge, SkillGroup

from .models import (
    Certificate,
    Cohort,
    CohortMembership,
    Course,
    CourseEnrollment,
    CourseModule,
    Lesson,
    Script,
    Student,
    StudentBadge,
    Teacher,
)

ACTIVE = "courses"


def _teacher(request):
    person = getattr(request.user, "community_profile", None)
    return Teacher.objects.filter(person=person).first() if person else None


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
    if apply_reorder(course.modules.order_by("order", "id"), request):
        return HttpResponse(status=204)
    return redirect("backoffice_courses:module-list", pk=course.pk)


# --- lessons ---------------------------------------------------------------

def _apply_lesson_files(request, form, lesson):
    """Turn the lesson form's uploads into VaultFiles (or honour removals)."""
    changed = []
    video = form.cleaned_data.get("video_upload")
    if video:
        lesson.video_file = create_vault_file(
            request.user, video, is_public=False, file_type="video")
        changed.append("video_file")
    elif form.cleaned_data.get("remove_video"):
        lesson.video_file = None
        changed.append("video_file")

    notes = form.cleaned_data.get("notes_upload")
    if notes:
        lesson.notes_file = create_vault_file(
            request.user, notes, is_public=False, file_type="pdf")
        changed.append("notes_file")
    elif form.cleaned_data.get("remove_notes"):
        lesson.notes_file = None
        changed.append("notes_file")

    if changed:
        lesson.save(update_fields=changed)


@teacher_required
def lesson_create(request, pk):
    module = get_object_or_404(CourseModule, pk=pk)
    form = LessonForm(request.POST or None, request.FILES or None, module=module)
    if request.method == "POST" and form.is_valid():
        lesson = form.save(commit=False)
        lesson.owner = _teacher(request)
        lesson.save()
        form.save_m2m()
        _apply_lesson_files(request, form, lesson)
        messages.success(request, _("Lesson added."))
        return redirect("backoffice_courses:module-edit", pk=module.pk)
    return backoffice_render(request, "academy/backoffice/lesson_form.html", {
        "form": form, "module": module,
        "title": _("New lesson"), "submit_label": _("Add lesson"),
        "icon": "fa-solid fa-plus",
    }, active=ACTIVE)


@teacher_required
def lesson_edit(request, pk):
    lesson = get_object_or_404(Lesson.objects.select_related("module"), pk=pk)
    form = LessonForm(request.POST or None, request.FILES or None,
                      instance=lesson, module=lesson.module)
    if request.method == "POST" and form.is_valid():
        form.save()
        _apply_lesson_files(request, form, lesson)
        messages.success(request, _("Lesson saved."))
        return redirect("backoffice_courses:module-edit", pk=lesson.module.pk)
    return backoffice_render(request, "academy/backoffice/lesson_form.html", {
        "form": form, "module": lesson.module, "lesson": lesson,
        "title": _("Edit lesson"), "submit_label": _("Save lesson"),
        "icon": "fa-solid fa-pen-to-square",
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
    if apply_reorder(module.lessons.order_by("order", "id"), request):
        return HttpResponse(status=204)
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


# --- roster & enrolment ----------------------------------------------------

def _search_people(query):
    query = (query or "").strip()
    if not query:
        return Person.objects.none()
    return Person.objects.filter(
        Q(display_name__icontains=query)
        | Q(email__icontains=query)
        | Q(user__username__icontains=query)
    ).order_by("display_name")[:20]


def _enrol(person, course):
    student, _created = Student.objects.get_or_create(person=person)
    _enrollment, created = CourseEnrollment.objects.get_or_create(student=student, course=course)
    return created


@teacher_required
def roster(request, pk):
    course = get_object_or_404(Course, pk=pk)
    enrollments = (
        CourseEnrollment.objects.filter(course=course)
        .select_related("student__person")
        .order_by("student__person__display_name")
    )
    cohorts_by_student = {}
    for membership in (CohortMembership.objects
                       .filter(cohort__course=course)
                       .select_related("cohort")):
        cohorts_by_student.setdefault(membership.student_id, []).append(membership.cohort)
    rows = [{
        "student": e.student,
        "person": e.student.person,
        "cohorts": cohorts_by_student.get(e.student_id, []),
        "completed": bool(e.completed_at),
    } for e in enrollments]

    query = request.GET.get("q", "")
    enrolled_person_ids = {e.student.person_id for e in enrollments}
    return backoffice_render(request, "academy/backoffice/roster.html", {
        "course": course,
        "rows": rows,
        "cohorts": course.cohorts.all(),
        "query": query,
        "results": list(_search_people(query)) if query else [],
        "enrolled_person_ids": enrolled_person_ids,
    }, active=ACTIVE)


@teacher_required
@require_POST
def enrol_student(request, pk):
    course = get_object_or_404(Course, pk=pk)
    person = Person.objects.filter(pk=request.POST.get("person")).first()
    if person is None:
        messages.error(request, _("Person not found."))
    elif _enrol(person, course):
        messages.success(request, _("Enrolled %(name)s.") % {"name": person.display_name})
    else:
        messages.info(request, _("%(name)s is already enrolled.") % {"name": person.display_name})
    return redirect("backoffice_courses:roster", pk=course.pk)


@teacher_required
@require_POST
def unenrol_student(request, pk):
    course = get_object_or_404(Course, pk=pk)
    CourseEnrollment.objects.filter(
        course=course, student__person_id=request.POST.get("person")).delete()
    messages.success(request, _("Student unenrolled."))
    return redirect("backoffice_courses:roster", pk=course.pk)


# --- cohorts ---------------------------------------------------------------

def _render_cohort_form(request, course, cohort, form):
    ctx = {
        "course": course, "cohort": cohort, "form": form,
        "title": _("Edit cohort") if cohort else _("New cohort"),
        "submit_label": _("Save cohort") if cohort else _("Create cohort"),
    }
    if cohort:
        members = (cohort.memberships
                   .select_related("student__person")
                   .order_by("student__person__display_name"))
        query = request.GET.get("q", "")
        ctx.update({
            "members": members,
            "query": query,
            "results": list(_search_people(query)) if query else [],
            "member_person_ids": {m.student.person_id for m in members},
        })
    return backoffice_render(request, "academy/backoffice/cohort_form.html", ctx, active=ACTIVE)


@teacher_required
def cohort_create(request, pk):
    course = get_object_or_404(Course, pk=pk)
    form = CohortForm(request.POST or None, course=course)
    if request.method == "POST" and form.is_valid():
        cohort = form.save(commit=False)
        cohort.teacher = _teacher(request)
        cohort.save()
        messages.success(request, _("Cohort created."))
        return redirect("backoffice_courses:cohort-edit", pk=cohort.pk)
    return _render_cohort_form(request, course, None, form)


@teacher_required
def cohort_edit(request, pk):
    cohort = get_object_or_404(Cohort.objects.select_related("course"), pk=pk)
    form = CohortForm(request.POST or None, instance=cohort, course=cohort.course)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Cohort saved."))
        return redirect("backoffice_courses:cohort-edit", pk=cohort.pk)
    return _render_cohort_form(request, cohort.course, cohort, form)


@teacher_required
def cohort_delete(request, pk):
    cohort = get_object_or_404(Cohort.objects.select_related("course"), pk=pk)
    course = cohort.course
    if request.method == "POST":
        cohort.delete()
        messages.success(request, _("Cohort deleted."))
        return redirect("backoffice_courses:roster", pk=course.pk)
    return backoffice_render(request, "backoffice/_generic_confirm_delete.html", {
        "title": _("Delete cohort"), "object_label": cohort.title,
        "warning": _("This removes the cohort and its membership list (enrolments are kept)."),
        "cancel_url": reverse("backoffice_courses:roster", kwargs={"pk": course.pk}),
    }, active=ACTIVE)


@teacher_required
@require_POST
def cohort_add_member(request, pk):
    cohort = get_object_or_404(Cohort.objects.select_related("course"), pk=pk)
    person = Person.objects.filter(pk=request.POST.get("person")).first()
    if person is None:
        messages.error(request, _("Person not found."))
        return redirect("backoffice_courses:cohort-edit", pk=cohort.pk)
    student, _created = Student.objects.get_or_create(person=person)
    already = CohortMembership.objects.filter(cohort=cohort, student=student).exists()
    if not already and cohort.capacity and cohort.member_count >= cohort.capacity:
        messages.error(request, _("Cohort is full (capacity %(n)s).") % {"n": cohort.capacity})
        return redirect("backoffice_courses:cohort-edit", pk=cohort.pk)
    CohortMembership.objects.get_or_create(cohort=cohort, student=student)
    _enrol(person, cohort.course)  # cohort members are enrolled in the course
    messages.success(request, _("Added %(name)s to the cohort.") % {"name": person.display_name})
    return redirect("backoffice_courses:cohort-edit", pk=cohort.pk)


@teacher_required
@require_POST
def cohort_remove_member(request, pk):
    cohort = get_object_or_404(Cohort, pk=pk)
    CohortMembership.objects.filter(
        cohort=cohort, student__person_id=request.POST.get("person")).delete()
    messages.success(request, _("Removed from the cohort."))
    return redirect("backoffice_courses:cohort-edit", pk=cohort.pk)


# --- per-student: completion, badges, certificate --------------------------

def _course_student(pk, student_pk):
    course = get_object_or_404(Course, pk=pk)
    student = get_object_or_404(Student.objects.select_related("person"), pk=student_pk)
    return course, student


@teacher_required
def student_detail(request, pk, student_pk):
    course, student = _course_student(pk, student_pk)
    enrollment = CourseEnrollment.objects.filter(course=course, student=student).first()
    earned_ids = set(student.badges.values_list("id", flat=True))
    modules = (course.modules
               .select_related("unlocks_badge", "unlocks_badge__group")
               .order_by("order", "id"))
    module_rows = [{
        "module": m,
        "badge": m.unlocks_badge,
        "earned": m.unlocks_badge_id in earned_ids,
    } for m in modules]
    earned_badges = (student.badges.select_related("group")
                     .order_by("group__order", "order", "title"))
    other_badges = (SkillBadge.objects.exclude(id__in=earned_ids)
                    .select_related("group").order_by("group__order", "order", "title"))
    certificate = Certificate.objects.filter(person=student.person, course=course).first()
    return backoffice_render(request, "academy/backoffice/student_detail.html", {
        "course": course,
        "student": student,
        "person": student.person,
        "enrollment": enrollment,
        "module_rows": module_rows,
        "earned_badges": earned_badges,
        "other_badges": other_badges,
        "certificate": certificate,
    }, active=ACTIVE)


@teacher_required
@require_POST
def student_complete(request, pk, student_pk):
    course, student = _course_student(pk, student_pk)
    enrollment, _created = CourseEnrollment.objects.get_or_create(student=student, course=course)
    enrollment.completed_at = timezone.now()
    enrollment.save()  # save() auto-issues the Certificate
    messages.success(request, _("Marked complete — a certificate was issued."))
    return redirect("backoffice_courses:student-detail", pk=course.pk, student_pk=student.pk)


@teacher_required
@require_POST
def student_uncomplete(request, pk, student_pk):
    course, student = _course_student(pk, student_pk)
    enrollment = CourseEnrollment.objects.filter(student=student, course=course).first()
    if enrollment:
        enrollment.completed_at = None
        enrollment.save(update_fields=["completed_at"])
    # remove the auto-issued certificate only when it was never cryptographically signed
    Certificate.objects.filter(
        person=student.person, course=course, cryptographic_signature="").delete()
    messages.success(request, _("Marked as in progress."))
    return redirect("backoffice_courses:student-detail", pk=course.pk, student_pk=student.pk)


@teacher_required
@require_POST
def student_award_badge(request, pk, student_pk):
    course, student = _course_student(pk, student_pk)
    badge = SkillBadge.objects.filter(pk=request.POST.get("badge")).first()
    if badge is None:
        messages.error(request, _("Badge not found."))
    else:
        StudentBadge.objects.get_or_create(student=student, badge=badge)
        messages.success(request, _("Awarded “%(b)s”.") % {"b": badge.title})
    return redirect("backoffice_courses:student-detail", pk=course.pk, student_pk=student.pk)


@teacher_required
@require_POST
def student_revoke_badge(request, pk, student_pk):
    course, student = _course_student(pk, student_pk)
    StudentBadge.objects.filter(student=student, badge_id=request.POST.get("badge")).delete()
    messages.success(request, _("Badge revoked."))
    return redirect("backoffice_courses:student-detail", pk=course.pk, student_pk=student.pk)
