"""Cyprian's editor as an ordinary Django form widget.

The full-page editor (``cyprian:edit``) owns a *document*: a vault file, a title,
margins, a table of contents, page breaks. Plenty of content is not a document —
a quiz's worked solution, a lecture note's section — and for those the writing
experience is the only part worth reusing.

That reuse is possible because the editor core already is: ``CyprianTipTap.create
(element, html, handlers)`` knows nothing about files, and ``sanitize_content``
is pure stdlib. This widget is the thin Django end of that seam:

* it renders a hidden ``<textarea>`` — the real form input, so the field posts,
  validates and re-renders on error exactly like any other field — plus a host
  element the editor mounts on;
* ``static/cyprian/field.js`` mounts one editor per host and writes the HTML back
  into the textarea on every change;
* :class:`toto.cyprian.forms.CyprianRichTextField` sanitises on the way in.

Nothing here touches the vault, and the full-page editor is unchanged. The only
shared server endpoint is ``cyprian:media_upload``, which stores nothing: it
answers with a data URI, which is also the only image form the sanitiser keeps.
"""
from __future__ import annotations

from django import forms
from django.utils.safestring import mark_safe

from toto.memo import tiptap


class _ModuleScripts:
    """The import map and the TipTap module, as one Media entry.

    ``forms.Media`` renders a list of URLs as plain ``<script src>`` tags, and
    the editor needs neither: an import map (inline JSON, and it must come
    BEFORE any module that imports a bare specifier) and ``type="module"``.
    Media accepts any object with ``__html__``, which is the documented escape
    hatch for exactly this.
    """

    def __html__(self) -> str:
        from django.templatetags.static import static

        return mark_safe(
            f'<script type="importmap">{tiptap.import_map_json()}</script>'
            f'<script type="module" src="{static("cyprian/tiptap_setup.js")}"></script>'
        )

    # Media de-duplicates by equality, so two widgets on one page must emit this
    # once. Without these, two fields would produce two import maps — which the
    # browser refuses outright ("Only one import map is allowed").
    def __eq__(self, other) -> bool:
        return isinstance(other, _ModuleScripts)

    def __hash__(self) -> int:
        return hash(_ModuleScripts)


class CyprianRichTextWidget(forms.Textarea):
    """A rich-text editor over an HTML string.

    Degrades honestly: with JavaScript off the textarea is still there and still
    posts, so the field keeps working — it just shows the HTML source.
    """

    template_name = "cyprian/widgets/_richtext.html"

    def __init__(self, attrs=None, *, placeholder: str = "", min_height: str = "16rem"):
        self.placeholder = placeholder
        self.min_height = min_height
        super().__init__(attrs)

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["placeholder"] = self.placeholder
        context["widget"]["min_height"] = self.min_height
        return context

    @property
    def media(self):
        return forms.Media(
            css={"all": ["vendor/katex/katex.min.css", "cyprian/field.css"]},
            js=[
                "vendor/katex/katex.min.js",
                "cyprian/field.js",
                _ModuleScripts(),
            ],
        )
