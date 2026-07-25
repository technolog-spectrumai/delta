from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from toto.competence.models import SkillBadge, SkillBadgePrerequisite, SkillGroup
from toto.core.models import Platform
from toto.people.models import Person

from .models import (
    Course,
    CourseModule,
    PersonalPath,
    PersonalPathStep,
    RecommendationConfig,
    Student,
    StudentBadge,
)
from .paths import compute_gap_badges, generate_steps, regenerate_path, sync_path
from .recommendations import (
    SIMILARITY_MATRIX_KEY,
    get_similarity_matrix,
    recommend_goals,
    refresh_similarity_matrix,
)
from .tasks import recompute_similarity_matrix


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


class AcademySmokeTests(TestCase):
    """End-to-end wiring checks: imports, registrations, URLs, empty-DB paths."""

    def test_celery_app_registers_recompute_task(self):
        from delta import celery_app
        import toto.academy.tasks  # noqa: F401 — import registers the task

        self.assertIn(
            "toto.academy.tasks.recompute_similarity_matrix",
            celery_app.tasks)

    def test_beat_schedule_references_registered_task(self):
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE[
            "academy-recompute-badge-similarity"]
        self.assertEqual(entry["task"], recompute_similarity_matrix.name)

    def test_personal_path_urls_reverse(self):
        reverse("academy:personal-path-list")
        reverse("academy:personal-path-create")
        reverse("academy:personal-path-detail", kwargs={"pk": 1})
        reverse("academy:personal-path-step-toggle",
                kwargs={"pk": 1, "step_pk": 1})
        reverse("academy:personal-path-regenerate", kwargs={"pk": 1})
        reverse("admin:academy_recommendationconfig_recalculate_matrix")

    def test_matrix_builds_on_empty_database(self):
        cache.clear()
        payload = refresh_similarity_matrix()

        self.assertEqual(payload["badge_ids"], [])
        self.assertEqual(payload["matrix"].shape, (0, 0))
        self.assertEqual(payload["n_pair_students"], 0)

    def test_recommendations_smoke_on_fresh_student(self):
        cache.clear()
        person = Person.objects.get_or_create(display_name="Smoke Student")[0]
        student = Student.objects.create(person=person)

        # No badges, no graph data at all — must not raise.
        self.assertEqual(recommend_goals(student), [])

        group = SkillGroup.objects.create(title="Smoke", slug="smoke", order=1)
        badge = SkillBadge.objects.create(
            group=group, title="Smoke badge", slug="smoke-badge", order=1)
        results = recommend_goals(student)

        self.assertEqual(results[0]["badge"], badge)
        self.assertEqual(RecommendationConfig.objects.count(), 1)


class PathFixtureMixin:
    """SkillGroup with badges A -> B -> C; published course unlocks A and B."""

    @classmethod
    def setUpTestData(cls):
        # PageProcessor 404s every decorated view without an active platform row
        # (seeded by init_platform in real deployments).
        Platform.objects.create(
            site_name="Test", author="tests", publication_year=2026)
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


class RecommendationFixtureMixin(PathFixtureMixin):
    """Path fixture plus a second skill group and peers for CF signal."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.physics = SkillGroup.objects.create(
            title="Physics", slug="physics", order=2)
        cls.badge_p1 = SkillBadge.objects.create(
            group=cls.physics, title="Mechanics", slug="mechanics", order=1)
        cls.badge_p2 = SkillBadge.objects.create(
            group=cls.physics, title="Optics", slug="optics", order=2)

        cls.peers = []
        for index in range(3):
            person = Person.objects.create(display_name=f"Peer {index}")
            cls.peers.append(Student.objects.create(person=person))

    def setUp(self):
        super().setUp()
        # The default LocMem cache persists across tests in-process; any test
        # touching the recommendation CF model must start from a clean cache.
        cache.clear()

    def rec_for(self, badge, **kwargs):
        for entry in recommend_goals(self.student, **kwargs):
            if entry["badge"] == badge:
                return entry
        return None

    def reason_kinds(self, entry):
        return {reason["kind"] for reason in entry["reasons"]}


class RecommendationTests(RecommendationFixtureMixin, TestCase):

    def test_earned_badges_are_excluded(self):
        StudentBadge.objects.create(student=self.student, badge=self.badge_a)
        self.assertIsNone(self.rec_for(self.badge_a))

    def test_active_path_coverage_is_excluded(self):
        path = self.make_path()  # goal C -> steps cover A, B, C
        generate_steps(path)

        recommended = {
            entry["badge"] for entry in recommend_goals(self.student)}
        self.assertEqual(recommended, {self.badge_p1, self.badge_p2})

    def test_readiness_fraction_of_transitive_prereqs(self):
        StudentBadge.objects.create(student=self.student, badge=self.badge_a)

        # B's only prerequisite (A) is earned; C is half-way (A of {A, B}).
        entry_b = self.rec_for(self.badge_b)
        entry_c = self.rec_for(self.badge_c)
        self.assertIn("ready", self.reason_kinds(entry_b))
        self.assertNotIn("ready", self.reason_kinds(entry_c))
        self.assertGreater(entry_b["score"], entry_c["score"])

        # No prerequisites at all -> immediately ready.
        self.assertIn(
            "ready", self.reason_kinds(self.rec_for(self.badge_p1)))

    def test_unlock_power_normalized_by_max(self):
        # A unlocks B and C (2 descendants, the max); score with no earned
        # badges is pure content: 0.5*readiness + 0.25*unlock + 0.25*0.
        entry_a = self.rec_for(self.badge_a)
        self.assertAlmostEqual(entry_a["score"], 0.75)

        entry_b = self.rec_for(self.badge_b)
        self.assertAlmostEqual(entry_b["score"], 0.125)

        unlocks_a = [r for r in entry_a["reasons"] if r["kind"] == "unlocks"]
        self.assertEqual(unlocks_a[0]["count"], 2)
        self.assertNotIn("unlocks", self.reason_kinds(self.rec_for(self.badge_c)))

    def test_continuation_proportional_and_zero_for_untouched_group(self):
        StudentBadge.objects.create(student=self.student, badge=self.badge_a)

        entry_b = self.rec_for(self.badge_b)
        self.assertIn("continues", self.reason_kinds(entry_b))

        entry_p1 = self.rec_for(self.badge_p1)
        self.assertNotIn("continues", self.reason_kinds(entry_p1))

        # Earned 1 of 3 maths badges: B = 0.5*1 + 0.25*0.5 + 0.25*(1/3)
        self.assertAlmostEqual(entry_b["score"], 0.5 + 0.125 + 1 / 12, places=4)

    def test_cf_lifts_cooccurring_badge(self):
        # Peers earned {A, P1}; the student earned A. P1 and P2 have
        # identical content metrics, so any gap comes from the CF term.
        for peer in self.peers:
            StudentBadge.objects.create(student=peer, badge=self.badge_a)
            StudentBadge.objects.create(student=peer, badge=self.badge_p1)
        StudentBadge.objects.create(student=self.student, badge=self.badge_a)

        entry_p1 = self.rec_for(self.badge_p1)
        entry_p2 = self.rec_for(self.badge_p2)
        self.assertGreater(entry_p1["score"], entry_p2["score"])

    def test_cf_weight_zero_without_earned_badges(self):
        for peer in self.peers:
            StudentBadge.objects.create(student=peer, badge=self.badge_a)
            StudentBadge.objects.create(student=peer, badge=self.badge_p1)

        # Nothing earned -> pure content score, no popularity claims.
        entry_a = self.rec_for(self.badge_a)
        self.assertAlmostEqual(entry_a["score"], 0.75)
        for entry in recommend_goals(self.student):
            self.assertNotIn("popular", self.reason_kinds(entry))

    def test_limit_and_score_ordering(self):
        entries = recommend_goals(self.student, limit=2)

        self.assertEqual(len(entries), 2)
        # A (0.75) then the ready no-prereq physics badge (0.5).
        self.assertEqual(entries[0]["badge"], self.badge_a)
        self.assertEqual(entries[1]["badge"], self.badge_p1)
        self.assertGreaterEqual(entries[0]["score"], entries[1]["score"])

    def test_matrix_stays_stale_until_recomputed(self):
        # The stored matrix deliberately ignores new awards until the next
        # beat run (or manual recompute) — no per-award invalidation.
        payload = get_similarity_matrix()
        self.assertEqual(payload["badge_ids"], [])

        StudentBadge.objects.create(student=self.student, badge=self.badge_a)

        payload = get_similarity_matrix()
        self.assertEqual(payload["badge_ids"], [])

        recompute_similarity_matrix()  # eager mode runs inline
        payload = get_similarity_matrix()
        self.assertEqual(payload["badge_ids"], [self.badge_a.pk])

    def test_cf_share_uses_config_knob(self):
        for peer in self.peers:
            StudentBadge.objects.create(student=peer, badge=self.badge_a)
            StudentBadge.objects.create(student=peer, badge=self.badge_p1)
        StudentBadge.objects.create(student=self.student, badge=self.badge_a)
        refresh_similarity_matrix()

        # Default knob 0.4, confidence 3/20 -> w_cf = 0.06.
        self.assertAlmostEqual(self.rec_for(self.badge_p1)["score"], 0.53)
        self.assertAlmostEqual(self.rec_for(self.badge_p2)["score"], 0.47)

        config = RecommendationConfig.load()
        config.cf_strength = 0.0
        config.save()
        self.assertAlmostEqual(self.rec_for(self.badge_p1)["score"], 0.5)
        self.assertAlmostEqual(self.rec_for(self.badge_p2)["score"], 0.5)

        config.cf_strength = 1.0
        config.cold_start_students = 3
        config.save()
        self.assertAlmostEqual(self.rec_for(self.badge_p1)["score"], 1.0)
        self.assertAlmostEqual(self.rec_for(self.badge_p2)["score"], 0.0)

    def test_popular_reason_gated_by_effective_weight(self):
        for peer in self.peers:
            StudentBadge.objects.create(student=peer, badge=self.badge_a)
            StudentBadge.objects.create(student=peer, badge=self.badge_p1)
        StudentBadge.objects.create(student=self.student, badge=self.badge_a)
        refresh_similarity_matrix()

        # Default w_cf = 0.06 < 0.15 -> CF too weak to claim popularity.
        entry = self.rec_for(self.badge_p1)
        self.assertNotIn("popular", self.reason_kinds(entry))

        config = RecommendationConfig.load()
        config.cold_start_students = 3  # w_cf = 0.4
        config.save()
        entry = self.rec_for(self.badge_p1)
        self.assertIn("popular", self.reason_kinds(entry))


class RecommendationViewTests(RecommendationFixtureMixin, TestCase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user_field = _person_user_field()
        if cls.user_field is None:
            return

        User = get_user_model()
        cls.user = User.objects.create_user(
            username="rec-student", password="secret")
        setattr(cls.person, cls.user_field, cls.user)
        cls.person.save(update_fields=[cls.user_field])

    def setUp(self):
        super().setUp()
        if self.user_field is None:
            self.skipTest(
                "Person model exposes no auth-user link in this environment")
        self.client.force_login(self.user)

    def test_list_page_shows_suggested_goals(self):
        response = self.client.get(reverse("academy:personal-path-list"))

        self.assertContains(response, "Suggested goals")
        badges = [
            entry["badge"] for entry in response.context["recommended_goals"]]
        self.assertIn(self.badge_a, badges)
        self.assertLessEqual(len(badges), 4)

    def test_create_page_shows_suggested_goals(self):
        response = self.client.get(reverse("academy:personal-path-create"))

        self.assertContains(response, "Suggested goals")
        self.assertTrue(response.context["recommended_goals"])

    def test_build_path_button_creates_path_and_archives_active(self):
        old = self.make_path(goal_badge=self.badge_c)

        response = self.client.post(
            reverse("academy:personal-path-create"),
            {"goal_badge": self.badge_p1.pk})

        new = PersonalPath.objects.get(
            student=self.student, goal_badge=self.badge_p1)
        self.assertRedirects(response, new.get_absolute_url())
        old.refresh_from_db()
        self.assertEqual(old.status, PersonalPath.Status.ARCHIVED)

    def test_section_hidden_when_no_candidates(self):
        for badge in (self.badge_a, self.badge_b, self.badge_c,
                      self.badge_p1, self.badge_p2):
            StudentBadge.objects.create(student=self.student, badge=badge)

        response = self.client.get(reverse("academy:personal-path-list"))

        self.assertNotContains(response, "Suggested goals")
        self.assertEqual(response.context["recommended_goals"], [])


class SimilarityMatrixTests(RecommendationFixtureMixin, TestCase):

    def test_matrix_values(self):
        for peer in self.peers:
            StudentBadge.objects.create(student=peer, badge=self.badge_a)
            StudentBadge.objects.create(student=peer, badge=self.badge_p1)

        payload = refresh_similarity_matrix()
        index = {b: i for i, b in enumerate(payload["badge_ids"])}
        matrix = payload["matrix"]

        # Perfect co-occurrence: 3/sqrt(3*3) = 1.0; zero diagonal.
        self.assertAlmostEqual(
            float(matrix[index[self.badge_a.pk], index[self.badge_p1.pk]]), 1.0)
        self.assertEqual(
            float(matrix[index[self.badge_a.pk], index[self.badge_a.pk]]), 0.0)

        # A fourth holder of A dilutes the cosine: 3/sqrt(3*4).
        StudentBadge.objects.create(student=self.student, badge=self.badge_a)
        payload = refresh_similarity_matrix()
        index = {b: i for i, b in enumerate(payload["badge_ids"])}
        self.assertAlmostEqual(
            float(payload["matrix"][index[self.badge_a.pk], index[self.badge_p1.pk]]),
            3 / (3 * 4) ** 0.5)

    def test_payload_schema_and_counts(self):
        for peer in self.peers:
            StudentBadge.objects.create(student=peer, badge=self.badge_a)
            StudentBadge.objects.create(student=peer, badge=self.badge_p1)
        StudentBadge.objects.create(student=self.student, badge=self.badge_b)

        payload = refresh_similarity_matrix()

        self.assertEqual(payload["n_students"], 4)
        self.assertEqual(payload["n_pair_students"], 3)
        self.assertEqual(payload["badge_ids"], sorted(payload["badge_ids"]))
        self.assertTrue(payload["computed_at"])
        self.assertEqual(
            payload["matrix"].shape,
            (len(payload["badge_ids"]), len(payload["badge_ids"])))

    def test_lazy_build_on_cache_miss(self):
        self.assertIsNone(cache.get(SIMILARITY_MATRIX_KEY))

        recommend_goals(self.student)

        self.assertIsNotNone(cache.get(SIMILARITY_MATRIX_KEY))

    def test_cache_round_trip(self):
        import numpy.testing

        for peer in self.peers:
            StudentBadge.objects.create(student=peer, badge=self.badge_a)
            StudentBadge.objects.create(student=peer, badge=self.badge_b)

        stored = refresh_similarity_matrix()
        loaded = get_similarity_matrix()

        numpy.testing.assert_allclose(loaded["matrix"], stored["matrix"])
        self.assertEqual(loaded["badge_ids"], stored["badge_ids"])

    def test_task_returns_summary(self):
        StudentBadge.objects.create(student=self.student, badge=self.badge_a)

        summary = recompute_similarity_matrix()

        self.assertEqual(summary["badges"], 1)
        self.assertEqual(summary["students"], 1)
        self.assertEqual(summary["pair_students"], 0)
        self.assertTrue(summary["computed_at"])


class RecommendationConfigTests(TestCase):

    def test_load_creates_singleton(self):
        config = RecommendationConfig.load()
        self.assertEqual(config.pk, 1)
        self.assertEqual(config.cf_strength, 0.4)
        self.assertEqual(config.cold_start_students, 20)
        self.assertEqual(RecommendationConfig.load().pk, config.pk)
        self.assertEqual(RecommendationConfig.objects.count(), 1)

    def test_save_forces_single_row(self):
        RecommendationConfig.load()
        RecommendationConfig(cf_strength=0.7).save()

        self.assertEqual(RecommendationConfig.objects.count(), 1)
        self.assertEqual(RecommendationConfig.load().cf_strength, 0.7)

    def test_cf_strength_bounds_validated(self):
        from django.core.exceptions import ValidationError

        for value in (1.5, -0.1):
            config = RecommendationConfig(cf_strength=value)
            with self.assertRaises(ValidationError):
                config.full_clean()


class RecommendationAdminTests(RecommendationFixtureMixin, TestCase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        User = get_user_model()
        cls.admin_user = User.objects.create_superuser(
            username="rec-admin", password="secret")

    def test_recalculate_endpoint_builds_matrix(self):
        StudentBadge.objects.create(student=self.student, badge=self.badge_a)
        self.client.force_login(self.admin_user)
        self.assertIsNone(cache.get(SIMILARITY_MATRIX_KEY))

        response = self.client.get(reverse(
            "admin:academy_recommendationconfig_recalculate_matrix"))

        self.assertRedirects(
            response,
            reverse("admin:academy_recommendationconfig_changelist"))
        self.assertIsNotNone(cache.get(SIMILARITY_MATRIX_KEY))

    def test_recalculate_endpoint_requires_staff(self):
        response = self.client.get(reverse(
            "admin:academy_recommendationconfig_recalculate_matrix"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login", response["Location"])
        self.assertIsNone(cache.get(SIMILARITY_MATRIX_KEY))

    def test_changelist_renders_with_config(self):
        RecommendationConfig.load()
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse(
            "admin:academy_recommendationconfig_changelist"))

        self.assertEqual(response.status_code, 200)


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
