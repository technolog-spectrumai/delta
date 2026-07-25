"""Seed a small, spec-aligned demo for the Delta maths e-learning host.

Mirrors the "Nauka" structure from the functional spec (wymagania.tex):
Nauka → Matematyka - szkoła średnia → Klasa → Dział → Lekcja + zadania, plus a
"Matura - poziom podstawowy" segment whose modules have no lessons — only the
worked solution shown after a wrong answer (spec F-10).

Every task is a quizzes.QuizQuestion; the module's attached Quiz is the practice
pool the student works through (correct → leaves the pool, wrong → returns).
Only runs under FULL_INGRESS.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.utils.text import slugify

from toto.ingress import IngressCommand

from toto.academy.models import Course, CourseModule, Lesson, Teacher
from toto.competence.models import SkillBadge, SkillBadgePrerequisite, SkillGroup
from toto.people.models import Person
from toto.quizzes.models import Quiz, QuizAnswer, QuizQuestion


# ── demo content ────────────────────────────────────────────────────────────
# A task is (text, type, [(answer, is_correct, explanation), ...]) with an
# optional 4th element: rich solution HTML (trix) shown as the worked solution.
_ALG_TASKS = [
    ("Uprość wyrażenie: 2x + 3x", "choice",
        [("5x", True, "2x + 3x = (2 + 3)x = 5x"), ("6x", False, ""), ("5", False, ""), ("x", False, "")]),
    ("Rozwiń wzór skróconego mnożenia: (x + 2)²", "choice",
        [("x² + 4x + 4", True, "(a + b)² = a² + 2ab + b²"), ("x² + 4", False, ""),
         ("x² + 2x + 4", False, ""), ("x² + 4x + 2", False, "")],
        "<h2>Rozwiązanie krok po kroku</h2>"
        "<p>Korzystamy ze wzoru skróconego mnożenia: <strong>(a + b)² = a² + 2ab + b²</strong>.</p>"
        "<p>Dla a = x oraz b = 2 mamy: x² + 2·x·2 + 2² = <strong>x² + 4x + 4</strong>.</p>"
        "<blockquote>Wzory skróconego mnożenia warto znać na pamięć — wracają na maturze.</blockquote>"),
    ("Oblicz wartość wyrażenia: 3 · (2 + 4)", "open",
        [("18", True, "3 · 6 = 18")]),
    ("Dla jakiego x zachodzi równość x + 5 = 12? Podaj samo x.", "open",
        [("7", True, "x = 12 − 5 = 7")]),
]

_MATURA_TASKS = [
    ("Zadanie zamknięte. Liczba 2^10 jest równa:", "choice",
        [("1024", True, "2^10 = 1024"), ("512", False, ""), ("2048", False, ""), ("100", False, "")]),
    ("Zadanie otwarte. Rozwiąż równanie 2x − 4 = 10. Podaj x.", "open",
        [("7", True, "2x = 14, więc x = 7")],
        "<h2>Rozwiązanie</h2>"
        "<p>Przenosimy wyraz wolny na prawą stronę: <strong>2x = 10 + 4 = 14</strong>.</p>"
        "<p>Dzielimy obie strony przez 2: <strong>x = 7</strong>.</p>"
        "<p>Sprawdzenie: 2·7 − 4 = 10 ✓</p>"),
    ("Zadanie otwarte. Podaj największą liczbę całkowitą mniejszą od 3,5.", "open",
        [("3", True, "Największa liczba całkowita < 3,5 to 3")]),
]

COURSES = [
    {
        "slug": "matematyka-szkola-srednia",
        "title": "Matematyka - szkoła średnia",
        "description": "Materiał szkoły średniej: klasy, działy, lekcje z narracją głosową i zadania do rozwiązania.",
        "modules": [
            {
                "title": "Klasa 1 · Wyrażenia algebraiczne",
                "badge": ("Wyrażenia algebraiczne", "badge-wyrazenia-algebraiczne"),
                "lesson": "Wprowadzenie do wyrażeń algebraicznych",
                "video": "",
                "quiz_title": "Wyrażenia algebraiczne — zadania",
                "quiz_slug": "wyrazenia-algebraiczne",
                "tasks": _ALG_TASKS,
            },
        ],
    },
    {
        "slug": "matura-podstawowa",
        "title": "Matura - poziom podstawowy",
        "description": "Zadania działowe i arkuszowe, otwarte i zamknięte. Bez osobnych lekcji — rozwiązanie pojawia się po błędnej odpowiedzi (F-10).",
        "modules": [
            {
                "title": "Wyrażenia algebraiczne (arkusze)",
                "badge": ("Matura: wyrażenia", "badge-matura-wyrazenia"),
                "lesson": None,  # matura modules have no lessons
                "video": "",
                "quiz_title": "Matura podstawowa — wyrażenia (zadania)",
                "quiz_slug": "matura-wyrazenia",
                "tasks": _MATURA_TASKS,
            },
        ],
    },
]


class Command(IngressCommand):
    help = "Seed a demo Delta maths course tree (Nauka → szkoła średnia + matura podstawowa)."

    def process(self):
        if not self.full:
            return

        User = get_user_model()
        admin = User.objects.filter(is_superuser=True).order_by("id").first()
        teacher_person = getattr(admin, "community_profile", None)
        if teacher_person is None:
            teacher_person, _ = Person.objects.get_or_create(display_name="Anna Kogut")
        teacher, _ = Teacher.objects.get_or_create(
            person=teacher_person,
            defaults={"title": "Nauczyciel matematyki", "is_active": True},
        )

        group, _ = SkillGroup.objects.get_or_create(
            slug="matematyka", defaults={"title": "Matematyka", "order": 1})

        for ci, cspec in enumerate(COURSES, start=1):
            course, _ = Course.objects.get_or_create(
                slug=cspec["slug"],
                defaults={
                    "title": cspec["title"],
                    "description": cspec["description"],
                    "owner": teacher,
                    "author": admin,
                    "is_published": True,
                    "order": ci,
                },
            )

            for mi, mspec in enumerate(cspec["modules"], start=1):
                quiz = self._ensure_quiz(mspec, teacher_person)

                badge_title, badge_slug = mspec["badge"]
                badge, _ = SkillBadge.objects.get_or_create(
                    slug=badge_slug,
                    defaults={
                        "group": group,
                        "title": badge_title,
                        "icon": "fa-solid fa-square-root-variable",
                        "order": mi,
                    },
                )

                module, _ = CourseModule.objects.get_or_create(
                    course=course,
                    slug=slugify(mspec["title"]),
                    defaults={
                        "title": mspec["title"],
                        "owner": teacher,
                        "unlocks_badge": badge,
                        "lecture_video_url": mspec.get("video", ""),
                        "order": mi,
                    },
                )
                module.attached_quizzes.add(quiz)
                if not module.exam_id and quiz.is_official:
                    module.exam = quiz
                    module.save(update_fields=["exam"])

                if mspec.get("lesson"):
                    lesson, _ = Lesson.objects.get_or_create(
                        module=module,
                        slug=slugify(mspec["lesson"]),
                        defaults={
                            "title": mspec["lesson"],
                            "owner": teacher,
                            "summary": "Lekcja wprowadzająca z narracją głosową.",
                        },
                    )
                    lesson.attached_quizzes.add(quiz)

        # Skill DAG for personalized learning paths: matura builds on algebra,
        # and "Funkcje" has no unlocking module — it demos pure "earn badge"
        # steps in a generated path.
        algebra = SkillBadge.objects.filter(slug="badge-wyrazenia-algebraiczne").first()
        matura = SkillBadge.objects.filter(slug="badge-matura-wyrazenia").first()
        if algebra and matura:
            SkillBadgePrerequisite.objects.get_or_create(
                badge=matura, prerequisite=algebra)

        funkcje, _ = SkillBadge.objects.get_or_create(
            slug="badge-funkcje",
            defaults={
                "group": group,
                "title": "Funkcje",
                "icon": "fa-solid fa-wave-square",
                "order": 3,
            },
        )
        if algebra:
            SkillBadgePrerequisite.objects.get_or_create(
                badge=funkcje, prerequisite=algebra)

        self.stdout.write("  🎓  academy: demo maths course tree + skill DAG seeded (Nauka).")

    def _ensure_quiz(self, mspec, owner_person):
        quiz, created = Quiz.objects.get_or_create(
            slug=mspec["quiz_slug"],
            defaults={
                "title": mspec["quiz_title"],
                "owner": owner_person,
                "is_published": True,
                "is_official": True,
            },
        )
        if created:
            for qi, task in enumerate(mspec["tasks"], start=1):
                text, qtype, answers = task[0], task[1], task[2]
                solution = task[3] if len(task) > 3 else ""
                question = QuizQuestion.objects.create(
                    quiz=quiz, text=text, question_type=qtype, order=qi,
                    solution=solution)
                for ai, (atext, correct, expl) in enumerate(answers, start=1):
                    QuizAnswer.objects.create(
                        question=question, text=atext, is_correct=correct,
                        explanation=expl, order=ai)
        return quiz
