"""Teacher back-office views for authoring quizzes and questions.

Kept separate from the student-facing ``views.py``. All views are gated by the
shared ``teacher_required`` and render through ``backoffice_render`` so they sit
inside the back-office shell (nav + platform chrome).
"""

from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from toto.backoffice.access import teacher_required
from toto.backoffice.shell import backoffice_render
from toto.backoffice.utils import apply_reorder

from .forms import QuestionForm, QuizForm, build_answer_formset
from .models import Quiz, QuizAttemptAnswer, QuizQuestion, QuizTrait


def _format_label(question):
    if question.question_type == QuizQuestion.TYPE_OPEN:
        return _("Open")
    return _("Multiple choice") if question.is_multiple_choice else _("Single choice")


# --- quizzes ---------------------------------------------------------------

@teacher_required
def quiz_list(request):
    quizzes = (
        Quiz.objects.annotate(
            question_count=Count("questions", distinct=True),
            # Finished attempts only: the metrics page counts completed ones, so
            # a link offered on any other basis would open an empty report.
            completed_attempt_count=Count(
                "attempts",
                filter=Q(attempts__completed_at__isnull=False),
                distinct=True,
            ),
        )
        .order_by("order", "title")
    )
    return backoffice_render(
        request, "quizzes/backoffice/quiz_list.html",
        {"quizzes": quizzes}, active="quizzes",
    )


@teacher_required
def quiz_create(request):
    if request.method == "POST":
        form = QuizForm(request.POST)
        if form.is_valid():
            quiz = form.save(commit=False)
            quiz.owner = getattr(request.user, "community_profile", None)
            quiz.save()
            messages.success(request, _("Quiz created. Now add some questions."))
            return redirect("backoffice_quizzes:question-list", pk=quiz.pk)
    else:
        form = QuizForm()
    return backoffice_render(request, "quizzes/backoffice/quiz_form.html", {
        "form": form,
        "title": _("New quiz"),
        "submit_label": _("Create quiz"),
        "icon": "fa-solid fa-plus",
    }, active="quizzes")


@teacher_required
def quiz_edit(request, pk):
    quiz = get_object_or_404(Quiz, pk=pk)
    if request.method == "POST":
        form = QuizForm(request.POST, instance=quiz)
        if form.is_valid():
            form.save()
            messages.success(request, _("Quiz saved."))
            return redirect("backoffice_quizzes:question-list", pk=quiz.pk)
    else:
        form = QuizForm(instance=quiz)
    return backoffice_render(request, "quizzes/backoffice/quiz_form.html", {
        "form": form,
        "quiz": quiz,
        "title": _("Edit quiz"),
        "submit_label": _("Save quiz"),
        "icon": "fa-solid fa-pen-to-square",
    }, active="quizzes")


@teacher_required
def quiz_delete(request, pk):
    quiz = get_object_or_404(Quiz, pk=pk)
    if request.method == "POST":
        quiz.delete()
        messages.success(request, _("Quiz deleted."))
        return redirect("backoffice_quizzes:quiz-list")
    return backoffice_render(
        request, "quizzes/backoffice/quiz_confirm_delete.html",
        {"quiz": quiz}, active="quizzes",
    )


# --- questions -------------------------------------------------------------

@teacher_required
def question_list(request, pk):
    quiz = get_object_or_404(Quiz, pk=pk)
    # How many finished attempts answered each question — the "if anyone
    # answered" test for showing a metrics link at all.
    answered = dict(
        QuizAttemptAnswer.objects
        .filter(attempt__quiz=quiz, attempt__completed_at__isnull=False)
        .values_list("question")
        .annotate(n=Count("id"))
    )
    rows = []
    for question in quiz.questions.prefetch_related("answers").order_by("order", "id"):
        answers = list(question.answers.all())
        rows.append({
            "q": question,
            "format": _format_label(question),
            "num_options": len(answers),
            "num_correct": sum(1 for a in answers if a.is_correct),
            "answered": answered.get(question.pk, 0),
        })
    return backoffice_render(request, "quizzes/backoffice/question_list.html", {
        "quiz": quiz,
        "rows": rows,
        "quiz_answered": sum(answered.values()),
    }, active="quizzes")


def _render_editor(request, quiz, question, form, formset, *, title, submit_label, icon):
    return backoffice_render(request, "quizzes/backoffice/question_form.html", {
        "quiz": quiz,
        "question": question,
        "form": form,
        "formset": formset,
        "traits": QuizTrait.objects.all(),
        "title": title,
        "submit_label": submit_label,
        "icon": icon,
        "FORMAT_SINGLE": QuestionForm.FORMAT_SINGLE,
        "FORMAT_MULTIPLE": QuestionForm.FORMAT_MULTIPLE,
        "FORMAT_OPEN": QuestionForm.FORMAT_OPEN,
    }, active="quizzes")


def _save_answers(formset, question):
    """Persist the answer formset against a saved question, then normalise
    open-question correctness and option ordering."""
    formset.instance = question
    formset.save()
    if formset.answer_format == QuestionForm.FORMAT_OPEN:
        question.answers.update(is_correct=True)
    order = 1
    for form in formset.forms:
        if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
            continue
        obj = form.instance
        if obj.pk and (obj.text or "").strip():
            if obj.order != order:
                obj.order = order
                obj.save(update_fields=["order"])
            order += 1


@teacher_required
def question_create(request, pk):
    quiz = get_object_or_404(Quiz, pk=pk)
    FormSet = build_answer_formset(extra=4)
    if request.method == "POST":
        form = QuestionForm(request.POST)
        formset = FormSet(request.POST, instance=QuizQuestion(quiz=quiz))
        formset.answer_format = request.POST.get("answer_format") or QuestionForm.FORMAT_SINGLE
        if form.is_valid() and formset.is_valid():
            question = form.save(commit=False)
            question.quiz = quiz
            if not question.order:
                question.order = quiz.questions.count() + 1
            question.save()
            _save_answers(formset, question)
            messages.success(request, _("Question added."))
            return redirect("backoffice_quizzes:question-list", pk=quiz.pk)
    else:
        form = QuestionForm(initial={"order": quiz.questions.count() + 1})
        formset = FormSet(instance=QuizQuestion(quiz=quiz))
    return _render_editor(request, quiz, None, form, formset,
                          title=_("New question"),
                          submit_label=_("Create question"),
                          icon="fa-solid fa-plus")


@teacher_required
def question_edit(request, pk):
    question = get_object_or_404(QuizQuestion.objects.select_related("quiz"), pk=pk)
    quiz = question.quiz
    FormSet = build_answer_formset(extra=1)
    if request.method == "POST":
        form = QuestionForm(request.POST, instance=question)
        formset = FormSet(request.POST, instance=question)
        formset.answer_format = request.POST.get("answer_format") or QuestionForm.FORMAT_SINGLE
        if form.is_valid() and formset.is_valid():
            question = form.save()
            _save_answers(formset, question)
            messages.success(request, _("Question saved."))
            return redirect("backoffice_quizzes:question-list", pk=quiz.pk)
    else:
        form = QuestionForm(instance=question)
        formset = FormSet(instance=question)
    return _render_editor(request, quiz, question, form, formset,
                          title=_("Edit question"),
                          submit_label=_("Save question"),
                          icon="fa-solid fa-pen-to-square")


@teacher_required
def question_delete(request, pk):
    question = get_object_or_404(QuizQuestion.objects.select_related("quiz"), pk=pk)
    quiz = question.quiz
    if request.method == "POST":
        question.delete()
        messages.success(request, _("Question deleted."))
        return redirect("backoffice_quizzes:question-list", pk=quiz.pk)
    return backoffice_render(request, "quizzes/backoffice/question_confirm_delete.html",
                             {"quiz": quiz, "question": question}, active="quizzes")


@teacher_required
@require_POST
def question_reorder(request, pk):
    """Reorder questions — drag-drop (order list) or up/down (question+direction)."""
    quiz = get_object_or_404(Quiz, pk=pk)
    if apply_reorder(quiz.questions.order_by("order", "id"), request, id_param="question"):
        return HttpResponse(status=204)
    return redirect("backoffice_quizzes:question-list", pk=quiz.pk)
