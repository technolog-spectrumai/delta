from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.academy.models import Course, CourseModule
from toto.competence.models import SkillBadge, SkillBadgePrerequisite, SkillGroup
from toto.core.models import Platform
from toto.library.models import Book, LibraryCollection
from toto.palimpsest.models import Page
from toto.people.models import Person


def _platform():
    Platform.objects.create(site_name="Test", author="tests", publication_year=2026)


def _staff(username):
    return get_user_model().objects.create_user(username, password="x", is_staff=True)


def _person_user_field():
    User = get_user_model()
    for field in Person._meta.fields:
        if field.is_relation and field.related_model is User:
            return field.name
    return None


class BackofficeAccessTests(TestCase):
    """The dashboard is gated to teachers/staff; students are 404'd, anons
    bounced to login."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Test", author="tests", publication_year=2026)
        cls.user_field = _person_user_field()
        User = get_user_model()
        cls.staff = User.objects.create_user("bo-staff", password="x", is_staff=True)
        cls.teacher_user = User.objects.create_user("bo-teacher", password="x")
        cls.student_user = User.objects.create_user("bo-student", password="x")
        if cls.user_field is None:
            return
        teacher_person = Person.objects.create(display_name="Teacher")
        setattr(teacher_person, cls.user_field, cls.teacher_user)
        teacher_person.save(update_fields=[cls.user_field])
        from toto.academy.models import Teacher
        Teacher.objects.create(person=teacher_person)
        student_person = Person.objects.create(display_name="Student")
        setattr(student_person, cls.user_field, cls.student_user)
        student_person.save(update_fields=[cls.user_field])

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse("backoffice:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/sso/login", response["Location"])

    def test_student_is_404(self):
        if self.user_field is None:
            self.skipTest("Person model exposes no auth-user link")
        self.client.force_login(self.student_user)
        response = self.client.get(reverse("backoffice:dashboard"))
        self.assertEqual(response.status_code, 404)

    def test_staff_sees_dashboard(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("backoffice:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Panel autorski")

    def test_teacher_sees_dashboard(self):
        if self.user_field is None:
            self.skipTest("Person model exposes no auth-user link")
        self.client.force_login(self.teacher_user)
        response = self.client.get(reverse("backoffice:dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_dashboard_lists_live_module_cards(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("backoffice:dashboard"))
        # Every authoring module is now live and linked from the dashboard.
        self.assertContains(response, reverse("backoffice_quizzes:quiz-list"))
        self.assertContains(response, reverse("backoffice_courses:course-list"))
        self.assertContains(response, reverse("backoffice_skills:skill-overview"))
        self.assertContains(response, reverse("backoffice_notes:page-list"))
        self.assertContains(response, reverse("backoffice_library:reference-list"))


# ---------------------------------------------------------------------------
# Module: Skills & badges (competence)
# ---------------------------------------------------------------------------

class SkillsBackofficeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-skills")

    def setUp(self):
        self.client.force_login(self.staff)

    def test_group_and_badge_crud(self):
        r = self.client.post(reverse("backoffice_skills:group-create"),
                             {"title": "Algebra", "description": "", "order": "0"})
        self.assertRedirects(r, reverse("backoffice_skills:skill-overview"))
        group = SkillGroup.objects.get(title="Algebra")
        self.assertTrue(group.slug)
        self.client.post(reverse("backoffice_skills:badge-create"),
                         {"group": group.pk, "title": "Sets", "description": "",
                          "icon": "", "order": "0"})
        badge = SkillBadge.objects.get(title="Sets")
        self.assertEqual(badge.icon, "fa-solid fa-award")  # default filled in

    def test_prerequisite_edge_created(self):
        group = SkillGroup.objects.create(title="G", slug="g")
        b1 = SkillBadge.objects.create(group=group, title="B1", slug="b1")
        b2 = SkillBadge.objects.create(group=group, title="B2", slug="b2")
        self.client.post(reverse("backoffice_skills:badge-edit", kwargs={"pk": b2.pk}),
                         {"group": group.pk, "title": "B2", "description": "",
                          "icon": "fa-solid fa-award", "order": "0",
                          "prerequisites": [b1.pk]})
        self.assertTrue(SkillBadgePrerequisite.objects.filter(badge=b2, prerequisite=b1).exists())

    def test_quick_create_returns_option(self):
        group = SkillGroup.objects.create(title="G", slug="g")
        r = self.client.post(reverse("backoffice_skills:badge-quick-create"),
                             {"qc_title": "Quick", "qc_group": group.pk})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"<option", r.content)
        self.assertTrue(SkillBadge.objects.filter(title="Quick").exists())

    def test_student_is_gated(self):
        user = get_user_model().objects.create_user("plain-skills", password="x")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("backoffice_skills:skill-overview")).status_code, 404)


# ---------------------------------------------------------------------------
# Module: Courses (academy)
# ---------------------------------------------------------------------------

class CoursesBackofficeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-courses")
        cls.group = SkillGroup.objects.create(title="G", slug="g")
        cls.badge = SkillBadge.objects.create(group=cls.group, title="B", slug="b")
        cls.badge2 = SkillBadge.objects.create(group=cls.group, title="B2", slug="b2")

    def setUp(self):
        self.client.force_login(self.staff)

    def test_course_create_autoslugs_and_redirects(self):
        r = self.client.post(reverse("backoffice_courses:course-create"),
                             {"title": "Course 1", "description": "", "order": "0"})
        course = Course.objects.get(title="Course 1")
        self.assertRedirects(r, reverse("backoffice_courses:module-list", kwargs={"pk": course.pk}))
        self.assertTrue(course.slug)

    def _module_payload(self, badge, **over):
        data = {"title": "M2", "description": "", "unlocks_badge": badge.pk,
                "attached_quizzes": [], "lecture_video_url": "", "order": "0"}
        data.update(over)
        return data

    def test_module_rejects_reused_badge(self):
        course = Course.objects.create(title="C", slug="c")
        CourseModule.objects.create(course=course, title="M1", slug="m1", unlocks_badge=self.badge)
        # reusing the same badge in the same course is invalid (unique per course)
        r = self.client.post(reverse("backoffice_courses:module-create", kwargs={"pk": course.pk}),
                             self._module_payload(self.badge))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(course.modules.count(), 1)
        # a different, unused badge works
        self.client.post(reverse("backoffice_courses:module-create", kwargs={"pk": course.pk}),
                         self._module_payload(self.badge2))
        self.assertEqual(course.modules.count(), 2)

    def test_lesson_and_script(self):
        course = Course.objects.create(title="C", slug="c")
        module = CourseModule.objects.create(course=course, title="M", slug="m", unlocks_badge=self.badge)
        r = self.client.post(reverse("backoffice_courses:lesson-create", kwargs={"pk": module.pk}),
                             {"title": "L1", "summary": "hi", "order": "0", "attached_quizzes": []})
        self.assertRedirects(r, reverse("backoffice_courses:module-edit", kwargs={"pk": module.pk}))
        self.assertEqual(module.lessons.count(), 1)
        self.client.post(reverse("backoffice_courses:script-create", kwargs={"pk": module.pk}), {
            "title": "S1", "description": "",
            "sections-TOTAL_FORMS": "3", "sections-INITIAL_FORMS": "0",
            "sections-MIN_NUM_FORMS": "0", "sections-MAX_NUM_FORMS": "1000",
            "sections-0-title": "Intro", "sections-0-content": "<div>Body</div>",
            "sections-1-title": "", "sections-1-content": "",
            "sections-2-title": "", "sections-2-content": "",
        })
        self.assertEqual(module.scripts.count(), 1)
        self.assertEqual(module.scripts.first().sections.count(), 1)

    def test_module_reorder(self):
        course = Course.objects.create(title="C", slug="c")
        m1 = CourseModule.objects.create(course=course, title="M1", slug="m1", unlocks_badge=self.badge, order=1)
        m2 = CourseModule.objects.create(course=course, title="M2", slug="m2", unlocks_badge=self.badge2, order=2)
        self.client.post(reverse("backoffice_courses:module-reorder", kwargs={"pk": course.pk}),
                         {"item": m1.pk, "direction": "down"})
        m1.refresh_from_db()
        m2.refresh_from_db()
        self.assertEqual(m1.order, 2)
        self.assertEqual(m2.order, 1)


# ---------------------------------------------------------------------------
# Module: Notes (palimpsest)
# ---------------------------------------------------------------------------

class NotesBackofficeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-notes")

    def setUp(self):
        self.client.force_login(self.staff)

    def test_page_and_section(self):
        r = self.client.post(reverse("backoffice_notes:page-create"),
                             {"title": "Note 1", "slug": "", "description": "", "tags": []})
        page = Page.objects.get(title="Note 1")
        self.assertRedirects(r, reverse("backoffice_notes:page-edit", kwargs={"pk": page.pk}))
        self.client.post(reverse("backoffice_notes:section-create", kwargs={"pk": page.pk}),
                         {"title": "S", "content": "<div>hi</div>", "author": "",
                          "order": "1", "tags": []})
        self.assertEqual(page.sections.count(), 1)

    def test_student_gated_but_legacy_stays_login(self):
        user = get_user_model().objects.create_user("plain-notes", password="x")
        self.client.force_login(user)
        # the back-office notes route is teacher-gated -> 404 for a plain student
        self.assertEqual(self.client.get(reverse("backoffice_notes:page-list")).status_code, 404)


# ---------------------------------------------------------------------------
# Module: Library
# ---------------------------------------------------------------------------

class LibraryBackofficeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-library")

    def setUp(self):
        self.client.force_login(self.staff)

    def test_book_create(self):
        r = self.client.post(reverse("backoffice_library:reference-create", kwargs={"kind": "book"}),
                             {"title": "TAOCP", "authors": "Knuth", "year": "1968",
                              "doi": "", "url": "", "abstract": "", "tags": [],
                              "publisher": "AW", "edition": "1", "isbn": ""})
        self.assertRedirects(r, reverse("backoffice_library:reference-list"))
        self.assertTrue(Book.objects.filter(title="TAOCP").exists())

    def test_collection_groups_references(self):
        book = Book.objects.create(title="B")
        self.client.post(reverse("backoffice_library:collection-create"),
                         {"title": "Reading", "description": "", "books": [book.pk],
                          "articles": [], "audio": [], "videos": []})
        collection = LibraryCollection.objects.get(title="Reading")
        self.assertEqual(collection.books.count(), 1)

    def test_unknown_reference_kind_404(self):
        self.assertEqual(self.client.get("/panel/biblioteka/widget/new/").status_code, 404)
