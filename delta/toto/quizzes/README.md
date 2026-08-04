# toto.quizzes

Task and quiz engine for the delta maths e-learning host: graded quizzes,
the practice task pool that drives the core learning loop (spec F-04/F-06),
and optional trait scoring.

## Purpose

A `Quiz` is both a submit-once graded quiz and a **practice pool**: each
section's tasks are questions the student works through one at a time. A
correct answer removes the task from the pool; a wrong one keeps it in and
reveals the worked solution. Questions can carry a rich WYSIWYG solution
(trix, rendered as a blog-section-style card after any submission) and an
optional plain-text hint (shown in a modal via the "Show hint" button).

## Models

- `Quiz` — `owner` (Person), `title`, `slug`, `description`, `is_published`, `is_official` (usable as a module exam), `order`. Practice helpers: `next_unsolved_question(participant, after_id)`, `practice_stats(participant)`.
- `QuizQuestion` — `quiz` FK, `text`, `question_type` (`choice` A–D / `open` typed), `explanation` (plain short feedback), `solution` (TrixEditorField — rich worked solution), `hint` (plain text; `has_hint` property), `is_multiple_choice`, `max_time`, `order`. Answer checking: `check_choice()`, `check_open()` (normalized comparison for maths answers).
- `QuizAnswer` — `question` FK, `text`, `is_correct`, `explanation`, `order`, `traits` M2M.
- `QuizTrait` / `QuizAnswerTrait` — optional trait dimensions with per-answer weights (self-assessment style quizzes).
- `QuizAttempt` / `QuizAttemptAnswer` — a person's graded run (`started_at`, `completed_at`) and the selected answers; `trait_scores()` aggregates trait weights.
- `QuestionProgress` — the practice-pool mastery record, unique per (participant, question): `is_solved`, `attempts`, `first_seen_at`, `last_attempt_at`, `solved_at`; `record_attempt(correct)` maintains it. Feeds the student progress page and the academy classroom dashboards.

## Views

- `quiz-list` / `quiz-detail` — catalog and the submit-once graded flow with a post-completion review (solution cards, per-answer feedback, trait chips).
- `quiz-practice` — the pool: one unsolved task at a time, hint modal on the question form, solution card after any submission, solved/total progress bar.
- `quiz-metrics` — per-question answer distributions and trait aggregates (staff or quiz owner).

## Dependencies

- `people` — `Quiz.owner`, `QuizAttempt.participant`, `QuestionProgress.participant` are Person FKs
- `trix_editor` — the rich `solution` field
- consumed by `academy` — modules attach quizzes and set official exams; classroom dashboards read `QuestionProgress`
