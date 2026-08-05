"""Form fields that carry cyprian-edited HTML.

The field's whole job is the boundary: whatever the browser posted becomes
sanitised HTML before anything else sees it. Everything else about the form —
required-ness, the other fields' validation, formsets, error re-rendering — is
ordinary Django and stays that way, which is the point of being a plain
``CharField`` subclass rather than a new kind of thing.

Deliberately NOT routed through ``document_format.Document``: that applies a
document's defaults (title, font, margins) and its own validation to something
that is a fragment, not a document.
"""
from __future__ import annotations

from django import forms
from django.apps import apps

from toto.cyprian.sanitize_html import sanitize_content
from toto.cyprian.widgets import CyprianRichTextWidget


class CyprianRichTextField(forms.CharField):
    """HTML written in cyprian's editor, sanitised on the way in.

    The sanitiser is the same one the full-page editor runs, so a fragment
    written here and a document written there obey one policy — notably: no
    ``id`` attributes, images only as ``data:`` URIs, and formulas surviving as
    ``data-latex`` (the rendered KaTeX markup is dropped and re-rendered for
    display, which is why display templates must render from ``data-latex``).
    """

    widget = CyprianRichTextWidget

    def __init__(self, *args, placeholder: str = "", min_height: str = "16rem", **kwargs):
        kwargs.setdefault("strip", False)   # whitespace is content in a document
        super().__init__(*args, **kwargs)
        if isinstance(self.widget, CyprianRichTextWidget):
            self.widget.placeholder = placeholder
            self.widget.min_height = min_height

    def clean(self, value):
        value = super().clean(value)
        if not value:
            return value
        return sanitize_content(value)

    def has_changed(self, initial, data):
        # Compare what would be STORED, not what was typed: the editor
        # normalises markup on load (TipTap re-serialises), so a field the user
        # never touched otherwise reports as changed on every save.
        if initial is None and not data:
            return False
        return sanitize_content(initial or "") != sanitize_content(data or "")
