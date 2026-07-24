import re

from django.db import models
from django.utils import timezone

from trix_editor.fields import TrixEditorField

from toto.people.models import Person


def normalize_answer(text: str) -> str:
    """Normalize an open-text answer for comparison.

    Lowercase, trim, unify the decimal separator (comma→dot) and drop all
    whitespace so that e.g. "1/2", " 1 / 2 " and "0,5"/"0.5" compare sensibly
    for short maths answers (spec F-10: open matura tasks).
    """
    text = (text or "").strip().lower()
    text = text.replace(",", ".")
    text = re.sub(r"\s+", "", text)
    return text


class Quiz(models.Model):
    owner = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_quizzes",
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200)
    description = models.TextField(blank=True)
    is_published = models.BooleanField(default=False)
    is_official = models.BooleanField(
        default=False,
        help_text="Official quizzes can be set as the exam for a course module.",
    )
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "title"]

    def __str__(self):
        return self.title

    # --- practice pool (delta) -------------------------------------------------
    # A Quiz doubles as a "task pool" for a topic/section: the student practises
    # its questions until every one is solved. Correct answers leave the pool,
    # wrong ones stay (spec F-04/F-06).

    def next_unsolved_question(self, participant, after_id=None):
        """The next question this participant has not yet solved.

        `after_id` (the question just answered) is pushed to the back so a wrong
        answer is not re-shown immediately when other tasks remain (spec F-09:
        "spróbuj ponownie za chwilę").
        """
        solved_ids = list(
            QuestionProgress.objects
            .filter(question__quiz=self, participant=participant, is_solved=True)
            .values_list("question_id", flat=True)
        )
        qs = self.questions.exclude(id__in=solved_ids).order_by("order", "id")
        if after_id is not None:
            others = qs.exclude(id=after_id)
            return others.first() or qs.filter(id=after_id).first()
        return qs.first()

    def practice_stats(self, participant):
        total = self.questions.count()
        solved = (
            QuestionProgress.objects
            .filter(question__quiz=self, participant=participant, is_solved=True)
            .count()
        )
        return {
            "total": total,
            "solved": solved,
            "remaining": max(0, total - solved),
            "complete": total > 0 and solved >= total,
        }


class QuizQuestion(models.Model):
    TYPE_CHOICE = "choice"
    TYPE_OPEN = "open"
    TYPE_CHOICES = [
        (TYPE_CHOICE, "Multiple choice (A–D)"),
        (TYPE_OPEN, "Open answer (typed)"),
    ]

    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    text = models.TextField()
    question_type = models.CharField(
        max_length=10,
        choices=TYPE_CHOICES,
        default=TYPE_CHOICE,
        help_text="Closed (A–D) or open (the student types a single answer). "
                  "For open questions, the accepted answer(s) are the QuizAnswer "
                  "rows marked is_correct.",
    )
    explanation = models.TextField(blank=True)
    solution = TrixEditorField(
        blank=True,
        help_text=(
            "Rich worked solution (WYSIWYG). Shown after any practice "
            "submission and in the graded review. Falls back to the plain "
            "explanation when empty."
        ),
    )
    hint = models.TextField(
        blank=True,
        help_text=(
            "Optional plain-text hint shown in a modal via the 'Show hint' "
            "button on the practice page."
        ),
    )
    is_multiple_choice = models.BooleanField(
        default=False,
        help_text="Closed questions only: if true, several answers may be selected.",
    )
    max_time = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Optional time limit for this question in minutes.",
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text[:80]

    @property
    def has_hint(self):
        return bool(self.hint.strip())

    def correct_answer_ids(self):
        return set(self.answers.filter(is_correct=True).values_list("id", flat=True))

    def check_choice(self, selected_ids) -> bool:
        try:
            selected = {int(i) for i in selected_ids}
        except (TypeError, ValueError):
            return False
        correct = self.correct_answer_ids()
        if not correct:
            return False
        if self.is_multiple_choice:
            return selected == correct
        # single choice: exactly one correct answer picked
        return len(selected) == 1 and selected.issubset(correct)

    def check_open(self, text: str) -> bool:
        submitted = normalize_answer(text)
        if not submitted:
            return False
        for answer in self.answers.filter(is_correct=True):
            if normalize_answer(answer.text) == submitted:
                return True
        return False


class QuizAnswer(models.Model):
    question = models.ForeignKey(
        QuizQuestion,
        on_delete=models.CASCADE,
        related_name="answers",
    )
    text = models.TextField()
    is_correct = models.BooleanField(default=False)
    explanation = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    traits = models.ManyToManyField(
        "QuizTrait",
        through="QuizAnswerTrait",
        related_name="quiz_answers",
        blank=True,
    )

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text[:80]


class QuizAnswerTrait(models.Model):
    answer = models.ForeignKey(
        QuizAnswer,
        on_delete=models.CASCADE,
        related_name="trait_mappings",
    )
    trait = models.ForeignKey(
        "QuizTrait",
        on_delete=models.CASCADE,
        related_name="answer_mappings",
    )
    weight = models.IntegerField(
        default=1,
        help_text="How strongly this answer contributes to the trait.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["answer", "trait"],
                name="unique_trait_per_quiz_answer",
            ),
        ]

    def __str__(self):
        return f"{self.answer} -> {self.trait} ({self.weight})"


class QuizTrait(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=100, default="fa-solid fa-star")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "title"]

    def __str__(self):
        return self.title


class QuizAttempt(models.Model):
    participant = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="quiz_attempts",
    )
    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name="attempts",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.participant} -> {self.quiz}"

    def trait_scores(self):
        scores = {}
        mappings = (
            QuizAnswerTrait.objects
            .filter(answer__attempt_selections__attempt=self)
            .select_related("trait")
        )
        for mapping in mappings:
            scores[mapping.trait] = scores.get(mapping.trait, 0) + mapping.weight
        return scores


class QuizAttemptAnswer(models.Model):
    attempt = models.ForeignKey(
        QuizAttempt,
        on_delete=models.CASCADE,
        related_name="selected_answers",
    )
    question = models.ForeignKey(
        QuizQuestion,
        on_delete=models.CASCADE,
        related_name="attempt_answers",
    )
    answer = models.ForeignKey(
        QuizAnswer,
        on_delete=models.CASCADE,
        related_name="attempt_selections",
    )
    selected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "question", "answer"],
                name="unique_selected_answer_per_attempt",
            ),
        ]

    def __str__(self):
        return f"{self.attempt} / {self.question} -> {self.answer}"


class QuestionProgress(models.Model):
    """Per-student mastery of a single task in the practice pool (spec F-04/F-06).

    A question is "in the pool" for a participant until it is solved. Once solved,
    it is removed (never shown again); a wrong answer only bumps `attempts` and
    leaves it in the pool. Section progress = solved / total for the quiz.
    """
    participant = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="question_progress",
    )
    question = models.ForeignKey(
        QuizQuestion,
        on_delete=models.CASCADE,
        related_name="progress_records",
    )
    is_solved = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    solved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["question__order", "question_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["participant", "question"],
                name="unique_progress_per_participant_question",
            ),
        ]

    def __str__(self):
        state = "solved" if self.is_solved else "unsolved"
        return f"{self.participant} / {self.question} ({state})"

    def record_attempt(self, correct: bool):
        self.attempts += 1
        self.last_attempt_at = timezone.now()
        if correct and not self.is_solved:
            self.is_solved = True
            self.solved_at = timezone.now()
        self.save()
        return self
