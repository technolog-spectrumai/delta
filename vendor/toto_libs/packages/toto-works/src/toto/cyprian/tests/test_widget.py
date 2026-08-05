"""The embedded editor: the form field, the widget, and what they guarantee.

These are about the SEAM, not the editor — the editor itself is JavaScript and
is exercised in the browser. What is worth pinning here is everything a Django
form promises: that the posted value is sanitised before anyone sees it, that
the field behaves like a CharField (required, initial, changed-detection), and
that two widgets on one page do not collide — the failure the full-page editor's
fixed ids would have caused.
"""

from django import forms
from django.template import Context, Template
from django.test import SimpleTestCase, override_settings

from toto.cyprian.forms import CyprianRichTextField
from toto.cyprian.widgets import CyprianRichTextWidget, _ModuleScripts


class _SolutionForm(forms.Form):
    """A form of the shape the callers use: one rich field beside plain ones."""

    title = forms.CharField(max_length=50)
    solution = CyprianRichTextField(required=False, placeholder="Worked solution")


storage_override = override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})


@storage_override
class FieldCleaningTests(SimpleTestCase):
    def test_posted_html_is_sanitised(self):
        form = _SolutionForm(data={
            "title": "t",
            "solution": '<p>Keep <b>this</b></p><script>alert(1)</script>',
        })
        self.assertTrue(form.is_valid(), form.errors)
        cleaned = form.cleaned_data["solution"]
        self.assertIn("<b>this</b>", cleaned)
        self.assertNotIn("<script", cleaned.lower())
        self.assertNotIn("alert(1)", cleaned)

    def test_event_handlers_and_ids_do_not_survive(self):
        form = _SolutionForm(data={
            "title": "t",
            "solution": '<p id="x" onclick="steal()">hi</p>',
        })
        self.assertTrue(form.is_valid(), form.errors)
        cleaned = form.cleaned_data["solution"]
        self.assertNotIn("onclick", cleaned.lower())
        self.assertNotIn('id="x"', cleaned)
        self.assertIn("hi", cleaned)

    def test_a_formula_keeps_its_source(self):
        # The sanitiser drops rendered KaTeX and keeps data-latex; display
        # templates re-render from it. If this ever stops holding, every stored
        # formula silently becomes unreadable.
        form = _SolutionForm(data={
            "title": "t",
            "solution": '<div class="cy-formula" data-latex="e^{i\\pi}+1=0">'
                        '<span class="katex">rendered</span></div>',
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn('data-latex="e^{i\\pi}+1=0"', form.cleaned_data["solution"])

    def test_an_empty_value_is_allowed_when_not_required(self):
        form = _SolutionForm(data={"title": "t", "solution": ""})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["solution"], "")

    def test_required_is_still_required(self):
        class Required(forms.Form):
            body = CyprianRichTextField()

        self.assertFalse(Required(data={"body": ""}).is_valid())

    def test_surrounding_validation_is_untouched(self):
        # The point of the whole design: the rich field is one field among
        # many, and the others keep failing exactly as they did.
        form = _SolutionForm(data={"title": "x" * 80, "solution": "<p>ok</p>"})
        self.assertFalse(form.is_valid())
        self.assertIn("title", form.errors)
        self.assertNotIn("solution", form.errors)

    def test_has_changed_compares_sanitised_values(self):
        field = CyprianRichTextField(required=False)
        # Same content, one carrying markup the sanitiser strips.
        self.assertFalse(field.has_changed('<p id="a">x</p>', "<p>x</p>"))
        self.assertTrue(field.has_changed("<p>x</p>", "<p>y</p>"))
        self.assertFalse(field.has_changed(None, ""))


@storage_override
class WidgetRenderingTests(SimpleTestCase):
    def test_the_textarea_is_the_field_and_starts_visible(self):
        # Progressive enhancement: without JavaScript the textarea must be
        # usable, so it may not be hidden server-side.
        html = _SolutionForm().as_p()
        self.assertIn('name="solution"', html)
        self.assertIn("data-cy-input", html)
        textarea = html[html.index("<textarea"):html.index("</textarea>")]
        self.assertNotIn("hidden", textarea)

    def test_initial_content_lands_in_the_textarea(self):
        form = _SolutionForm(initial={"solution": "<p>seeded</p>"})
        self.assertIn("&lt;p&gt;seeded&lt;/p&gt;", form.as_p())

    def test_ids_derive_from_the_field_never_a_literal(self):
        html = _SolutionForm().as_p()
        self.assertIn('id="id_solution"', html)
        # #cy-editor is the full-page editor's singleton host; a widget that
        # used it would fight the page and its own siblings.
        self.assertNotIn('id="cy-editor"', html)

    def test_two_widgets_on_one_page_do_not_collide(self):
        class Two(forms.Form):
            first = CyprianRichTextField(required=False)
            second = CyprianRichTextField(required=False)

        html = Two().as_p()
        self.assertIn('id="id_first"', html)
        self.assertIn('id="id_second"', html)
        self.assertEqual(html.count("data-cy-field"), 2)

    def test_the_import_map_is_emitted_once_for_two_widgets(self):
        # Two import maps on one page is a hard browser error, so Media must
        # de-duplicate the module block.
        class Two(forms.Form):
            first = CyprianRichTextField(required=False)
            second = CyprianRichTextField(required=False)

        media = str(Two().media)
        self.assertEqual(media.count('type="importmap"'), 1)
        self.assertEqual(media.count("cyprian/tiptap_setup.js"), 1)

    def test_media_carries_what_the_editor_needs(self):
        media = str(_SolutionForm().media)
        for asset in ("katex.min.css", "katex.min.js", "cyprian/field.css",
                      "cyprian/field.js", "cyprian/tiptap_setup.js"):
            self.assertIn(asset, media, asset)

    def test_the_widget_does_not_pull_in_the_page_stylesheet(self):
        # document.css ends in a page-global `@page { size: A4 }`; loading it
        # for a form would put the whole host application on A4 paper.
        self.assertNotIn("cyprian/document.css", str(_SolutionForm().media))

    def test_module_scripts_dedupe_by_equality(self):
        self.assertEqual(_ModuleScripts(), _ModuleScripts())
        self.assertEqual(len({_ModuleScripts(), _ModuleScripts()}), 1)

    def test_the_placeholder_reaches_the_markup(self):
        self.assertIn('data-placeholder="Worked solution"', _SolutionForm().as_p())

    def test_it_renders_inside_an_ordinary_template(self):
        rendered = Template("{{ form.solution }}").render(
            Context({"form": _SolutionForm()}))
        self.assertIn("data-cy-field", rendered)
        self.assertIn("data-upload-url", rendered)
