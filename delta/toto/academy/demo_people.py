"""The demo *participation* layer: people using the courses, not the courses.

``ingress_academy`` seeds the course tree — modules, lessons, tasks, the skill
DAG. That is enough to click through, and useless for judging whether a screen
works: every roster is empty, every metrics page says nothing, every progress
bar is at zero. This module fills the other half so a tester sees the product
with people in it — cohorts with members, half-finished courses, badges earned
by some and not others, and enough answered questions for the metrics pages to
have something to draw.

Everything here is:

* **idempotent** — keyed on stable slugs/usernames, so ``ingress_all`` can run
  on every container start (it does) without multiplying students;
* **deterministic** — one seeded ``Random``, so two environments seeded from the
  same code show the same numbers and a screenshot stays true;
* **obviously fake** — Polish demo names with a ``demo-`` slug prefix and lorem
  ipsum prose, so nobody mistakes a fixture for a real learner. Every account
  shares one password, printed by the command.

It creates no Teacher: ``ingress_academy`` already made the demo teacher who
owns the courses, and a second one would confuse the roster.
"""
from __future__ import annotations

import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.text import slugify

from toto.academy.models import (
    Cohort,
    CohortMembership,
    Course,
    CourseEnrollment,
    Student,
    StudentBadge,
    Teacher,
)
from toto.competence.models import SkillBadge
from toto.people.models import Person
from toto.quizzes.models import (
    QuestionProgress,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizAttemptAnswer,
    QuizQuestion,
)

User = get_user_model()

DEMO_PASSWORD = "demo12345"          # noqa: S105 — a fixture, printed on seed
SEED = 20260805                      # fixed: same data everywhere, every run

# Ordinary Polish names, so the roster reads like a class list rather than a
# test fixture. The "demo-" slug prefix is what marks them as seeded.
_STUDENTS = [
    ("Zofia", "Kowalska"), ("Jan", "Nowak"), ("Maja", "Wiśniewska"),
    ("Antoni", "Wójcik"), ("Lena", "Kowalczyk"), ("Kacper", "Kamiński"),
    ("Hanna", "Lewandowska"), ("Filip", "Zieliński"), ("Julia", "Szymańska"),
    ("Aleksander", "Woźniak"), ("Oliwia", "Dąbrowska"), ("Szymon", "Kozłowski"),
    ("Amelia", "Jankowska"), ("Nikodem", "Mazur"), ("Zuzanna", "Krawczyk"),
    ("Marcel", "Piotrowski"), ("Alicja", "Grabowska"), ("Ignacy", "Pawłowski"),
]

_LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim "
    "veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea "
    "commodo consequat."
)

_LOREM_SHORT = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Vestibulum "
    "tincidunt, nisl eget aliquam luctus."
)


def _person_for(first: str, last: str, index: int) -> Person:
    """A demo learner: a login, a Person, and a Student row, all idempotent."""
    username = f"demo.{slugify(first)}.{slugify(last)}"
    user, created = User.objects.get_or_create(
        username=username,
        defaults={"first_name": first, "last_name": last,
                  "email": f"{username}@example.invalid"},
    )
    if created:
        user.set_password(DEMO_PASSWORD)
        user.save(update_fields=["password"])

    person = Person.objects.filter(user=user).first()
    if person is None:
        person = Person.objects.create(
            user=user,
            display_name=f"{first} {last}",
            email=user.email,
            bio=_LOREM_SHORT if index % 3 == 0 else "",
        )
    return person


def seed(stdout=None) -> dict:
    """Create the participation layer. Returns a count per kind, for the log."""
    # One RULE about randomness keeps this idempotent: every draw happens
    # unconditionally, never inside an `if made:`. The moment a draw is skipped
    # because a row already exists, the stream shifts and the SECOND run samples
    # different students than the first — which is how a re-run grows the data.
    rng = random.Random(SEED)
    now = timezone.now()
    counts = {k: 0 for k in
              ("students", "cohorts", "memberships", "enrolments",
               "badges", "attempts", "answers", "progress")}

    courses = list(Course.objects.order_by("id"))
    if not courses:
        return counts                     # nothing to participate in yet
    teacher = Teacher.objects.filter(is_active=True).order_by("id").first()

    students = []
    for index, (first, last) in enumerate(_STUDENTS):
        person = _person_for(first, last, index)
        student, made = Student.objects.get_or_create(person=person)
        counts["students"] += int(made)
        students.append(student)

    # ── cohorts: two per course, so the roster has something to group by ──
    for course in courses:
        for label, offset in (("Grupa poranna", 0), ("Grupa popołudniowa", 1)):
            cohort, made = Cohort.objects.get_or_create(
                course=course,
                slug=slugify(f"{course.slug}-{label}"),
                defaults={
                    "title": f"{label} — {course.title}",
                    "teacher": teacher,
                    "starts_at": now - timedelta(days=30 - offset * 7),
                    "ends_at": now + timedelta(days=60 + offset * 7),
                    "capacity": 12,
                    "is_active": True,
                },
            )
            counts["cohorts"] += int(made)

    # ── who is on which course, and how far along ─────────────────────────
    # A spread on purpose: a course where nobody has finished looks broken, and
    # one where everybody has finished hides the progress UI entirely.
    for course in courses:
        cohorts = list(Cohort.objects.filter(course=course).order_by("id"))
        roster = rng.sample(students, k=min(len(students), rng.randint(9, 14)))
        for position, student in enumerate(roster):
            finished_days_ago = rng.randint(1, 20)      # drawn unconditionally
            enrolment, made = CourseEnrollment.objects.get_or_create(
                student=student, course=course)
            counts["enrolments"] += int(made)

            # Roughly a fifth have finished; the rest are mid-course.
            if made and position % 5 == 0:
                enrolment.completed_at = now - timedelta(days=finished_days_ago)
                enrolment.save(update_fields=["completed_at"])

            if cohorts:
                _, joined = CohortMembership.objects.get_or_create(
                    cohort=cohorts[position % len(cohorts)], student=student)
                counts["memberships"] += int(joined)

    # ── badges: a pyramid, so the skill tree shows a frontier ─────────────
    badges = list(SkillBadge.objects.order_by("order", "id"))
    for rank, student in enumerate(students):
        # The earlier a student sits in the list, the further they have got —
        # a deterministic spread rather than random noise.
        earned = badges[: max(0, len(badges) - (rank % (len(badges) + 1)))]
        for badge in earned:
            _, made = StudentBadge.objects.get_or_create(student=student, badge=badge)
            counts["badges"] += int(made)

    # ── answered questions: what the metrics pages are made of ────────────
    for quiz in Quiz.objects.filter(is_published=True).order_by("id"):
        questions = list(
            QuizQuestion.objects.filter(quiz=quiz).prefetch_related("answers")
            .order_by("order", "id")
        )
        if not questions:
            continue
        # Only students enrolled on a course that uses this quiz would have
        # answered it; approximating with a slice keeps the numbers plausible.
        answering = rng.sample(students, k=min(len(students), rng.randint(5, 11)))
        for student in answering:
            person = student.person
            # Draw the whole attempt's randomness up front, whether or not the
            # rows exist — see the rule at the top of this function.
            attempt_days_ago = rng.randint(0, 14)
            plan = []
            for question in questions:
                options = list(question.answers.all())
                if not options:
                    plan.append(None)
                    continue
                correct = [a for a in options if a.is_correct]
                wrong = [a for a in options if not a.is_correct]
                # ~70% get it right: enough wrong answers for the distribution
                # chart to be worth looking at.
                pick = (rng.choice(correct) if correct and (rng.random() < 0.7 or not wrong)
                        else rng.choice(wrong or correct))
                plan.append({
                    "pick": pick,
                    "tries": rng.randint(1, 3),
                    "last_days_ago": rng.randint(0, 14),
                    "solved_days_ago": rng.randint(0, 10),
                })

            attempt, made = QuizAttempt.objects.get_or_create(
                quiz=quiz, participant=person,
                defaults={"completed_at": now - timedelta(days=attempt_days_ago)},
            )
            if not made:
                continue
            counts["attempts"] += 1
            for question, step in zip(questions, plan):
                if step is None:
                    continue
                pick = step["pick"]
                QuizAttemptAnswer.objects.get_or_create(
                    attempt=attempt, question=question, defaults={"answer": pick})
                counts["answers"] += 1

                solved = bool(pick.is_correct)
                _, tracked = QuestionProgress.objects.get_or_create(
                    participant=person, question=question,
                    defaults={
                        "is_solved": solved,
                        "attempts": step["tries"],
                        "last_attempt_at": now - timedelta(days=step["last_days_ago"]),
                        "solved_at": (now - timedelta(days=step["solved_days_ago"])
                                      if solved else None),
                    },
                )
                counts["progress"] += int(tracked)

    if stdout:
        stdout.write(
            "  👥  academy demo people: "
            + ", ".join(f"{v} {k}" for k, v in counts.items() if v)
            + f" (login: demo.<imię>.<nazwisko> / {DEMO_PASSWORD})"
        )
    return counts
