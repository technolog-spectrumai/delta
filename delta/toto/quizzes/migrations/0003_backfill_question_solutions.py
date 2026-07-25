# Backfill rich solutions from the existing plain-text explanations:
# question.explanation plus the correct answers' explanations become an
# initial HTML solution. The plain fields are left untouched.

from django.db import migrations
from django.utils.html import escape


def build_solution_html(question_explanation, correct_answers):
    """Compose initial solution HTML from plain-text explanations.

    correct_answers: iterable of (answer_text, answer_explanation). All text
    is escaped — these are plain-text fields being promoted into an HTML
    field that templates render with |safe.
    """
    parts = []
    if (question_explanation or "").strip():
        parts.append("<p>%s</p>" % escape(question_explanation.strip()))
    for text, explanation in correct_answers:
        if (explanation or "").strip():
            parts.append(
                "<p><strong>%s:</strong> %s</p>"
                % (escape((text or "").strip()), escape(explanation.strip()))
            )
    return "".join(parts)


def forwards(apps, schema_editor):
    QuizQuestion = apps.get_model("quizzes", "QuizQuestion")
    questions = (
        QuizQuestion.objects
        .filter(solution="")
        .prefetch_related("answers")
    )
    for question in questions:
        correct = [
            (answer.text, answer.explanation)
            for answer in question.answers.all()
            if answer.is_correct
        ]
        html = build_solution_html(question.explanation, correct)
        if html:
            question.solution = html
            question.save(update_fields=["solution"])


class Migration(migrations.Migration):

    dependencies = [
        ('quizzes', '0002_quizquestion_solution'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
