"""Cross-feature integration tests.

These exercise the real HTTP views end-to-end and the seams between
features: enrollment -> practice pool -> worked solutions -> badges ->
personalized paths -> recommendations -> classroom dashboards. Unit
coverage lives in tests.py (academy) and toto/quizzes/tests.py.
"""
import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from toto.competence.models import SkillBadge, SkillBadgePrerequisite, SkillGroup
from toto.people.models import Person
from toto.quizzes.models import QuestionProgress, Quiz, QuizAnswer, QuizQuestion

from .models import (
    Cohort,
    CohortMembership,
    Course,
    CourseEnrollment,
    CourseModule,
    PersonalPath,
    RecommendationConfig,
    Student,
    StudentBadge,
    Teacher,
)
from .recommendations import SIMILARITY_MATRIX_KEY, refresh_similarity_matrix
from .tasks import recompute_similarity_matrix
from .tests import _person_user_field


class IntegrationFixtureMixin:
    """A small but complete world: skill DAG, course, quiz, people.

    Badges: algebra -> equations -> functions (chain). The course's two
    modules unlock algebra and equations; module one carries a published
    practice quiz whose first question has a rich solution and a hint.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user_field = _person_user_field()
        if cls.user_field is None:
            return

        cls.group = SkillGroup.objects.create(
            title="Integration Maths", slug="integration-maths", order=1)
        cls.badge_algebra = SkillBadge.objects.create(
            group=cls.group, title="Int Algebra", slug="int-algebra", order=1)
        cls.badge_equations = SkillBadge.objects.create(
            group=cls.group, title="Int Equations", slug="int-equations", order=2)
        cls.badge_functions = SkillBadge.objects.create(
            group=cls.group, title="Int Functions", slug="int-functions", order=3)
        SkillBadgePrerequisite.objects.create(
            badge=cls.badge_equations, prerequisite=cls.badge_algebra)
        SkillBadgePrerequisite.objects.create(
            badge=cls.badge_functions, prerequisite=cls.badge_equations)

        cls.course = Course.objects.create(
            title="Integration course", slug="integration-course",
            is_published=True, order=1)
        cls.module_algebra = CourseModule.objects.create(
            course=cls.course, unlocks_badge=cls.badge_algebra,
            title="Int algebra module", slug="int-algebra-module", order=1)
        CourseModule.objects.create(
            course=cls.course, unlocks_badge=cls.badge_equations,
            title="Int equations module", slug="int-equations-module", order=2)

        cls.quiz = Quiz.objects.create(
            title="Integration quiz", slug="integration-quiz",
            is_published=True)
        cls.module_algebra.attached_quizzes.add(cls.quiz)

        cls.questions = []
        for i in range(1, 3):
            question = QuizQuestion.objects.create(
                quiz=cls.quiz, text=f"Int Q{i}", order=i,
                solution="<h2>Integration solution</h2><p>Step by step.</p>"
                if i == 1 else "",
                hint="Think again." if i == 1 else "")
            QuizAnswer.objects.create(
                question=question, text="Right", is_correct=True, order=1)
            QuizAnswer.objects.create(
                question=question, text="Wrong", is_correct=False, order=2)
            cls.questions.append(question)

        User = get_user_model()
        cls.user = User.objects.create_user(
            username="int-student", password="x")
        cls.person = Person.objects.create(display_name="Int Student")
        setattr(cls.person, cls.user_field, cls.user)
        cls.person.save(update_fields=[cls.user_field])

    def setUp(self):
        if self.user_field is None:
            self.skipTest(
                "Person model exposes no auth-user link in this environment")
        cache.clear()
        self.client.force_login(self.user)

    # --- real-flow helpers ---------------------------------------------------

    def enroll(self):
        return self.client.post(reverse(
            "academy:course-enroll", kwargs={"slug": self.course.slug}))

    def answer(self, question, correct=True):
        chosen = question.answers.get(is_correct=correct)
        return self.client.post(
            reverse("quizzes:quiz-practice", kwargs={"pk": self.quiz.pk}),
            {"question_id": question.pk, "answer": chosen.pk})

    def student(self):
        return Student.objects.get(person=self.person)


class StudentJourneyIntegrationTests(IntegrationFixtureMixin, TestCase):
    """Enroll -> practice (solutions/hints) -> badge -> path auto-sync."""

    def test_enroll_practice_and_path_progress_flow(self):
        # 1. Enroll through the real endpoint (no subscription plans exist,
        #    so enrollment is open).
        response = self.enroll()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(CourseEnrollment.objects.filter(
            student__person=self.person, course=self.course).exists())

        # 2. The practice form offers the hint, and a wrong answer shows the
        #    rich worked solution while keeping the task in the pool.
        response = self.client.get(reverse(
            "quizzes:quiz-practice", kwargs={"pk": self.quiz.pk}))
        self.assertContains(response, "Show hint")

        response = self.answer(self.questions[0], correct=False)
        self.assertContains(response, "Integration solution")
        progress = QuestionProgress.objects.get(
            participant=self.person, question=self.questions[0])
        self.assertFalse(progress.is_solved)

        # 3. Correct answers clear the pool and also show the solution.
        response = self.answer(self.questions[0], correct=True)
        self.assertContains(response, "Integration solution")
        response = self.answer(self.questions[1], correct=True)
        self.assertEqual(QuestionProgress.objects.filter(
            participant=self.person, is_solved=True).count(), 2)

        # 4. Build a personalized path to the top of the badge chain via the
        #    real create endpoint: steps algebra -> equations -> functions.
        response = self.client.post(
            reverse("academy:personal-path-create"),
            {"goal_badge": self.badge_functions.pk})
        path = PersonalPath.objects.get(student=self.student())
        self.assertRedirects(response, path.get_absolute_url())
        self.assertEqual(
            [step.badge for step in path.steps.order_by("order")],
            [self.badge_algebra, self.badge_equations, self.badge_functions])

        # 5. Awarding the algebra badge (admin flow today) auto-completes its
        #    step on the next path view — read-time sync across features.
        StudentBadge.objects.create(
            student=self.student(), badge=self.badge_algebra)
        response = self.client.get(path.get_absolute_url())
        self.assertEqual(response.context["progress"]["done"], 1)
        step = path.steps.get(badge=self.badge_algebra)
        self.assertTrue(step.is_completed)

        # 6. Earning everything completes the path.
        StudentBadge.objects.create(
            student=self.student(), badge=self.badge_equations)
        StudentBadge.objects.create(
            student=self.student(), badge=self.badge_functions)
        self.client.get(path.get_absolute_url())
        path.refresh_from_db()
        self.assertEqual(path.status, PersonalPath.Status.COMPLETED)


class RecommendationPipelineIntegrationTests(IntegrationFixtureMixin, TestCase):
    """Peers' badges -> celery task -> cached matrix -> suggestions -> path."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        if cls.user_field is None:
            return
        cls.peers = []
        for i in range(3):
            person = Person.objects.create(display_name=f"Int Peer {i}")
            student = Student.objects.create(person=person)
            StudentBadge.objects.create(student=student, badge=cls.badge_algebra)
            StudentBadge.objects.create(student=student, badge=cls.badge_equations)
            cls.peers.append(student)

    def test_matrix_task_feeds_view_suggestions_and_one_click_path(self):
        StudentBadge.objects.create(
            student=Student.objects.create(person=self.person),
            badge=self.badge_algebra)

        # The (eager) celery task populates the cache the views read from.
        summary = recompute_similarity_matrix()
        self.assertGreaterEqual(summary["pair_students"], 3)
        self.assertIsNotNone(cache.get(SIMILARITY_MATRIX_KEY))

        response = self.client.get(reverse("academy:personal-path-list"))
        recommended = [
            entry["badge"] for entry in response.context["recommended_goals"]]
        self.assertIn(self.badge_equations, recommended)
        self.assertNotIn(self.badge_algebra, recommended)  # already earned

        # One-click "Build path" from a suggestion.
        response = self.client.post(
            reverse("academy:personal-path-create"),
            {"goal_badge": self.badge_equations.pk})
        path = PersonalPath.objects.get(
            student__person=self.person, goal_badge=self.badge_equations)
        self.assertRedirects(response, path.get_absolute_url())

        # Suggestions on the list page now exclude the active path's badges.
        response = self.client.get(reverse("academy:personal-path-list"))
        recommended = [
            entry["badge"] for entry in response.context["recommended_goals"]]
        self.assertNotIn(self.badge_equations, recommended)

    def test_admin_knob_changes_ranking_end_to_end(self):
        StudentBadge.objects.create(
            student=Student.objects.create(person=self.person),
            badge=self.badge_algebra)
        refresh_similarity_matrix()

        config = RecommendationConfig.load()
        config.cf_strength = 0.0
        config.save()
        response = self.client.get(reverse("academy:personal-path-list"))
        content_only = [
            entry["badge"] for entry in response.context["recommended_goals"]]

        config.cf_strength = 1.0
        config.cold_start_students = 1
        config.save()
        response = self.client.get(reverse("academy:personal-path-list"))
        cf_heavy = response.context["recommended_goals"]

        # With full CF weight, the peers' co-occurring badge must rank first.
        self.assertEqual(cf_heavy[0]["badge"], self.badge_equations)
        self.assertTrue(content_only)  # both configurations produce results


class ClassroomIntegrationTests(IntegrationFixtureMixin, TestCase):
    """Student activity through real views shows up on the teacher dashboard."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        if cls.user_field is None:
            return

        User = get_user_model()
        cls.teacher_user = User.objects.create_user(
            username="int-teacher", password="x")
        teacher_person = Person.objects.create(display_name="Int Teacher")
        setattr(teacher_person, cls.user_field, cls.teacher_user)
        teacher_person.save(update_fields=[cls.user_field])
        cls.teacher = Teacher.objects.create(person=teacher_person)

        cls.cohort = Cohort.objects.create(
            course=cls.course, teacher=cls.teacher,
            title="Int Class", slug="int-class")

    def test_student_activity_reaches_teacher_dashboard(self):
        # Student acts through the real endpoints...
        student = Student.objects.create(person=self.person)
        CohortMembership.objects.create(cohort=self.cohort, student=student)
        self.enroll()
        self.answer(self.questions[0], correct=True)
        StudentBadge.objects.create(student=student, badge=self.badge_algebra)

        # ...and the teacher sees exactly that on the dashboard.
        self.client.force_login(self.teacher_user)
        response = self.client.get(reverse(
            "academy:classroom-dashboard", kwargs={"pk": self.cohort.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["member_count"], 1)
        self.assertEqual(response.context["enrolled_count"], 1)
        row = response.context["member_rows"][0]
        self.assertEqual(row["solved"], 1)
        self.assertEqual(row["badge_count"], 1)
        self.assertIsNotNone(row["last_activity"])

        activity = json.loads(response.context["activity_chart_json"])
        self.assertEqual(activity["datasets"][0]["data"][-1], 1)  # solved now
        self.assertEqual(activity["datasets"][1]["data"][-1], 1)  # badge now

        funnel = json.loads(response.context["funnel_chart_json"])
        self.assertEqual(funnel["datasets"][0]["data"], [1, 1, 0])

    def test_dashboard_reflects_empty_start_then_growth(self):
        self.client.force_login(self.teacher_user)
        url = reverse(
            "academy:classroom-dashboard", kwargs={"pk": self.cohort.pk})

        response = self.client.get(url)
        self.assertEqual(response.context["member_count"], 0)
        self.assertEqual(response.context["average_pct"], 0)

        student = Student.objects.create(person=self.person)
        CohortMembership.objects.create(cohort=self.cohort, student=student)
        response = self.client.get(url)
        self.assertEqual(response.context["member_count"], 1)
