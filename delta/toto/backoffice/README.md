# Teacher back office (Panel autorski)

A dedicated, ergonomic authoring area at `/panel/` so **teachers never need the
Django admin** to create learning content. `toto.backoffice` is a *shell* app
(no models): it owns the dashboard, nav, access gate and a shared render helper.
Each authoring **module** lives in the app that owns its data and plugs into the
shell.

## Shell

- `access.py` — `is_backoffice_user(user)` = academy **Teacher** row **OR**
  `is_staff`; `@teacher_required` = `@login_required` + `Http404` for non-members.
- `shell.py` — `backoffice_render(request, template, ctx, active=<key>)` injects
  the module registry + active tab and runs `PageProcessor` (oya chrome).
- `nav.py` — `get_modules()`; the dashboard/nav render these cards. A module whose
  app is not installed is dropped; `live=False` shows a "coming soon" placeholder.
- `context_processors.backoffice_access` — exposes `can_access_backoffice` to every
  template (teacher-only entry link in the academy chrome).
- Templates: `backoffice/base.html`, `dashboard.html`, `_nav.html`, plus reusable
  `_generic_form.html` and `_generic_confirm_delete.html`.

## Modules (live)

| Module | App | URL / namespace |
|---|---|---|
| Tasks (quizzes) | `toto.quizzes` | `/panel/zadania/` · `backoffice_quizzes` |
| Courses | `toto.academy` | `/panel/kursy/` · `backoffice_courses` |
| Skills & badges | `toto.competence` | `/panel/kompetencje/` · `backoffice_skills` |
| Notes | `toto.palimpsest` | `/panel/notatki/` · `backoffice_notes` |
| Library | `toto.library` | `/panel/biblioteka/` · `backoffice_library` |

## Adding a module

1. In the owning app add `backoffice_views.py` (function views, `@teacher_required`,
   render via `backoffice_render(..., active="<key>")`), `backoffice_urls.py`
   (`app_name = "backoffice_<x>"`), and a `*_forms.py` of `ModelForm`s
   (style with `apply_oya_field_styles`; rich text via `TrixEditorWidget`;
   auto-slug via `toto.backoffice.utils.unique_slug`).
2. Templates under `<app>/templates/<app>/backoffice/` extending `backoffice/base.html`
   (or reuse `backoffice/_generic_form.html` / `_generic_confirm_delete.html`).
3. Register the route include in `delta/delta/delta/urls.py` under `/panel/<prefix>/`.
4. Add / flip the card in `nav.py`.
5. Register every parameterized route in `delta/delta/delta/management/commands/smoke_urls.py`
   (`STAFF_ONLY` + `PARAM_SOURCES`), else the gate fails with `UNMAPPED`.
6. Tests in the owning app's `tests.py` (gate + CRUD), run with dotted module labels
   (`manage.py test toto.<app>.tests`).

## TODO — panel roadmap (deferred)

Content authoring is done; these operational/roster and richer-media features are
next so teachers can run everything from the panel:

- [x] **In-panel PDF & video upload** — done for lessons (video + PDF notes → Vault via
  `toto.backoffice.vault_upload.create_vault_file`). Library `vault_file` upload still TODO.
- [x] **Watch video via a custom VOD app** — `toto.vod` copied into delta (native player +
  vault "Play" plugins) plus an enrollment-gated, Range-seekable inline `<video>` player on
  the course page (`academy:lesson-video` / `lesson-notes`).
- [ ] **Cohort management** — create cohorts (rosters, capacity, start/end dates).
- [ ] **Enrolment** — teacher enrols students into a course / cohort.
- [ ] **Completion** — mark student module/course completion.
- [ ] **Badge awards** — award `SkillBadge`s to students.
- [ ] **Certificates** — issue certificates and link the teacher signing flow
  (`academy:certificate-sign`, already teacher-gated).
- [ ] Math (KaTeX) in lesson/script content; drag-and-drop reorder (baseline is
  up/down); promote a Person to Teacher from the panel.
