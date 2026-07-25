import importlib

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from trix_editor.fields import TrixEditorField

from toto.core.models import Platform
from toto.people.models import Person

from .models import (
    QuestionProgress,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizAttemptAnswer,
    QuizQuestion,
)

SOLUTION_HTML = "<h2>Worked solution</h2><p><strong>Step 1:</strong> count the items.</p>"
PLAIN_PROBE = "Plain <b>text</b> hint"


def _person_user_field():
    """Name of the Person field pointing at the auth user, if any.

    Person lives in the installed toto base package; the user link is
    resolved dynamically so these tests don't depend on its field name.
    """
    User = get_user_model()
    for field in Person._meta.fields:
        if field.is_relation and field.related_model is User:
            return field.name
    return None


class QuizSolutionFixtureMixin:
    """Published quiz with one rich-solution question and one plain-only."""

    @classmethod
    def setUpTestData(cls):
        # PageProcessor 404s every decorated view without an active platform row
        # (seeded by init_platform in real deployments).
        Platform.objects.create(
            site_name="Test", author="tests", publication_year=2026)
        cls.quiz = Quiz.objects.create(
            title="Solutions quiz", slug="solutions-quiz", is_published=True)

        cls.question_rich = QuizQuestion.objects.create(
            quiz=cls.quiz, text="Rich question", order=1,
            explanation=PLAIN_PROBE, solution=SOLUTION_HTML)
        cls.correct_rich = QuizAnswer.objects.create(
            question=cls.question_rich, text="Right", is_correct=True,
            explanation="short answer note", order=1)
        cls.wrong_rich = QuizAnswer.objects.create(
            question=cls.question_rich, text="Wrong", is_correct=False, order=2)

        cls.question_plain = QuizQuestion.objects.create(
            quiz=cls.quiz, text="Plain question", order=2,
            explanation=PLAIN_PROBE)
        cls.correct_plain = QuizAnswer.objects.create(
            question=cls.question_plain, text="Right", is_correct=True, order=1)
        cls.wrong_plain = QuizAnswer.objects.create(
            question=cls.question_plain, text="Wrong", is_correct=False, order=2)

        cls.user_field = _person_user_field()
        if cls.user_field is None:
            return

        User = get_user_model()
        cls.user = User.objects.create_user(
            username="quiz-student", password="secret")
        cls.person = Person.objects.create(display_name="Quiz Student")
        setattr(cls.person, cls.user_field, cls.user)
        cls.person.save(update_fields=[cls.user_field])

    def setUp(self):
        if self.user_field is None:
            self.skipTest(
                "Person model exposes no auth-user link in this environment")
        self.client.force_login(self.user)

    def practice_post(self, question, answer):
        return self.client.post(
            reverse("quizzes:quiz-practice", kwargs={"pk": self.quiz.pk}),
            {"question_id": question.pk, "answer": answer.pk})


class QuizSolutionModelTests(TestCase):

    def test_solution_field_blank_ok(self):
        quiz = Quiz.objects.create(title="Q", slug="q")
        question = QuizQuestion.objects.create(quiz=quiz, text="T")

        question.full_clean()

        self.assertEqual(question.solution, "")
        field = QuizQuestion._meta.get_field("solution")
        self.assertIsInstance(field, TrixEditorField)

    def test_has_hint_property(self):
        quiz = Quiz.objects.create(title="H", slug="h")

        with_hint = QuizQuestion.objects.create(
            quiz=quiz, text="T1", hint="Try factoring first.")
        without_hint = QuizQuestion.objects.create(quiz=quiz, text="T2")
        whitespace_hint = QuizQuestion.objects.create(
            quiz=quiz, text="T3", hint="   \n  ")

        self.assertTrue(with_hint.has_hint)
        self.assertFalse(without_hint.has_hint)
        self.assertFalse(whitespace_hint.has_hint)


class QuizSolutionPracticeViewTests(QuizSolutionFixtureMixin, TestCase):

    def test_wrong_answer_shows_rich_solution(self):
        response = self.practice_post(self.question_rich, self.wrong_rich)

        self.assertContains(response, "<h2>Worked solution</h2>", html=False)
        self.assertContains(response, "Not quite")
        self.assertContains(response, self.correct_rich.text)

    def test_correct_answer_shows_rich_solution(self):
        response = self.practice_post(self.question_rich, self.correct_rich)

        self.assertContains(response, "<h2>Worked solution</h2>", html=False)
        self.assertContains(response, "Correct")

    def test_fallback_to_plain_explanation_without_solution(self):
        response = self.practice_post(self.question_plain, self.wrong_plain)

        self.assertNotContains(response, "Worked solution")
        # Plain explanation renders escaped, never as raw HTML.
        self.assertContains(response, "Plain &lt;b&gt;text&lt;/b&gt; hint")
        self.assertNotContains(response, PLAIN_PROBE, html=False)

    def test_plain_explanation_suppressed_when_solution_present(self):
        response = self.practice_post(self.question_rich, self.wrong_rich)

        # The rich card replaces the plain question explanation (per-answer
        # explanations stay), and the plain field never leaks unescaped.
        self.assertNotContains(response, "Plain &lt;b&gt;text&lt;/b&gt; hint")
        self.assertNotContains(response, PLAIN_PROBE, html=False)
        self.assertContains(response, "short answer note")


class QuizHintViewTests(QuizSolutionFixtureMixin, TestCase):
    HINT_TEXT = "Try factoring <first>"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.question_rich.hint = cls.HINT_TEXT
        cls.question_rich.save(update_fields=["hint"])

    def test_show_hint_button_and_escaped_hint_in_question_form(self):
        # First unsolved question is question_rich (order 1), which has a hint.
        response = self.client.get(
            reverse("quizzes:quiz-practice", kwargs={"pk": self.quiz.pk}))

        self.assertContains(response, "Show hint")
        self.assertContains(response, "Try factoring &lt;first&gt;")
        self.assertNotContains(response, self.HINT_TEXT, html=False)

    def test_no_hint_button_for_hintless_question(self):
        # Solve the hinted question so the pool serves question_plain next.
        progress, _ = QuestionProgress.objects.get_or_create(
            participant=self.person, question=self.question_rich)
        progress.record_attempt(True)

        response = self.client.get(
            reverse("quizzes:quiz-practice", kwargs={"pk": self.quiz.pk}))

        self.assertContains(response, self.question_plain.text)
        self.assertNotContains(response, "Show hint")

    def test_hint_absent_from_feedback_screen(self):
        # The hint belongs to the question form, not the answer feedback.
        response = self.practice_post(self.question_rich, self.wrong_rich)

        self.assertNotContains(response, "Show hint")


class QuizSolutionDetailViewTests(QuizSolutionFixtureMixin, TestCase):

    def test_graded_review_shows_solution_with_plain_fallback(self):
        attempt = QuizAttempt.objects.create(
            participant=self.person, quiz=self.quiz)
        QuizAttemptAnswer.objects.create(
            attempt=attempt, question=self.question_rich,
            answer=self.correct_rich)
        QuizAttemptAnswer.objects.create(
            attempt=attempt, question=self.question_plain,
            answer=self.wrong_plain)
        attempt.completed_at = timezone.now()
        attempt.save(update_fields=["completed_at"])

        response = self.client.get(
            reverse("quizzes:quiz-detail", kwargs={"pk": self.quiz.pk}))

        # Rich card for the question with a solution...
        self.assertContains(response, "<h2>Worked solution</h2>", html=False)
        # ...escaped lightbulb fallback for the plain-only question.
        self.assertContains(response, "Plain &lt;b&gt;text&lt;/b&gt; hint")
        self.assertNotContains(response, PLAIN_PROBE, html=False)


class SolutionBackfillHelperTests(TestCase):

    @staticmethod
    def _helper():
        module = importlib.import_module(
            "toto.quizzes.migrations.0003_backfill_question_solutions")
        return module.build_solution_html

    def test_composes_question_and_correct_answer_explanations(self):
        build = self._helper()

        html = build("Use the formula.", [("5x", "add the coefficients")])

        self.assertEqual(
            html,
            "<p>Use the formula.</p>"
            "<p><strong>5x:</strong> add the coefficients</p>")

    def test_escapes_promoted_plain_text(self):
        build = self._helper()

        html = build("<script>x</script> & more", [("a<b>", "1 < 2")])

        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&amp; more", html)
        self.assertIn("a&lt;b&gt;", html)
        self.assertIn("1 &lt; 2", html)

    def test_returns_empty_when_nothing_to_compose(self):
        build = self._helper()

        self.assertEqual(build("", []), "")
        self.assertEqual(build("   ", [("x", ""), ("y", "  ")]), "")
