# toto-works (delta's trim: memo + cyprian + primula)

**Vault-backed slide decks, documents and sheets.** This is delta's copy of the
suite's `toto-works` wheel, trimmed to the three apps delta installs:

- **`toto.memo`** — a slide deck is one self-contained `.pml` XML file in the
  vault. Author it in the browser: pick a slide layout, fill each box with text,
  a list, a picture, code, a quote or a formula, and present the result as a
  full reveal.js slideshow. Nothing is stored in the database.
- **`toto.cyprian`** — a writer. One WYSIWYG canvas that draws the A4 page
  boundaries as you type, so a page break in the editor is a page break in the
  PDF. Headings, lists, quotes, code, tables, images, formulas and callouts from
  a toolbar; a **Source** tab holding the file itself; and Save-as-PDF /
  Save-as-HTML into the vault. A document is one XML file, also with no rows
  behind it.
- **`toto.primula`** — spreadsheets. The grid is Univer (browser-side, vendored
  UMD bundles); the server stores one JSON workbook snapshot per `sheet` vault
  file plus a capped `SheetVersion` history table. On delta the whole app is
  teachers-only: every `/primula/` route is wrapped in `teacher_required` and
  the entry point is the Panel autorski, not a dashboard tile.

Each app carries its own README under `src/toto/<app>/README.md`; those are the
reference for the file formats, the endpoints and the design decisions.

## Why this copy is trimmed

Upstream the same wheel also carries `kanban`, which delta does not install —
and it used to carry `antaresia`, the app whose `workflows.WorkflowRun` foreign
key drags in `toto-flow`; dropping that is what lets this package depend on
`toto-base` alone and keeps the whole workflow tier out of delta. (Upstream
retired antaresia in its 1.45; `primula` joined this trim in delta's 1.10.)

That is the policy `full_secession.md` already describes for whole packages
("trimmed the vendored copy to what delta installs"), applied one level down.
The consequence to remember: **this wheel's contents differ from the same-named
wheel in the portal monorepo**, which is why delta's version series is its own,
and why an upstream change to memo or cyprian arrives here by being copied in
rather than by a subtree pull. When you do copy one in, bring the whole app
directory — cyprian imports `memo.sanitize` and `memo.media`, so half an update
is worse than none.

## What delta wires up

| Where | What |
|---|---|
| `delta/settings.py` | `toto.editor`, `toto.memo`, `toto.cyprian`, `toto.primula` in `INSTALLED_APPS` |
| `delta/urls.py` | `/memo/`, `/cyprian/`, and `/primula/` through the `teacher_required` wrapper (`delta/primula_urls.py`) |
| `toto.academy` | a lesson's presentation **is** a memo deck — see the academy app |
| `scripts/download_vendor.py` | ACE, KaTeX, Trix and reveal.js for memo/cyprian; the Univer bundles + react/react-dom/rxjs peer globals for primula |

## Requires

`toto-base` at the same version, for the vault (`VaultFile`, `Bucket`, the
editor/play plugin registries and the file picker), `toto.editor`'s
`BaseFileDisplayView`, `toto.ui`'s `PageProcessor` and `toto.core`'s `Platform`.
memo and cyprian are pure file editors with no tables; primula is the exception
— its `SheetVersion` history table means installing it is a migration, and the
vault needs the `sheet` file-type choice (toto-base migration 0010).
