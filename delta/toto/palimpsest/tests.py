"""Lecture notes: the section body is cyprian-written and sanitised.

Before this, section HTML was stored exactly as Trix produced it and rendered
with `|safe` — a teacher (or anyone who reached the still-loosely-guarded
public CRUD) could store a `<script>`. These tests pin the boundary rather than
the editor: what the form accepts, what it stores, and that the page/section
restrictions around it are unaffected.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from toto.palimpsest.forms import SectionForm
from toto.palimpsest.models import Page, Section

User = get_user_model()


class SectionContentSanitisingTests(TestCase):
    def setUp(self):
        self.page = Page.objects.create(title="Lecture 1", slug="lecture-1")

    def _form(self, content, **over):
        data = {"title": "Intro", "content": content, "order": "0"}
        data.update(over)
        return SectionForm(data=data)

    def test_scripts_do_not_survive_a_save(self):
        form = self._form('<p>Keep me</p><script>alert(1)</script>')
        self.assertTrue(form.is_valid(), form.errors)
        section = form.save(commit=False)
        section.page = self.page
        section.save()
        section.refresh_from_db()
        self.assertIn("Keep me", section.content)
        self.assertNotIn("script", section.content.lower())

    def test_event_handlers_are_stripped(self):
        form = self._form('<p onmouseover="steal()">text</p>')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn("onmouseover", form.cleaned_data["content"].lower())

    def test_a_formula_keeps_its_source(self):
        # Display re-renders from data-latex, so losing it loses the formula.
        form = self._form('<div class="cy-formula" data-latex="a^2+b^2">x</div>')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn('data-latex="a^2+b^2"', form.cleaned_data["content"])

    def test_an_image_url_does_not_survive_but_a_data_uri_does(self):
        # Cyprian embeds images as data URIs; a linked /media/ image is dropped
        # by the sanitiser, which is why the migrations inline old ones first.
        linked = self._form('<p><img src="/media/uploads/a.png"></p>')
        self.assertTrue(linked.is_valid(), linked.errors)
        self.assertNotIn("<img", linked.cleaned_data["content"])

        tiny = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAf"
                "FcSJAAAADUlEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC")
        inline = self._form(f'<p><img src="{tiny}"></p>')
        self.assertTrue(inline.is_valid(), inline.errors)
        self.assertIn("data:image/png;base64,", inline.cleaned_data["content"])

    def test_an_empty_body_is_allowed(self):
        form = self._form("")
        self.assertTrue(form.is_valid(), form.errors)

    def test_the_widget_is_rendered_for_the_body(self):
        html = str(SectionForm())
        self.assertIn("data-cy-field", html)
        self.assertIn("data-cy-input", html)

    def test_other_field_restrictions_still_apply(self):
        # The rich field must not swallow the rest of the form's validation.
        form = self._form("<p>ok</p>", order="not-a-number")
        self.assertFalse(form.is_valid())
        self.assertIn("order", form.errors)
