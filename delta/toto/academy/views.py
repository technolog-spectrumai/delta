import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView
from django.views.generic import DetailView, FormView, ListView

from toto.competence.models import SkillBadge, SkillGroup
from toto.ui import PageProcessor
from toto.verbena.views import PageDetailMixin

from .forms import PathTaskStepForm, PersonalPathGoalForm
from .models import (
    Certificate, Cohort, Course, CourseModule, PersonalPath, PersonalPathStep,
    Script, Student, StudentBadge, Teacher,
)
from .paths import generate_steps, regenerate_path, sync_path


def build_skill_tree_graph(tree):
    badges = list(
        tree.badges
        .select_related("group")
        .prefetch_related(
            "prerequisites",
            "unlocking_modules",
            "unlocking_modules__course",
        )
        .order_by("order", "title")
    )
    badge_ids = {badge.pk for badge in badges}

    nodes = [
        {
            "data": {
                "id": f"group-{tree.pk}",
                "label": tree.title,
                "kind": "group",
                "description": tree.description,
                "url": reverse("academy:skill-tree-detail", kwargs={"slug": tree.slug}),
            }
        }
    ]
    edges = []

    for badge in badges:
        courses = []
        seen_course_ids = set()

        for module in badge.unlocking_modules.all():
            course = module.course
            if course.pk in seen_course_ids:
                continue

            seen_course_ids.add(course.pk)
            courses.append(
                {
                    "title": course.title,
                    "url": course.get_absolute_url(),
                }
            )

        nodes.append(
            {
                "data": {
                    "id": f"badge-{badge.pk}",
                    "label": badge.title,
                    "kind": "badge",
                    "description": badge.description,
                    "icon": badge.icon,
                    "courses": courses,
                    "course_count": len(courses),
                }
            }
        )

        if not badge.prerequisites.exists():
            edges.append(
                {
                    "data": {
                        "id": f"group-{tree.pk}-badge-{badge.pk}",
                        "source": f"group-{tree.pk}",
                        "target": f"badge-{badge.pk}",
                    }
                }
            )

        for prerequisite in badge.prerequisites.all():
            if prerequisite.pk not in badge_ids:
                continue

            edges.append(
                {
                    "data": {
                        "id": f"badge-{prerequisite.pk}-badge-{badge.pk}",
                        "source": f"badge-{prerequisite.pk}",
                        "target": f"badge-{badge.pk}",
                    }
                }
            )

    return {
        "tree": {
            "id": tree.pk,
            "title": tree.title,
            "slug": tree.slug,
            "description": tree.description,
            "url": reverse("academy:skill-tree-detail", kwargs={"slug": tree.slug}),
            "badge_count": len(badges),
        },
        "elements": nodes + edges,
    }


def build_student_progress_graph(student):
    earned_badge_ids = set(
        student.badges.values_list("id", flat=True)
    ) if student else set()
    enrolled_course_ids = set(
        student.enrolled_courses.values_list("id", flat=True)
    ) if student else set()

    groups = (
        SkillGroup.objects
        .prefetch_related(
            "badges",
            "badges__prerequisites",
            "badges__unlocking_modules",
            "badges__unlocking_modules__course",
        )
        .order_by("order", "title")
    )

    elements = []

    for group in groups:
        group_id = f"group-{group.pk}"
        elements.append(
            {
                "data": {
                    "id": group_id,
                    "label": group.title,
                    "kind": "group",
                    "status": "group",
                    "url": reverse("academy:skill-tree-detail", kwargs={"slug": group.slug}),
                }
            }
        )

        for badge in group.badges.all().order_by("order", "title"):
            prerequisite_ids = set(badge.prerequisites.values_list("id", flat=True))
            same_group_prerequisites = [
                prerequisite
                for prerequisite in badge.prerequisites.all()
                if prerequisite.group_id == group.pk
            ]
            module_courses = []
            module_course_ids = set()

            for module in badge.unlocking_modules.all():
                course = module.course
                if course.pk in module_course_ids:
                    continue

                module_course_ids.add(course.pk)
                module_courses.append(
                    {
                        "title": course.title,
                        "url": course.get_absolute_url(),
                    }
                )

            if badge.pk in earned_badge_ids:
                status = "earned"
            elif prerequisite_ids.issubset(earned_badge_ids):
                status = "available"
            else:
                status = "locked"

            if status == "available" and enrolled_course_ids.intersection(module_course_ids):
                status = "current"

            elements.append(
                {
                    "data": {
                        "id": f"badge-{badge.pk}",
                        "label": badge.title,
                        "kind": "badge",
                        "status": status,
                        "description": badge.description,
                        "courses": module_courses,
                    }
                }
            )

            if not same_group_prerequisites:
                elements.append(
                    {
                        "data": {
                            "id": f"{group_id}-badge-{badge.pk}",
                            "source": group_id,
                            "target": f"badge-{badge.pk}",
                        }
                    }
                )

            for prerequisite in same_group_prerequisites:
                elements.append(
                    {
                        "data": {
                            "id": f"badge-{prerequisite.pk}-badge-{badge.pk}",
                            "source": f"badge-{prerequisite.pk}",
                            "target": f"badge-{badge.pk}",
                        }
                    }
                )

    return {
        "elements": elements,
        "summary": {
            "earned": len(earned_badge_ids),
            "enrolled": len(enrolled_course_ids),
        },
    }


class AcademyContextMixin:
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


class CourseListView(AcademyContextMixin, ListView):
    model = Course
    template_name = "academy/course_list.html"
    context_object_name = "courses"

    def get_queryset(self):
        queryset = (
            Course.objects
            .select_related("author", "owner", "owner__person")
            .prefetch_related(
                "modules",
                "modules__lessons",
                "modules__lessons__video_file",
                "modules__unlocks_badge",
            )
            .filter(is_virtual=False)
        )

        if not self.request.user.is_staff:
            queryset = queryset.filter(is_published=True)

        return queryset.order_by("order", "title")


class TeacherDetailView(AcademyContextMixin, DetailView):
    model = Teacher
    template_name = "academy/teacher_detail.html"
    context_object_name = "teacher"

    def get_queryset(self):
        return Teacher.objects.select_related("person")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        teacher = self.object

        context["courses"] = (
            teacher.owned_courses
            .prefetch_related("modules", "modules__lessons")
            .order_by("order", "title")
        )
        context["modules"] = (
            teacher.owned_modules
            .select_related("course")
            .order_by("course__order", "course__title", "order", "title")
        )
        context["lessons"] = (
            teacher.owned_lessons
            .select_related("module", "module__course", "video_file", "notes_file")
            .order_by("module__course__order", "module__order", "order", "title")
        )
        context["certificates"] = (
            Certificate.objects
            .filter(person=teacher.person)
            .select_related("course")
            .order_by("-granted_at")
        )
        context["experiences"] = teacher.person.experiences.all()

        return context


class StudentProgressView(LoginRequiredMixin, AcademyContextMixin, TemplateView):
    template_name = "academy/student_progress.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        person = getattr(self.request.user, "community_profile", None)
        student = None

        if person:
            student, _ = Student.objects.get_or_create(person=person)

        earned_badges = SkillBadge.objects.none()
        enrolled_courses = Course.objects.none()
        certificates = Certificate.objects.none()

        if student:
            earned_badges = (
                student.badges
                .select_related("group")
                .order_by("group__order", "order", "title")
            )
            enrolled_courses = (
                student.enrolled_courses
                .order_by("order", "title")
            )
            certificates = (
                Certificate.objects
                .filter(person=student.person)
                .select_related("course")
                .order_by("-granted_at")
            )

        context["person"] = person
        context["student"] = student
        context["earned_badges"] = earned_badges
        context["enrolled_courses"] = enrolled_courses
        context["certificates"] = certificates
        context["progress_graph"] = build_student_progress_graph(student)

        return context


class CourseDetailView(AcademyContextMixin, DetailView):
    model = Course
    template_name = "academy/course_detail.html"
    context_object_name = "course"

    def get_queryset(self):
        queryset = (
            Course.objects
            .select_related("author", "owner", "owner__person")
            .prefetch_related(
                "modules",
                "modules__lessons",
                "modules__unlocks_badge",
                "modules__unlocks_badge__group",
                "modules__unlocks_badge__prerequisites",
                "modules__verbena_page",
                "modules__attached_quizzes",
                "modules__owner",
                "modules__owner__person",
                "modules__lessons__owner",
                "modules__lessons__owner__person",
            )
        )

        if not self.request.user.is_staff:
            queryset = queryset.filter(is_published=True)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        course = self.object

        modules = (
            CourseModule.objects
            .filter(course=course)
            .select_related(
                "unlocks_badge",
                "unlocks_badge__group",
                "verbena_page",
                "owner",
                "owner__person",
            )
            .prefetch_related(
                "lessons",
                "lessons__video_file",
                "lessons__notes_file",
                "lessons__owner",
                "lessons__owner__person",
                "attached_quizzes",
                "attached_quizzes__questions",
                "unlocks_badge__prerequisites",
            )
            .order_by("order", "id")
        )
        lessons = []

        for module in modules:
            for lesson in module.lessons.all().order_by("order", "id"):
                lessons.append(lesson)

        badges = SkillBadge.objects.filter(
            unlocking_modules__course=course
        ).select_related("group").prefetch_related(
            "prerequisites"
        ).distinct().order_by("group__order", "order", "title")

        skill_groups = SkillGroup.objects.filter(
            badges__unlocking_modules__course=course
        ).distinct().order_by("order", "title")

        context["modules"] = modules
        context["lessons"] = lessons
        context["badges"] = badges
        context["skill_groups"] = skill_groups
        context["badge_count"] = badges.count()
        context["lesson_count"] = len(lessons)

        return context


class SkillForestView(AcademyContextMixin, ListView):
    model = SkillGroup
    template_name = "academy/skill_forest.html"
    context_object_name = "trees"

    def get_queryset(self):
        return (
            SkillGroup.objects
            .prefetch_related(
                "badges",
                "badges__prerequisites",
                "badges__unlocking_modules",
                "badges__unlocking_modules__course",
            )
            .order_by("order", "title")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        trees = list(context["trees"])
        context["tree_graphs"] = [build_skill_tree_graph(tree) for tree in trees]
        return context


class SkillTreeDetailView(AcademyContextMixin, DetailView):
    model = SkillGroup
    template_name = "academy/skill_tree_detail.html"
    context_object_name = "tree"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return (
            SkillGroup.objects
            .prefetch_related(
                "badges",
                "badges__prerequisites",
                "badges__unlocking_modules",
                "badges__unlocking_modules__course",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        tree = self.object

        context["badges"] = (
            tree.badges
            .select_related("group")
            .prefetch_related(
                "prerequisites",
                "unlocking_modules",
                "unlocking_modules__course",
            )
            .order_by("order", "title")
        )
        context["tree_graph"] = build_skill_tree_graph(tree)

        return context


class ScriptDetailView(PageDetailMixin, DetailView):
    model = Script
    template_name = "academy/script_detail.html"
    context_object_name = "page"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["sections"] = self.render_sections(self.object)
        context["back_url"] = "academy:course-detail"
        context["back_url_slug"] = self.object.module.course.slug
        context["back_label"] = self.object.module.course.title
        context["page_type_label"] = "Script"
        return PageProcessor().decorate(context, self.request)


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def certificate_detail(request, uuid):
    certificate = get_object_or_404(
        Certificate.objects.select_related(
            "person", "course", "signed_by", "signed_by__person", "signing_key"
        ),
        uuid=uuid,
    )
    person = getattr(request.user, "community_profile", None)
    teacher = None
    if person:
        teacher = Teacher.objects.filter(person=person).first()

    return _render(request, "academy/certificate_detail.html", {
        "certificate": certificate,
        "teacher": teacher,
    })


def certificate_sign(request, uuid):
    from toto.gervazy.models import UserStrongbox, WrappedDataKey
    from toto.gervazy.signing import SigningService, SigningError
    from toto.gervazy.crypto import GervazyCryptoSession

    certificate = get_object_or_404(
        Certificate.objects.select_related("person", "course"),
        uuid=uuid,
    )

    person = getattr(request.user, "community_profile", None)
    teacher = None
    if person:
        teacher = Teacher.objects.filter(person=person).first()

    strongboxes = []
    existing_signing_key = None
    if person and request.user.is_authenticated:
        strongboxes = list(UserStrongbox.objects.filter(owner=request.user))
        existing_signing_key = SigningService.get_active_signing_key(person)

    def _ctx():
        required_strongbox = None
        if existing_signing_key:
            required_strongbox = existing_signing_key.encrypted_private_key.strongbox
        return {
            "certificate": certificate,
            "teacher": teacher,
            "strongboxes": strongboxes,
            "existing_signing_key": existing_signing_key,
            "required_strongbox": required_strongbox,
        }

    if request.method == "POST":
        if not teacher:
            messages.error(request, "Only teachers (professors) can sign certificates.")
            return redirect("academy:certificate-detail", uuid=certificate.uuid)
        if certificate.cryptographic_signature:
            messages.info(request, "This certificate has already been cryptographically signed.")
            return redirect("academy:certificate-detail", uuid=certificate.uuid)

        password = request.POST.get("strongbox_password", "").strip()
        strongbox_id = request.POST.get("strongbox_id", "").strip()

        if not password:
            messages.error(request, "Strongbox password is required to sign.")
            return _render(request, "academy/certificate_sign.html", _ctx())

        existing_signing_key = SigningService.get_active_signing_key(person)

        if existing_signing_key:
            signing_strongbox = existing_signing_key.encrypted_private_key.strongbox
            wrapped_key = None
        else:
            try:
                if strongbox_id:
                    signing_strongbox = UserStrongbox.objects.get(pk=strongbox_id, owner=request.user)
                elif strongboxes:
                    signing_strongbox = strongboxes[0]
                else:
                    messages.error(request, "No strongbox found. Set one up in Gervazy first.")
                    return redirect("academy:certificate-detail", uuid=certificate.uuid)
            except UserStrongbox.DoesNotExist:
                messages.error(request, "Invalid strongbox selection.")
                return _render(request, "academy/certificate_sign.html", _ctx())

            wrapped_key = (
                WrappedDataKey.objects
                .filter(strongbox=signing_strongbox, state="active")
                .select_related("vmk")
                .first()
            )
            if not wrapped_key:
                messages.error(
                    request,
                    f"No active data key found in strongbox \"{signing_strongbox.name}\". "
                    "Initialize a data key in Gervazy before signing for the first time.",
                )
                return _render(request, "academy/certificate_sign.html", _ctx())

        try:
            session = GervazyCryptoSession(signing_strongbox, password)

            signed_at = timezone.now()
            payload = SigningService.canonical_certificate_payload(certificate, teacher, signed_at)
            doc_sig = SigningService.sign_document(session, person, payload, wrapped_key=wrapped_key)

            certificate.signed_by = teacher
            certificate.signing_payload = payload.decode("utf-8")
            certificate.cryptographic_signature = doc_sig.signature_b64
            certificate.signing_key = SigningService.get_active_signing_key(person).encrypted_private_key
            certificate.save(update_fields=[
                "signed_by", "signing_payload", "cryptographic_signature", "signing_key",
            ])

            session.close()

        except Exception as exc:
            messages.error(request, f"Signing failed: {exc}")
            return _render(request, "academy/certificate_sign.html", _ctx())

        messages.success(request, "Certificate signed with cryptographic signature.")
        return redirect("academy:certificate-detail", uuid=certificate.uuid)

    return _render(request, "academy/certificate_sign.html", _ctx())


@login_required
def course_metrics(request, slug):
    course = get_object_or_404(
        Course.objects.prefetch_related(
            "modules",
            "modules__attached_quizzes",
            "cohorts",
            "cohorts__teacher",
            "cohorts__memberships__student__person",
            "students",
            "students__person",
            "course_enrollments__student__person",
        ),
        slug=slug,
    )

    person = getattr(request.user, "community_profile", None)
    is_teacher = person and Teacher.objects.filter(person=person).exists()
    if not is_teacher:
        from django.http import Http404
        raise Http404

    total_enrolled = course.course_enrollments.count()
    total_completed = course.course_enrollments.filter(completed_at__isnull=False).count()

    # Per-module quiz attempt rates
    module_stats = []
    for module in course.modules.all():
        quizzes = list(module.attached_quizzes.filter(is_published=True))
        quiz_rows = []
        for quiz in quizzes:
            attempts = quiz.attempts.filter(completed_at__isnull=False).count()
            quiz_rows.append({"quiz": quiz, "completed_attempts": attempts})
        module_stats.append({
            "module": module,
            "quiz_rows": quiz_rows,
        })

    # Cohort breakdown
    cohort_stats = []
    for cohort in course.cohorts.all():
        member_ids = set(cohort.memberships.values_list("student_id", flat=True))
        completed = course.course_enrollments.filter(
            student_id__in=member_ids,
            completed_at__isnull=False,
        ).count()
        cohort_stats.append({
            "cohort": cohort,
            "member_count": len(member_ids),
            "completed": completed,
            "pct": round(completed / len(member_ids) * 100) if member_ids else 0,
        })

    enrollment_chart = json.dumps({
        "chart_type": "doughnut",
        "labels": ["Completed", "In progress"],
        "datasets": [{
            "data": [total_completed, max(total_enrolled - total_completed, 0)],
            "backgroundColor": ["#10B981", "#4F46E5"],
        }],
    })

    context = {
        "course": course,
        "total_enrolled": total_enrolled,
        "total_completed": total_completed,
        "module_stats": module_stats,
        "cohort_stats": cohort_stats,
        "enrollment_chart_json": enrollment_chart,
    }
    return render(request, "academy/course_metrics.html", PageProcessor().decorate(context, request))


# ---------------------------------------------------------------------------
# Personalized learning paths (self-service)
# ---------------------------------------------------------------------------

def _get_own_path(request, pk):
    person = getattr(request.user, "community_profile", None)
    if not person:
        raise Http404
    return get_object_or_404(
        PersonalPath.objects.select_related("goal_badge", "goal_group", "student"),
        pk=pk,
        student__person=person,
    )


class PersonalPathListView(LoginRequiredMixin, AcademyContextMixin, TemplateView):
    template_name = "academy/personal_path_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        person = getattr(self.request.user, "community_profile", None)
        student = None

        if person:
            student, _ = Student.objects.get_or_create(person=person)

        active_paths = []
        completed_paths = []
        archived_paths = []

        if student:
            paths = (
                student.personal_paths
                .select_related("goal_badge", "goal_badge__group", "goal_group")
                .prefetch_related("steps")
            )
            for path in paths:
                if path.status == PersonalPath.Status.ARCHIVED:
                    archived_paths.append({"path": path, "progress": None})
                    continue

                entry = {"path": path, "progress": sync_path(path)}
                if path.status == PersonalPath.Status.COMPLETED:
                    completed_paths.append(entry)
                else:
                    active_paths.append(entry)

        context["person"] = person
        context["student"] = student
        context["active_paths"] = active_paths
        context["completed_paths"] = completed_paths
        context["archived_paths"] = archived_paths

        return context


class PersonalPathCreateView(LoginRequiredMixin, AcademyContextMixin, FormView):
    template_name = "academy/personal_path_form.html"
    form_class = PersonalPathGoalForm

    def get_student(self):
        person = getattr(self.request.user, "community_profile", None)
        if not person:
            return None
        student, _ = Student.objects.get_or_create(person=person)
        return student

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["student"] = self.get_student()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        student = self.get_student()
        context["active_path"] = (
            PersonalPath.objects
            .filter(student=student, status=PersonalPath.Status.ACTIVE)
            .first()
            if student else None
        )
        return context

    def form_valid(self, form):
        student = self.get_student()
        if student is None:
            messages.error(
                self.request,
                "You need a community profile to create a learning path.",
            )
            return redirect("academy:personal-path-list")

        goal = form.cleaned_data["goal_badge"] or form.cleaned_data["goal_group"]

        try:
            with transaction.atomic():
                # One active path per student: starting a new goal archives
                # the current one.
                PersonalPath.objects.filter(
                    student=student, status=PersonalPath.Status.ACTIVE
                ).update(status=PersonalPath.Status.ARCHIVED)

                path = PersonalPath.objects.create(
                    student=student,
                    title=f"Path to {goal.title}",
                    goal_badge=form.cleaned_data["goal_badge"],
                    goal_group=form.cleaned_data["goal_group"],
                )
                generate_steps(path)
        except IntegrityError:
            existing = PersonalPath.objects.filter(
                student=student, status=PersonalPath.Status.ACTIVE
            ).first()
            if existing:
                return redirect(existing.get_absolute_url())
            return redirect("academy:personal-path-list")

        messages.success(self.request, f"Your path to {goal.title} is ready.")
        return redirect(path.get_absolute_url())


class PersonalPathDetailView(LoginRequiredMixin, AcademyContextMixin, DetailView):
    model = PersonalPath
    template_name = "academy/personal_path_detail.html"
    context_object_name = "path"

    def get_queryset(self):
        person = getattr(self.request.user, "community_profile", None)
        if not person:
            return PersonalPath.objects.none()
        return (
            PersonalPath.objects
            .select_related("goal_badge", "goal_badge__group", "goal_group", "student")
            .filter(student__person=person)
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        path = self.object

        context["progress"] = sync_path(path)
        context["steps"] = (
            path.steps
            .select_related("badge", "badge__group", "module", "module__course")
            .order_by("order", "id")
        )
        context["task_form"] = PathTaskStepForm()

        return context


@login_required
def personal_path_step_toggle(request, pk, step_pk):
    path = _get_own_path(request, pk)
    if request.method != "POST":
        return redirect(path.get_absolute_url())

    step = get_object_or_404(path.steps, pk=step_pk)

    if step.step_type == PersonalPathStep.StepType.BADGE:
        messages.info(
            request,
            "This step completes automatically when you earn the skill.",
        )
        return redirect(path.get_absolute_url())

    badge_earned = step.badge_id and StudentBadge.objects.filter(
        student=path.student, badge_id=step.badge_id
    ).exists()
    if step.is_completed and badge_earned:
        messages.info(
            request,
            "This step is backed by an earned skill and cannot be unchecked.",
        )
        return redirect(path.get_absolute_url())

    step.is_completed = not step.is_completed
    step.completed_at = timezone.now() if step.is_completed else None
    step.save(update_fields=["is_completed", "completed_at"])

    return redirect(path.get_absolute_url())


@login_required
def personal_path_task_add(request, pk):
    path = _get_own_path(request, pk)
    if request.method != "POST":
        return redirect(path.get_absolute_url())

    form = PathTaskStepForm(request.POST)
    if form.is_valid():
        last_order = path.steps.aggregate(max_order=Max("order"))["max_order"] or 0
        PersonalPathStep.objects.create(
            path=path,
            step_type=PersonalPathStep.StepType.TASK,
            title=form.cleaned_data["title"],
            note=form.cleaned_data["note"],
            order=last_order + 10,
        )
        messages.success(request, "Task added to your path.")
    else:
        messages.error(request, "Enter a task title.")

    return redirect(path.get_absolute_url())


@login_required
def personal_path_step_delete(request, pk, step_pk):
    path = _get_own_path(request, pk)
    if request.method != "POST":
        return redirect(path.get_absolute_url())

    step = get_object_or_404(
        path.steps, pk=step_pk, step_type=PersonalPathStep.StepType.TASK
    )
    step.delete()
    messages.success(request, "Task removed.")

    return redirect(path.get_absolute_url())


@login_required
def personal_path_regenerate(request, pk):
    path = _get_own_path(request, pk)
    if request.method != "POST":
        return redirect(path.get_absolute_url())

    regenerate_path(path)
    messages.success(
        request,
        "Path rebuilt against your current skills. "
        "Completed steps and your tasks were kept.",
    )

    return redirect(path.get_absolute_url())


@login_required
def personal_path_archive(request, pk):
    path = _get_own_path(request, pk)
    if request.method != "POST":
        return redirect(path.get_absolute_url())

    path.status = PersonalPath.Status.ARCHIVED
    path.save(update_fields=["status", "updated_at"])
    messages.success(request, "Path archived.")

    return redirect("academy:personal-path-list")


# ---------------------------------------------------------------------------
# Self-enrollment (subscription-gated)
# ---------------------------------------------------------------------------

@login_required
def course_enroll(request, slug):
    """
    POST-only. Enroll the current user in a course.

    Subscription check: if the course community requires an 'academy' subscription,
    the user must have an active one.  If no subscription plan is configured,
    enrollment is open (free pass).
    """
    from django.views.decorators.http import require_POST
    if request.method != "POST":
        return redirect("academy:course-detail", slug=slug)

    course = get_object_or_404(Course, slug=slug, is_published=True)

    # Subscription check — graceful: if subscriptions app absent or no gate, allow
    try:
        from toto.subscriptions.gates import is_subscribed, SubscriptionRequired
        # Check if any SubscriptionPlan with code "academy" exists and is active
        from toto.subscriptions.models import SubscriptionPlan
        academy_plans_exist = SubscriptionPlan.objects.filter(
            code__startswith="academy", status="active"
        ).exists()
        if academy_plans_exist and not is_subscribed(request.user, "academy"):
            messages.error(
                request,
                "An Academy subscription is required to enroll. Subscribe to continue.",
            )
            try:
                from django.urls import reverse as _rev
                return redirect(_rev("subscriptions:plan_list"))
            except Exception:
                return redirect("academy:course-detail", slug=slug)
    except ImportError:
        pass  # subscriptions app not installed → open enrollment

    # Get or create student profile
    person = getattr(request.user, "community_profile", None)
    if not person:
        messages.error(request, "You need a community profile to enroll.")
        return redirect("academy:course-detail", slug=slug)

    student, _ = Student.objects.get_or_create(person=person)
    from .models import CourseEnrollment
    _, created = CourseEnrollment.objects.get_or_create(student=student, course=course)
    if created:
        messages.success(request, f"You are now enrolled in {course.title}.")
    else:
        messages.info(request, f"You are already enrolled in {course.title}.")
    return redirect("academy:course-detail", slug=slug)
