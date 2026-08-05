import importlib
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone


from toto.core.models import Platform
from toto.people.models import Person

from .models import (
    QuestionProgress,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizAttemptAnswer,
    QuizQuestion,
    QuizTrait,
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
        # A plain TextField since the editor moved to cyprian: the editing
        # surface is the form's business, so swapping editors must never be a
        # migration. What the column owes is "blank HTML is fine".
        field = QuizQuestion._meta.get_field("solution")
        self.assertIsInstance(field, models.TextField)
        self.assertTrue(field.blank)

    def test_the_solution_form_field_sanitises(self):
        # The rich field is where teacher HTML is scrubbed — it never was
        # before, when the field was Trix and the value was stored raw.
        from toto.quizzes.forms import QuestionForm

        field = QuestionForm().fields["solution"]
        self.assertEqual(
            field.clean("<p>keep<script>alert(1)</script></p>"),
            field.clean("<p>keep</p>"),
        )
        self.assertNotIn("script", field.clean("<p>x<script>y</script></p>").lower())

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


# ---------------------------------------------------------------------------
# Teacher back-office quiz editor
# ---------------------------------------------------------------------------

class QuizBackofficeMixin:
    """A logged-in staff (=back-office) user and an empty quiz to author into."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(
            site_name="Test", author="tests", publication_year=2026)
        User = get_user_model()
        cls.staff = User.objects.create_user(
            "bo-editor", password="secret", is_staff=True)
        cls.quiz = Quiz.objects.create(title="Pool", slug="pool")

    def setUp(self):
        self.client.force_login(self.staff)

    def _payload(self, **over):
        """A two-option single-choice question POST payload (formset included)."""
        data = {
            "text": "Question?",
            "answer_format": "single",
            "solution": "", "hint": "", "explanation": "",
            "max_time": "", "order": "1",
            "answers-TOTAL_FORMS": "2",
            "answers-INITIAL_FORMS": "0",
            "answers-MIN_NUM_FORMS": "0",
            "answers-MAX_NUM_FORMS": "1000",
            "answers-0-text": "A", "answers-0-order": "1", "answers-0-traits_data": "",
            "answers-1-text": "B", "answers-1-order": "2", "answers-1-traits_data": "",
        }
        data.update(over)
        return data

    def _create(self, **over):
        return self.client.post(
            reverse("backoffice_quizzes:question-create", kwargs={"pk": self.quiz.pk}),
            self._payload(**over))


class QuizCrudViewTests(QuizBackofficeMixin, TestCase):

    def test_create_quiz_autoslugs_and_redirects(self):
        response = self.client.post(
            reverse("backoffice_quizzes:quiz-create"),
            {"title": "Klasa 1", "slug": "", "description": "", "order": "0"})
        quiz = Quiz.objects.get(title="Klasa 1")
        self.assertRedirects(
            response,
            reverse("backoffice_quizzes:question-list", kwargs={"pk": quiz.pk}))
        self.assertTrue(quiz.slug)

    def test_duplicate_slug_is_made_unique(self):
        Quiz.objects.create(title="Dup", slug="dup")
        self.client.post(reverse("backoffice_quizzes:quiz-create"),
                         {"title": "Dup", "slug": "dup", "description": "", "order": "0"})
        slugs = set(Quiz.objects.filter(title="Dup").values_list("slug", flat=True))
        # Two quizzes now share the title but must have distinct slugs.
        self.assertEqual(len(slugs), 2)
        self.assertIn("dup", slugs)

    def test_edit_quiz(self):
        response = self.client.post(
            reverse("backoffice_quizzes:quiz-edit", kwargs={"pk": self.quiz.pk}),
            {"title": "Renamed", "slug": self.quiz.slug, "description": "",
             "order": "3", "is_published": "on"})
        self.assertEqual(response.status_code, 302)
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.title, "Renamed")
        self.assertTrue(self.quiz.is_published)

    def test_delete_quiz(self):
        doomed = Quiz.objects.create(title="Doomed", slug="doomed")
        response = self.client.post(
            reverse("backoffice_quizzes:quiz-delete", kwargs={"pk": doomed.pk}))
        self.assertRedirects(response, reverse("backoffice_quizzes:quiz-list"))
        self.assertFalse(Quiz.objects.filter(pk=doomed.pk).exists())

    def test_student_cannot_reach_editor(self):
        User = get_user_model()
        student = User.objects.create_user("plain-student", password="x")
        self.client.force_login(student)
        response = self.client.get(reverse("backoffice_quizzes:quiz-list"))
        self.assertEqual(response.status_code, 404)


class QuestionEditorTests(QuizBackofficeMixin, TestCase):

    def test_single_choice_creates_choice_question(self):
        self._create(answer_format="single", **{"answers-0-is_correct": "on"})
        question = self.quiz.questions.get()
        self.assertEqual(question.question_type, QuizQuestion.TYPE_CHOICE)
        self.assertFalse(question.is_multiple_choice)
        self.assertEqual(question.answers.count(), 2)
        self.assertEqual(
            set(question.answers.filter(is_correct=True).values_list("text", flat=True)),
            {"A"})

    def test_multiple_choice_sets_flag(self):
        self._create(answer_format="multiple",
                     **{"answers-0-is_correct": "on", "answers-1-is_correct": "on"})
        question = self.quiz.questions.get()
        self.assertEqual(question.question_type, QuizQuestion.TYPE_CHOICE)
        self.assertTrue(question.is_multiple_choice)
        self.assertEqual(question.answers.filter(is_correct=True).count(), 2)

    def test_open_forces_accepted_answers(self):
        self._create(answer_format="open")  # no is_correct posted
        question = self.quiz.questions.get()
        self.assertEqual(question.question_type, QuizQuestion.TYPE_OPEN)
        self.assertFalse(question.is_multiple_choice)
        self.assertEqual(question.answers.filter(is_correct=True).count(), 2)

    def test_single_requires_exactly_one_correct(self):
        response = self._create(answer_format="single")  # zero correct
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.quiz.questions.count(), 0)

    def test_open_requires_at_least_one_answer(self):
        response = self._create(answer_format="open",
                                **{"answers-0-text": "", "answers-1-text": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.quiz.questions.count(), 0)

    def test_choice_requires_two_options(self):
        response = self._create(answer_format="single",
                                **{"answers-0-is_correct": "on", "answers-1-text": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.quiz.questions.count(), 0)

    def test_edit_switches_format_and_correctness(self):
        question = QuizQuestion.objects.create(
            quiz=self.quiz, text="orig", question_type=QuizQuestion.TYPE_CHOICE,
            is_multiple_choice=False, order=1)
        a1 = QuizAnswer.objects.create(question=question, text="A", is_correct=True, order=1)
        a2 = QuizAnswer.objects.create(question=question, text="B", order=2)
        data = self._payload(
            text="edited", answer_format="multiple",
            **{
                "answers-INITIAL_FORMS": "2",
                "answers-0-id": str(a1.pk), "answers-0-is_correct": "on",
                "answers-1-id": str(a2.pk), "answers-1-is_correct": "on",
            })
        response = self.client.post(
            reverse("backoffice_quizzes:question-edit", kwargs={"pk": question.pk}), data)
        self.assertEqual(response.status_code, 302)
        question.refresh_from_db()
        self.assertEqual(question.text, "edited")
        self.assertTrue(question.is_multiple_choice)
        self.assertEqual(question.answers.filter(is_correct=True).count(), 2)

    def test_delete_question(self):
        question = QuizQuestion.objects.create(quiz=self.quiz, text="del", order=1)
        response = self.client.post(
            reverse("backoffice_quizzes:question-delete", kwargs={"pk": question.pk}))
        self.assertRedirects(
            response,
            reverse("backoffice_quizzes:question-list", kwargs={"pk": self.quiz.pk}))
        self.assertFalse(QuizQuestion.objects.filter(pk=question.pk).exists())

    def test_reorder_swaps_adjacent(self):
        q1 = QuizQuestion.objects.create(quiz=self.quiz, text="q1", order=1)
        q2 = QuizQuestion.objects.create(quiz=self.quiz, text="q2", order=2)
        self.client.post(
            reverse("backoffice_quizzes:question-reorder", kwargs={"pk": self.quiz.pk}),
            {"question": q1.pk, "direction": "down"})
        q1.refresh_from_db()
        q2.refresh_from_db()
        self.assertEqual(q1.order, 2)
        self.assertEqual(q2.order, 1)

    def test_reorder_get_is_405(self):
        response = self.client.get(
            reverse("backoffice_quizzes:question-reorder", kwargs={"pk": self.quiz.pk}))
        self.assertEqual(response.status_code, 405)

    def test_advanced_traits_create_mappings(self):
        trait = QuizTrait.objects.create(title="Pragmatist", slug="pragmatist")
        self._create(
            answer_format="single",
            **{
                "answers-0-is_correct": "on",
                "answers-0-traits_data": json.dumps([{"trait": trait.id, "weight": 3}]),
            })
        answer = self.quiz.questions.get().answers.get(text="A")
        self.assertEqual(answer.trait_mappings.count(), 1)
        mapping = answer.trait_mappings.get()
        self.assertEqual(mapping.trait_id, trait.id)
        self.assertEqual(mapping.weight, 3)


class QuizMathRenderingTests(TestCase):
    """KaTeX scaffolding is present and the escaping contract is preserved."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(
            site_name="Test", author="tests", publication_year=2026)
        cls.quiz = Quiz.objects.create(title="Math", slug="math", is_published=True)
        cls.question = QuizQuestion.objects.create(
            quiz=cls.quiz, text="Solve $x^2$ and <b>bold</b>", order=1)
        QuizAnswer.objects.create(
            question=cls.question, text="$x=2$", is_correct=True, order=1)
        cls.user_field = _person_user_field()
        if cls.user_field is None:
            return
        User = get_user_model()
        cls.user = User.objects.create_user("math-student", password="x")
        cls.person = Person.objects.create(display_name="Math Student")
        setattr(cls.person, cls.user_field, cls.user)
        cls.person.save(update_fields=[cls.user_field])

    def setUp(self):
        if self.user_field is None:
            self.skipTest("Person model exposes no auth-user link")
        self.client.force_login(self.user)

    def test_practice_page_loads_katex_and_escapes_stem(self):
        response = self.client.get(
            reverse("quizzes:quiz-practice", kwargs={"pk": self.quiz.pk}))
        self.assertContains(response, "katex")
        self.assertContains(response, "js-math")
        # $…$ delimiters survive as literal text for KaTeX to pick up client-side,
        # and raw HTML in the stem is still escaped (no new XSS surface).
        self.assertContains(response, "Solve $x^2$")
        self.assertContains(response, "&lt;b&gt;bold&lt;/b&gt;")
        self.assertNotContains(response, "<b>bold</b>", html=False)

    def test_editor_has_katex_and_live_preview(self):
        User = get_user_model()
        editor = User.objects.create_user("bo-math", password="x", is_staff=True)
        self.client.force_login(editor)
        response = self.client.get(
            reverse("backoffice_quizzes:question-create", kwargs={"pk": self.quiz.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "katex")
        self.assertContains(response, "data-preview")
