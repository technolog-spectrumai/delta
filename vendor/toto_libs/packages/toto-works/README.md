# toto-works (delta's trim: memo + cyprian)

**Vault-backed slide decks and documents.** This is delta's copy of the suite's
`toto-works` wheel, trimmed to the two apps delta installs:

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

Each app carries its own README under `src/toto/<app>/README.md`; those are the
reference for the file formats, the endpoints and the design decisions.

## Why this copy is trimmed

Upstream the same wheel also carries `antaresia`, `kanban` and `primula`. Delta
installs none of them — and `antaresia` is the app whose `workflows.WorkflowRun`
foreign key drags in `toto-flow`, so dropping it is what lets this package
depend on `toto-base` alone and keeps the whole workflow tier out of delta.

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
| `delta/settings.py` | `toto.editor`, `toto.memo`, `toto.cyprian` in `INSTALLED_APPS` |
| `delta/urls.py` | `/memo/` and `/cyprian/` |
| `toto.academy` | a lesson's presentation **is** a memo deck — see the academy app |
| `scripts/download_vendor.py` | ACE, KaTeX, Trix and reveal.js, the browser libraries both apps load |

## Requires

`toto-base` at the same version, for the vault (`VaultFile`, `Bucket`, the
editor/play plugin registries and the file picker), `toto.editor`'s
`BaseFileDisplayView`, `toto.ui`'s `PageProcessor` and `toto.core`'s `Platform`.
Both apps are pure file editors: neither adds a table, so installing or removing
either one is a settings change with no migration behind it.
