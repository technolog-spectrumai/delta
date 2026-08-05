"""Host-level tests: the Polish catalogue, and the flags that shape the build.

delta ships to Polish schools with ``LANGUAGE_CODE=pl``, so an untranslated
string is a visible defect rather than a nicety. The catalogue lives in
``delta/locale/pl/LC_MESSAGES/`` and is COMPILED IN THE REPOSITORY (the image
has no gettext): editing ``django.po`` without running ``compilemessages``
leaves the platform in English, silently. These tests are what makes that
loud — they read the compiled catalogue, not the source.
"""

from django.test import SimpleTestCase
from django.utils import translation


class PolishCatalogueTests(SimpleTestCase):
    """The compiled .mo is present, current, and actually reached."""

    # A spread across the surfaces a teacher and a student each see, so a
    # partially-compiled catalogue cannot pass.
    SAMPLES = {
        "Dashboard": "Pulpit",
        "My Profile": "Mój profil",
        "Skills": "Umiejętności",
        "Books": "Książki",
        "Subscription": "Subskrypcja",
        "Materials": "Materiały",
        "Save": "Zapisz",
        "Delete": "Usuń",
        "New course": "Nowy kurs",
        "Worked solution": "Rozwiązanie krok po kroku",
    }

    def test_the_samples_are_translated(self):
        with translation.override("pl"):
            for source, expected in self.SAMPLES.items():
                with self.subTest(source=source):
                    self.assertEqual(translation.gettext(source), expected)

    def test_english_is_untouched(self):
        with translation.override("en"):
            self.assertEqual(translation.gettext("Dashboard"), "Dashboard")

    def test_nothing_in_the_sample_falls_back_to_english(self):
        # The failure this catches: a .po edited and never compiled. gettext
        # then returns the msgid, which is English and looks "fine" in code.
        with translation.override("pl"):
            unchanged = [s for s in self.SAMPLES if translation.gettext(s) == s]
        self.assertEqual(unchanged, [], "these fell back to English — recompile "
                                        "the catalogue (manage.py compilemessages -l pl)")
