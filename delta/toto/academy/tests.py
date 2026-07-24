from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from toto.competence.models import SkillBadge, SkillBadgePrerequisite, SkillGroup
from toto.people.models import Person

from .models import (
    Course,
    CourseModule,
    PersonalPath,
    PersonalPathStep,
    Student,
    StudentBadge,
)
from .paths import compute_gap_badges, generate_steps, regenerate_path, sync_path


def _person_user_field():
    """Name of the Person field pointing at the auth user, if any.

    The Person model lives in the installed toto base package; the user link
    is resolved dynamically so these tests don't depend on its field name.
    """
    User = get_user_model()
    for field in Person._meta.fields:
        if field.is_relation and field.related_model is User:
            return field.name
    return None


class PathFixtureMixin:
    """SkillGroup with badges A -> B -> C; published course unlocks A and B."""

    @classmethod
    def setUpTestData(cls):
        cls.group = SkillGroup.objects.create(
            title="Maths", slug="maths", order=1)
        cls.badge_a = SkillBadge.objects.create(
            group=cls.group, title="Algebra", slug="algebra", order=1)
        cls.badge_b = SkillBadge.objects.create(
            group=cls.group, title="Equations", slug="equations", order=2)
        cls.badge_c = SkillBadge.objects.create(
            group=cls.group, title="Functions", slug="functions", order=3)
        SkillBadgePrerequisite.objects.create(
            badge=cls.badge_b, prerequisite=cls.badge_a)
        SkillBadgePrerequisite.objects.create(
            badge=cls.badge_c, prerequisite=cls.badge_b)

        cls.course = Course.objects.create(
            title="Maths course", slug="maths-course",
            is_published=True, order=1)
        cls.module_a = CourseModule.objects.create(
            course=cls.course, unlocks_badge=cls.badge_a,
            title="Algebra module", slug="algebra-module", order=1)
        cls.module_b = CourseModule.objects.create(
            course=cls.course, unlocks_badge=cls.badge_b,
            title="Equations module", slug="equations-module", order=2)

        cls.person = Person.objects.get_or_create(
            display_name="Test Student")[0]
        cls.student = Student.objects.create(person=cls.person)

    def make_path(self, **kwargs):
        kwargs.setdefault("student", self.student)
        kwargs.setdefault("goal_badge", self.badge_c)
        kwargs.setdefault("title", "Path to Functions")
        return PersonalPath.objects.create(**kwargs)


class PathGenerationTests(PathFixtureMixin, TestCase):

    def test_gap_is_topologically_ordered(self):
        gap = compute_gap_badges(self.student, goal_badge=self.badge_c)
        self.assertEqual(gap, [self.badge_a, self.badge_b, self.badge_c])

    def test_earned_badges_leave_the_gap(self):
        StudentBadge.objects.create(student=self.student, badge=self.badge_a)
        gap = compute_gap_badges(self.student, goal_badge=self.badge_c)
        self.assertEqual(gap, [self.badge_b, self.badge_c])

    def test_group_goal_covers_all_badges(self):
        gap = compute_gap_badges(self.student, goal_group=self.group)
        self.assertEqual(gap, [self.badge_a, self.badge_b, self.badge_c])

    def test_steps_link_unlocking_modules(self):
        path = self.make_path()
        steps = generate_steps(path)

        self.assertEqual(
            [step.badge for step in steps],
            [self.badge_a, self.badge_b, self.badge_c])
        self.assertEqual(
            [step.step_type for step in steps],
            [PersonalPathStep.StepType.MODULE,
             PersonalPathStep.StepType.MODULE,
             PersonalPathStep.StepType.BADGE])
        self.assertEqual(steps[0].module, self.module_a)
        self.assertEqual(steps[1].module, self.module_b)
        self.assertIsNone(steps[2].module)

    def test_unpublished_course_module_is_not_linked(self):
        self.course.is_published = False
        self.course.save(update_fields=["is_published"])

        path = self.make_path()
        steps = generate_steps(path)

        self.assertTrue(all(step.module is None for step in steps))
        self.assertTrue(all(
            step.step_type == PersonalPathStep.StepType.BADGE
            for step in steps))

    def test_tie_break_uses_curriculum_order(self):
        # Two independent prerequisites of a new goal, at the same depth.
        early = SkillBadge.objects.create(
            group=self.group, title="Zeta early", slug="zeta-early", order=0)
        goal = SkillBadge.objects.create(
            group=self.group, title="Goal", slug="goal", order=9)
        SkillBadgePrerequisite.objects.create(badge=goal, prerequisite=early)
        SkillBadgePrerequisite.objects.create(
            badge=goal, prerequisite=self.badge_a)

        gap = compute_gap_badges(self.student, goal_badge=goal)
        self.assertEqual(gap, [early, self.badge_a, goal])

    def test_prerequisite_cycle_does_not_break_generation(self):
        # The DB only forbids self-loops; a longer cycle must still generate.
        SkillBadgePrerequisite.objects.create(
            badge=self.badge_a, prerequisite=self.badge_c)

        gap = compute_gap_badges(self.student, goal_badge=self.badge_c)
        self.assertCountEqual(
            gap, [self.badge_a, self.badge_b, self.badge_c])


class PathSyncTests(PathFixtureMixin, TestCase):

    def test_badge_award_completes_step_with_award_date(self):
        path = self.make_path()
        generate_steps(path)
        award = StudentBadge.objects.create(
            student=self.student, badge=self.badge_a)

        progress = sync_path(path)

        step = path.steps.get(badge=self.badge_a)
        self.assertTrue(step.is_completed)
        self.assertEqual(step.completed_at, award.awarded_at)
        self.assertEqual(progress["done"], 1)
        self.assertEqual(progress["total"], 3)

    def test_all_steps_done_completes_the_path(self):
        path = self.make_path()
        generate_steps(path)
        for badge in (self.badge_a, self.badge_b, self.badge_c):
            StudentBadge.objects.create(student=self.student, badge=badge)

        progress = sync_path(path)

        path.refresh_from_db()
        self.assertEqual(path.status, PersonalPath.Status.COMPLETED)
        self.assertIsNotNone(path.completed_at)
        self.assertEqual(progress["percent"], 100)

    def test_task_added_after_completion_reactivates_path(self):
        path = self.make_path()
        generate_steps(path)
        for badge in (self.badge_a, self.badge_b, self.badge_c):
            StudentBadge.objects.create(student=self.student, badge=badge)
        sync_path(path)

        PersonalPathStep.objects.create(
            path=path, step_type=PersonalPathStep.StepType.TASK,
            title="Review notes", order=100)
        sync_path(path)

        path.refresh_from_db()
        self.assertEqual(path.status, PersonalPath.Status.ACTIVE)
        self.assertIsNone(path.completed_at)


class PathRegenerateTests(PathFixtureMixin, TestCase):

    def test_regenerate_keeps_completed_and_task_steps(self):
        path = self.make_path()
        generate_steps(path)
        StudentBadge.objects.create(student=self.student, badge=self.badge_a)
        sync_path(path)
        task = PersonalPathStep.objects.create(
            path=path, step_type=PersonalPathStep.StepType.TASK,
            title="Extra practice", order=100)

        regenerate_path(path)

        steps = list(path.steps.order_by("order", "id"))
        badges = [step.badge for step in steps if step.badge_id]
        self.assertIn(self.badge_a, badges)  # completed step kept
        self.assertIn(task, steps)
        self.assertEqual(len(badges), len(set(badges)))  # no duplicates
        self.assertCountEqual(
            badges, [self.badge_a, self.badge_b, self.badge_c])


class PathConstraintTests(PathFixtureMixin, TestCase):

    def test_single_active_path_per_student(self):
        self.make_path()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_path(goal_badge=self.badge_b, title="Second path")

    def test_archived_path_frees_the_active_slot(self):
        self.make_path(status=PersonalPath.Status.ARCHIVED)
        self.make_path()  # no IntegrityError

    def test_exactly_one_goal_required(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PersonalPath.objects.create(
                student=self.student, title="Goal-less", goal_badge=None,
                goal_group=None)


class PersonalPathViewTests(PathFixtureMixin, TestCase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user_field = _person_user_field()
        if cls.user_field is None:
            return

        User = get_user_model()
        cls.user = User.objects.create_user(
            username="student", password="secret")
        setattr(cls.person, cls.user_field, cls.user)
        cls.person.save(update_fields=[cls.user_field])

        cls.other_user = User.objects.create_user(
            username="other", password="secret")
        cls.other_person = Person.objects.create(display_name="Other Student")
        setattr(cls.other_person, cls.user_field, cls.other_user)
        cls.other_person.save(update_fields=[cls.user_field])
        cls.other_student = Student.objects.create(person=cls.other_person)

    def setUp(self):
        if self.user_field is None:
            self.skipTest(
                "Person model exposes no auth-user link in this environment")
        self.client.force_login(self.user)

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("academy:personal-path-list"))
        self.assertEqual(response.status_code, 302)

    def test_create_builds_path_and_steps(self):
        response = self.client.post(
            reverse("academy:personal-path-create"),
            {"goal_badge": self.badge_c.pk})

        path = PersonalPath.objects.get(student=self.student)
        self.assertRedirects(response, path.get_absolute_url())
        self.assertEqual(path.steps.count(), 3)

    def test_create_archives_previous_active_path(self):
        old = self.make_path()
        self.client.post(
            reverse("academy:personal-path-create"),
            {"goal_group": self.group.pk})

        old.refresh_from_db()
        self.assertEqual(old.status, PersonalPath.Status.ARCHIVED)
        self.assertTrue(
            PersonalPath.objects.filter(
                student=self.student, goal_group=self.group,
                status=PersonalPath.Status.ACTIVE).exists())

    def test_create_rejects_double_goal(self):
        response = self.client.post(
            reverse("academy:personal-path-create"),
            {"goal_badge": self.badge_c.pk, "goal_group": self.group.pk})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalPath.objects.exists())

    def test_detail_is_owner_only(self):
        other_path = PersonalPath.objects.create(
            student=self.other_student, goal_badge=self.badge_c,
            title="Other's path")

        response = self.client.get(other_path.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_toggle_task_step(self):
        path = self.make_path()
        task = PersonalPathStep.objects.create(
            path=path, step_type=PersonalPathStep.StepType.TASK,
            title="Practice", order=10)

        self.client.post(reverse(
            "academy:personal-path-step-toggle",
            kwargs={"pk": path.pk, "step_pk": task.pk}))

        task.refresh_from_db()
        self.assertTrue(task.is_completed)
        self.assertIsNotNone(task.completed_at)

    def test_toggle_rejected_for_badge_step(self):
        path = self.make_path()
        generate_steps(path)
        badge_step = path.steps.get(
            step_type=PersonalPathStep.StepType.BADGE)

        self.client.post(reverse(
            "academy:personal-path-step-toggle",
            kwargs={"pk": path.pk, "step_pk": badge_step.pk}))

        badge_step.refresh_from_db()
        self.assertFalse(badge_step.is_completed)

    def test_task_add_and_delete(self):
        path = self.make_path()
        self.client.post(
            reverse("academy:personal-path-task-add",
                    kwargs={"pk": path.pk}),
            {"title": "Read the script", "note": ""})

        task = path.steps.get(step_type=PersonalPathStep.StepType.TASK)
        self.assertEqual(task.title, "Read the script")

        self.client.post(reverse(
            "academy:personal-path-step-delete",
            kwargs={"pk": path.pk, "step_pk": task.pk}))
        self.assertFalse(path.steps.filter(pk=task.pk).exists())

    def test_archive(self):
        path = self.make_path()
        response = self.client.post(reverse(
            "academy:personal-path-archive", kwargs={"pk": path.pk}))

        self.assertRedirects(
            response, reverse("academy:personal-path-list"))
        path.refresh_from_db()
        self.assertEqual(path.status, PersonalPath.Status.ARCHIVED)
