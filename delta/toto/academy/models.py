import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import get_language, gettext_lazy
from django.urls import reverse

from toto.competence.models import SkillBadge, SkillGroup
from toto.people.models import Person
from toto.verbena.models import AbstractPage, AbstractSection


class Teacher(models.Model):
    person = models.OneToOneField(
        Person,
        on_delete=models.CASCADE,
        related_name="academy_teacher",
    )
    title = models.CharField(max_length=200, blank=True)
    bio = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["person__display_name"]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        if self.title:
            return f"{self.person.display_name}, {self.title}"

        return self.person.display_name

    def get_absolute_url(self):
        return reverse("academy:teacher-detail", kwargs={"pk": self.pk})


class Course(models.Model):
    title = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="academy_courses",
    )
    owner = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_courses",
    )
    is_published = models.BooleanField(default=False)
    is_virtual = models.BooleanField(
        default=False,
        help_text="Virtual courses are teacher curriculum items and are hidden from the public course list.",
    )
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "title"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("academy:course-detail", kwargs={"slug": self.slug})

    @property
    def module_count(self):
        return self.modules.count()

    @property
    def lesson_count(self):
        return Lesson.objects.filter(module__course=self).count()


class CourseModule(models.Model):
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="modules",
    )
    unlocks_badge = models.ForeignKey(
        SkillBadge,
        on_delete=models.PROTECT,
        related_name="unlocking_modules",
    )
    owner = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_modules",
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200)
    description = models.TextField(blank=True)
    verbena_page = models.ForeignKey(
        "palimpsest.Page",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="academy_modules",
        help_text="Optional notes, article, or handout page for this module.",
    )
    exam = models.ForeignKey(
        "quizzes.Quiz",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exam_modules",
        limit_choices_to={"is_official": True},
        help_text="Official quiz used as the exam for this module.",
    )
    attached_quizzes = models.ManyToManyField(
        "quizzes.Quiz",
        related_name="academy_modules",
        blank=True,
        help_text="Standalone quizzes attached to this academy module.",
    )
    lecture_video_url = models.URLField(
        blank=True,
        help_text="Optional VOD link for the lecture video (YouTube, Vimeo, etc.).",
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "slug"],
                name="unique_module_slug_per_course",
            ),
            models.UniqueConstraint(
                fields=["course", "unlocks_badge"],
                name="unique_badge_unlock_per_course",
            ),
        ]

    def __str__(self):
        return f"{self.course.title} / {self.title}"


class Lesson(models.Model):
    module = models.ForeignKey(
        CourseModule,
        on_delete=models.CASCADE,
        related_name="lessons",
    )
    owner = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_lessons",
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200)
    summary = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    # Voice-narrated video lesson: either an uploaded Vault file or an external URL
    # (spec F-05 — "każda lekcja jako nagranie z narracją głosową").
    video_file = models.ForeignKey(
        "vault.VaultFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="academy_lessons",
        help_text="Optional video for this lesson (from Vault).",
    )
    # Downloadable PDF notes for the lesson (spec F-05 — "notatki w formie PDF do
    # ściągnięcia lub wydrukowania").
    notes_file = models.ForeignKey(
        "vault.VaultFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="academy_lesson_notes",
        help_text="Optional downloadable PDF notes for this lesson (from Vault).",
    )
    attached_quizzes = models.ManyToManyField(
        "quizzes.Quiz",
        related_name="academy_lessons",
        blank=True,
        help_text="Standalone quizzes attached to this academy lesson.",
    )

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["module", "slug"],
                name="unique_lesson_slug_per_module",
            ),
        ]

    def __str__(self):
        return f"{self.module.title} / {self.title}"


class Student(models.Model):
    person = models.OneToOneField(
        Person,
        on_delete=models.CASCADE,
        related_name="academy_student",
    )

    badges = models.ManyToManyField(
        SkillBadge,
        through="StudentBadge",
        related_name="students",
        blank=True,
    )

    enrolled_courses = models.ManyToManyField(
        Course,
        through="CourseEnrollment",
        related_name="students",
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return str(self.person)

    def has_badge(self, badge):
        return self.badges.filter(pk=badge.pk).exists()


class StudentBadge(models.Model):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="earned_badges",
    )
    badge = models.ForeignKey(
        SkillBadge,
        on_delete=models.CASCADE,
        related_name="earned_by_students",
    )

    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-awarded_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "badge"],
                name="unique_student_badge",
            ),
        ]

    def __str__(self):
        return f"{self.student} earned {self.badge}"


class CourseEnrollment(models.Model):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="course_enrollments",
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="course_enrollments",
    )

    enrolled_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-enrolled_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "course"],
                name="unique_student_course_enrollment",
            ),
        ]

    def __str__(self):
        return f"{self.student} -> {self.course}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if self.completed_at:
            Certificate.objects.get_or_create(
                person=self.student.person,
                course=self.course,
                defaults={
                    "granted_at": self.completed_at,
                },
            )


class Certificate(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    person = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="academy_certificates",
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="certificates",
    )
    exam = models.ForeignKey(
        "quizzes.Quiz",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="certificates",
        limit_choices_to={"is_official": True},
        help_text="Official exam quiz this certificate was awarded for.",
    )
    title = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    granted_at = models.DateTimeField(default=timezone.now)

    # Cryptographic signature by a teacher (professor).
    signed_by = models.ForeignKey(
        "academy.Teacher",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="signed_certificates",
        help_text="Teacher who cryptographically signed this certificate.",
    )
    signing_payload = models.TextField(blank=True)
    cryptographic_signature = models.TextField(blank=True)
    signing_key = models.ForeignKey(
        "gervazy.EncryptedPrivateKey",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="certificate_signatures",
    )

    class Meta:
        ordering = ["-granted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["person", "course"],
                condition=models.Q(course__isnull=False),
                name="unique_course_certificate_per_person",
            ),
        ]

    def __str__(self):
        return self.display_title

    @property
    def display_title(self):
        if self.title:
            return self.title

        if self.course:
            return self.course.title

        return "Certificate"


class Cohort(models.Model):
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="cohorts",
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cohorts",
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-starts_at", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "slug"],
                name="unique_cohort_slug_per_course",
            ),
        ]

    def __str__(self):
        return f"{self.course.title} / {self.title}"

    @property
    def member_count(self):
        return self.memberships.count()


class CohortMembership(models.Model):
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="cohort_memberships",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-joined_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["cohort", "student"],
                name="unique_student_cohort_membership",
            ),
        ]

    def __str__(self):
        return f"{self.student} / {self.cohort}"


class LearningPath(models.Model):
    title = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    badges = models.ManyToManyField(
        SkillBadge,
        through="LearningPathBadge",
        related_name="learning_paths",
        blank=True,
    )
    is_published = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "title"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("academy:path-detail", kwargs={"slug": self.slug})


class LearningPathBadge(models.Model):
    learning_path = models.ForeignKey(
        LearningPath,
        on_delete=models.CASCADE,
        related_name="badge_steps",
    )
    badge = models.ForeignKey(
        SkillBadge,
        on_delete=models.CASCADE,
        related_name="path_steps",
    )
    order = models.PositiveIntegerField(default=0)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["learning_path", "badge"],
                name="unique_badge_per_path",
            ),
        ]

    def __str__(self):
        return f"{self.learning_path.title} -> {self.badge.title}"


class PersonalPath(models.Model):
    """A student's personalized route to a skill goal.

    Steps are generated by gap analysis over the SkillBadge prerequisite DAG
    (see toto.academy.paths) and frozen at creation; completion status is
    re-synced against StudentBadge on every view.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        ARCHIVED = "archived", "Archived"

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="personal_paths",
    )
    title = models.CharField(max_length=200)
    goal_badge = models.ForeignKey(
        SkillBadge,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="targeted_by_personal_paths",
    )
    goal_group = models.ForeignKey(
        SkillGroup,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="targeted_by_personal_paths",
    )
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(goal_badge__isnull=False, goal_group__isnull=True)
                    | models.Q(goal_badge__isnull=True, goal_group__isnull=False)
                ),
                name="personal_path_exactly_one_goal",
            ),
            models.UniqueConstraint(
                fields=["student"],
                condition=models.Q(status="active"),
                name="unique_active_personal_path_per_student",
            ),
        ]

    def __str__(self):
        return f"{self.student} -> {self.title}"

    def get_absolute_url(self):
        return reverse("academy:personal-path-detail", kwargs={"pk": self.pk})

    @property
    def goal(self):
        return self.goal_badge or self.goal_group


class PersonalPathStep(models.Model):
    class StepType(models.TextChoices):
        MODULE = "module", "Course module"
        BADGE = "badge", "Earn skill badge"
        TASK = "task", "Task"

    path = models.ForeignKey(
        PersonalPath,
        on_delete=models.CASCADE,
        related_name="steps",
    )
    step_type = models.CharField(max_length=10, choices=StepType.choices)
    badge = models.ForeignKey(
        SkillBadge,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="personal_path_steps",
    )
    # SET_NULL so a deleted module degrades the step to plain badge behavior.
    module = models.ForeignKey(
        CourseModule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="personal_path_steps",
    )
    title = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["path", "badge"],
                condition=models.Q(badge__isnull=False),
                name="unique_badge_step_per_personal_path",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(step_type="task", badge__isnull=True)
                    | models.Q(step_type__in=["module", "badge"], badge__isnull=False)
                ),
                name="personal_path_step_type_badge_consistency",
            ),
        ]

    def __str__(self):
        return f"{self.path.title} / {self.display_title}"

    @property
    def display_title(self):
        if self.step_type == self.StepType.TASK:
            return self.title
        if self.step_type == self.StepType.MODULE and self.module:
            return self.module.title
        if self.badge:
            return f"Earn {self.badge.title}"
        return self.title

    @property
    def target_url(self):
        if self.step_type == self.StepType.MODULE and self.module:
            return self.module.course.get_absolute_url()
        if self.badge:
            return reverse(
                "academy:skill-tree-detail",
                kwargs={"slug": self.badge.group.slug},
            )
        return ""


class RecommendationConfig(models.Model):
    """Singleton (pk=1) tuning knobs for the hybrid badge recommender.

    cf_strength caps the collaborative share of the hybrid score; the
    automatic cold-start shrinkage still applies on top (effective weight =
    cf_strength * data confidence). Content sub-weights stay as constants in
    recommendations.py. Changing these fields needs no matrix rebuild — the
    similarity matrix stores similarities only; weights blend per request.
    """

    cf_strength = models.FloatField(
        default=0.4,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text=(
            "Maximum collaborative-filtering share of the hybrid score "
            "(0 = pure content-based)."
        ),
    )
    cold_start_students = models.PositiveIntegerField(
        default=20,
        help_text=(
            "Students with at least two badges needed for the collaborative "
            "share to reach full strength."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Recommendation configuration"

    def __str__(self):
        return "Recommendation configuration"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ────────────────────────────────────────────────
# SCRIPT  (instructional pages per module, multiple allowed)
# ────────────────────────────────────────────────

class Script(AbstractPage):
    """Instructional content page attached to a course module."""
    module = models.ForeignKey(
        CourseModule,
        on_delete=models.CASCADE,
        related_name="scripts",
    )
    author = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="academy_scripts",
    )

    class Meta:
        verbose_name = "Script"
        verbose_name_plural = "Scripts"
        ordering = ["title"]

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("academy:script-detail", args=[self.pk])


class ScriptSection(AbstractSection):
    page = models.ForeignKey(
        Script,
        on_delete=models.CASCADE,
        related_name="sections",
    )

    class Meta:
        ordering = ["order"]
        verbose_name = "Script Section"
        verbose_name_plural = "Script Sections"

    def __str__(self):
        return f"{self.page.title} – {self.title or 'Section'}"


# ────────────────────────────────────────────────
# WELCOME COPY  (editable, bilingual landing-page text — singleton)
# ────────────────────────────────────────────────

class WelcomeCopy(models.Model):
    """Editable, bilingual copy for the public welcome/landing page.

    Singleton by convention (pk forced to 1); use ``WelcomeCopy.current()``. Each
    field has a ``_pl`` and ``_en`` variant; the localized properties pick by the
    active language and fall back gracefully, so an empty row still renders
    sensible defaults (mirrors the RecommendationConfig singleton pattern).
    """

    headline_pl = models.CharField(max_length=200, blank=True)
    headline_en = models.CharField(max_length=200, blank=True)
    subtitle_pl = models.CharField(max_length=300, blank=True)
    subtitle_en = models.CharField(max_length=300, blank=True)
    invitation_pl = models.TextField(blank=True)
    invitation_en = models.TextField(blank=True)
    student_cta_pl = models.CharField(max_length=80, blank=True)
    student_cta_en = models.CharField(max_length=80, blank=True)
    teacher_cta_pl = models.CharField(max_length=80, blank=True)
    teacher_cta_en = models.CharField(max_length=80, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    _DEFAULTS = {
        "headline": gettext_lazy("Learn mathematics with Delta"),
        "subtitle": gettext_lazy("Guided courses, hands-on tasks and real feedback."),
        "invitation": gettext_lazy(
            "Delta is a maths e-learning platform. Explore courses, earn skill "
            "badges and track your progress — or sign in as a teacher to author "
            "lessons."),
        "student_cta": gettext_lazy("I’m a student"),
        "teacher_cta": gettext_lazy("I’m a teacher"),
    }

    class Meta:
        verbose_name = "Welcome page copy"
        verbose_name_plural = "Welcome page copy"

    def __str__(self):
        return "Welcome page copy"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def current(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def _localized(self, base):
        lang = (get_language() or "en")[:2]
        primary = getattr(self, f"{base}_pl" if lang == "pl" else f"{base}_en")
        fallback = getattr(self, f"{base}_en") or getattr(self, f"{base}_pl")
        return primary or fallback or self._DEFAULTS[base]

    @property
    def headline(self):
        return self._localized("headline")

    @property
    def subtitle(self):
        return self._localized("subtitle")

    @property
    def invitation(self):
        return self._localized("invitation")

    @property
    def student_cta(self):
        return self._localized("student_cta")

    @property
    def teacher_cta(self):
        return self._localized("teacher_cta")
