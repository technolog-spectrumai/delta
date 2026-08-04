"""Teacher back-office: author courses (Course -> Module -> Lesson / Script)."""

from django.contrib import messages
from django.db import transaction
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
    WelcomeCopyForm,
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
    WelcomeCopy,
)

ACTIVE = "courses"


def _teacher(request):
    person = getattr(request.user, "community_profile", None)
    return Teacher.objects.filter(person=person).first() if person else None


def _renumber_sections(formset):
    """Assign section order 1..n, respecting the submitted (drag-set) order."""
    live = [
        form for form in formset.forms
        if hasattr(form, "cleaned_data") and not form.cleaned_data.get("DELETE")
        and form.instance.pk
    ]
    live.sort(key=lambda form: form.cleaned_data.get("order") or 0)
    for i, form in enumerate(live, start=1):
        # Always persist: an order-only reorder of an existing row is skipped by
        # formset.save() (has_changed ignores `order`), so the DB order is only
        # corrected here — and form.instance.order already holds the cleaned value,
        # making an `if obj.order != i` guard compare the wrong thing.
        obj = form.instance
        obj.order = i
        obj.save(update_fields=["order"])


# Slides are renumbered identically to script sections (drag-set order → 1..n).


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


def _ensure_authoring_file(request, lesson, field, suffix, file_type, make_xml):
    """The lesson's authored file (deck or notes document), created on demand.

    The panel's job is plumbing: make sure the vault file exists on ``field``,
    then the caller hands over to the editor every other file of that kind uses.
    Created owned by the teacher who first opened the page, in their personal
    bucket. Returns the file, or None with a message when it belongs to another
    teacher — the editors are owner-only, and a message naming the owner beats
    a bare 404 from a button that looks broken.
    """
    from django.core.files.base import ContentFile
    from django.utils.text import slugify

    from toto.vault.models import VaultFile

    from toto.backoffice.vault_upload import _default_bucket

    vault_file = getattr(lesson, field)
    if vault_file is None:
        xml = make_xml(lesson.title)
        base = slugify(lesson.title or "lesson") or "lesson"
        name = f"{base}-{suffix}"
        bucket = _default_bucket(request.user)
        vault_file = VaultFile(
            owner=request.user, title=name,
            key=_unique_authoring_key(f"{base}-{suffix.rsplit('.', 1)[0]}", bucket),
            file_type=file_type, bucket=bucket,
            is_public=False, is_encrypted=False)
        vault_file.file.save(name, ContentFile(xml), save=True)
        vault_file.content_hash = vault_file.create_hash()
        vault_file.save(update_fields=["content_hash"])
        setattr(lesson, field, vault_file)
        lesson.save(update_fields=[field])

    if vault_file.owner_id != request.user.id:
        messages.info(request, _(
            "This file belongs to %(owner)s — only its owner can edit it."
        ) % {"owner": vault_file.owner.username})
        return None
    return vault_file


def _unique_authoring_key(base, bucket):
    from toto.vault.models import VaultFile
    key, n = base, 1
    while VaultFile.objects.filter(bucket=bucket, key=key).exists():
        n += 1
        key = f"{base}-{n}"
    return key


@teacher_required
def presentation_edit(request, pk):
    """Open the lesson's slide deck in memo's editor, creating it on demand."""
    from toto.memo import presentation_format

    lesson = get_object_or_404(Lesson.objects.select_related("module"), pk=pk)
    vault_file = _ensure_authoring_file(
        request, lesson, "presentation_file", "deck.pml", "presentation",
        lambda title: presentation_format.dumps(
            presentation_format.new_presentation(title=title)).encode("utf-8"))
    if vault_file is None:
        return redirect("backoffice_courses:lesson-edit", pk=lesson.pk)
    return redirect("memo:edit", vault_file.pk)


@teacher_required
def notes_document_edit(request, pk):
    """Open the lesson's authored notes in cyprian's writer, creating on demand.

    The document parallel of the deck above: notes are a cyprian document, so
    the teacher writes real pages with images and formulas, exports a PDF from
    the same file, and students read it behind the enrollment gate — see
    academy.views.lesson_notes_document.
    """
    from toto.cyprian import document_format

    lesson = get_object_or_404(Lesson.objects.select_related("module"), pk=pk)
    vault_file = _ensure_authoring_file(
        request, lesson, "notes_document", "notatki.xml", "document",
        lambda title: document_format.dumps(
            document_format.new_document(title=title)).encode("utf-8"))
    if vault_file is None:
        return redirect("backoffice_courses:lesson-edit", pk=lesson.pk)
    return redirect("cyprian:edit", vault_file.pk)


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


@teacher_required
@require_POST
def cohort_award_certificates(request, pk):
    """Mark every cohort member's course complete and issue each a certificate.

    Reuses the single-student mechanic (set completed_at + save() -> the
    CourseEnrollment.save() override auto-issues an idempotent Certificate).
    Already-complete members keep their original completion date.
    """
    cohort = get_object_or_404(Cohort.objects.select_related("course"), pk=pk)
    issued = already = 0
    with transaction.atomic():
        for membership in cohort.memberships.select_related("student__person"):
            enrollment, _created = CourseEnrollment.objects.get_or_create(
                student=membership.student, course=cohort.course)
            if enrollment.completed_at:
                already += 1
                continue
            enrollment.completed_at = timezone.now()
            enrollment.save()  # save() auto-issues the Certificate
            issued += 1
    if issued or already:
        messages.success(request, _(
            "Issued %(n)d certificate(s); %(m)d member(s) were already complete."
        ) % {"n": issued, "m": already})
    else:
        messages.info(request, _("This cohort has no members yet."))
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


# --- people: promote / demote teachers -------------------------------------

ACTIVE_PEOPLE = "people"


def _promote_candidates(query):
    """Login-linked People who are not already active teachers, matching query."""
    query = (query or "").strip()
    if not query:
        return Person.objects.none()
    return (Person.objects.filter(user__isnull=False)
            .filter(Q(display_name__icontains=query)
                    | Q(email__icontains=query)
                    | Q(user__username__icontains=query))
            .exclude(academy_teacher__is_active=True)
            .order_by("display_name")[:20])


@teacher_required
def people_list(request):
    teachers = (Person.objects.filter(academy_teacher__isnull=False)
                .select_related("academy_teacher", "user")
                .order_by("display_name"))
    query = request.GET.get("q", "")
    return backoffice_render(request, "academy/backoffice/people_list.html", {
        "teachers": teachers,
        "query": query,
        "results": list(_promote_candidates(query)) if query else [],
    }, active=ACTIVE_PEOPLE)


@teacher_required
@require_POST
def promote_teacher(request, pk):
    person = get_object_or_404(Person, pk=pk)
    Teacher.objects.update_or_create(person=person, defaults={"is_active": True})
    if person.user_id is None:
        messages.warning(request, _(
            "%(name)s has no login account yet, so they cannot enter the panel "
            "until one is linked.") % {"name": person.display_name})
    else:
        messages.success(request, _("%(name)s is now a teacher.") % {"name": person.display_name})
    return redirect("backoffice_people:people-list")


ACTIVE_WELCOME = "welcome"


@teacher_required
def welcome_edit(request):
    copy = WelcomeCopy.current()
    form = WelcomeCopyForm(request.POST or None, instance=copy)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Welcome page updated."))
        return redirect("backoffice_welcome:welcome-edit")
    return backoffice_render(request, "backoffice/_generic_form.html", {
        "form": form, "title": _("Welcome page"), "submit_label": _("Save"),
        "icon": "fa-solid fa-door-open", "cancel_url": reverse("backoffice:dashboard"),
    }, active=ACTIVE_WELCOME)


@teacher_required
@require_POST
def demote_teacher(request, pk):
    person = get_object_or_404(Person, pk=pk)
    if person == getattr(request.user, "community_profile", None):
        messages.error(request, _("You cannot remove your own teacher access."))
    else:
        Teacher.objects.filter(person=person).update(is_active=False)
        if getattr(person.user, "is_staff", False):
            # Staff bypass the teacher gate, so deactivating the row does not lock
            # them out — say so plainly instead of claiming access was revoked.
            messages.warning(request, _(
                "%(name)s still has staff access; remove their staff status in the "
                "admin to fully revoke panel access.") % {"name": person.display_name})
        else:
            messages.success(request, _("%(name)s is no longer a teacher.") % {"name": person.display_name})
    return redirect("backoffice_people:people-list")
