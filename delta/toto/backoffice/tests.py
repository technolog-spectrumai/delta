import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.academy.models import (
    Certificate,
    Cohort,
    CohortMembership,
    Course,
    CourseEnrollment,
    CourseModule,
    Lesson,
    Student,
    StudentBadge,
)
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
        self.assertContains(response, reverse("backoffice_people:people-list"))
        self.assertContains(response, reverse("primula:index"))


class SheetsGateTests(TestCase):
    """/primula/ is teachers-only on delta (delta/primula_urls.py wraps every
    route in teacher_required). The app's own suite runs it ungated, so the
    gate itself is only tested here."""

    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.user_field = _person_user_field()
        User = get_user_model()
        cls.staff = User.objects.create_user("sh-staff", password="x", is_staff=True)
        cls.teacher_user = User.objects.create_user("sh-teacher", password="x")
        cls.student_user = User.objects.create_user("sh-student", password="x")
        if cls.user_field is None:
            return
        teacher_person = Person.objects.create(display_name="Sheet Teacher")
        setattr(teacher_person, cls.user_field, cls.teacher_user)
        teacher_person.save(update_fields=[cls.user_field])
        from toto.academy.models import Teacher
        Teacher.objects.create(person=teacher_person)
        student_person = Person.objects.create(display_name="Sheet Student")
        setattr(student_person, cls.user_field, cls.student_user)
        student_person.save(update_fields=[cls.user_field])

    def test_anonymous_is_bounced_to_login(self):
        response = self.client.get(reverse("primula:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/sso/login", response["Location"])

    def test_student_is_404_on_every_route_shape(self):
        if self.user_field is None:
            self.skipTest("Person model exposes no auth-user link")
        self.client.force_login(self.student_user)
        # The index, an arg-taking read route and a write route — existence
        # hidden regardless of whether the pk is real.
        for url in (reverse("primula:index"),
                    reverse("primula:versions", kwargs={"file_pk": 1}),
                    reverse("primula:save", kwargs={"file_pk": 1})):
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_teacher_reaches_the_index(self):
        if self.user_field is None:
            self.skipTest("Person model exposes no auth-user link")
        self.client.force_login(self.teacher_user)
        self.assertEqual(self.client.get(reverse("primula:index")).status_code, 200)

    def test_staff_reaches_the_index(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("primula:index")).status_code, 200)


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
            "sections-0-title": "Intro", "sections-0-content": "<div>Body</div>", "sections-0-order": "0",
            "sections-1-title": "", "sections-1-content": "", "sections-1-order": "0",
            "sections-2-title": "", "sections-2-content": "", "sections-2-order": "0",
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


# ---------------------------------------------------------------------------
# Lesson in-panel upload (video + PDF -> VaultFile)
# ---------------------------------------------------------------------------

_UPLOAD_MEDIA = tempfile.mkdtemp(prefix="delta-upload-test-")


@override_settings(MEDIA_ROOT=_UPLOAD_MEDIA)
class LessonUploadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-upload")
        cls.group = SkillGroup.objects.create(title="G", slug="gu")
        cls.badge = SkillBadge.objects.create(group=cls.group, title="B", slug="bu")
        cls.course = Course.objects.create(title="C", slug="cu")
        cls.module = CourseModule.objects.create(
            course=cls.course, title="M", slug="mu", unlocks_badge=cls.badge)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_UPLOAD_MEDIA, ignore_errors=True)

    def setUp(self):
        self.client.force_login(self.staff)

    def _create(self, **extra):
        data = {"title": "L1", "summary": "", "order": "0", "attached_quizzes": []}
        data.update(extra)
        return self.client.post(
            reverse("backoffice_courses:lesson-create", kwargs={"pk": self.module.pk}), data)

    def test_upload_video_creates_and_links_vaultfile(self):
        video = SimpleUploadedFile("lesson.mp4", b"\x00\x00fakevideo", content_type="video/mp4")
        resp = self._create(video_upload=video)
        self.assertRedirects(
            resp, reverse("backoffice_courses:module-edit", kwargs={"pk": self.module.pk}))
        lesson = self.module.lessons.get()
        self.assertIsNotNone(lesson.video_file)
        vf = lesson.video_file
        self.assertEqual(vf.file_type, "video")
        self.assertFalse(vf.is_encrypted)
        self.assertEqual(vf.owner, self.staff)
        self.assertTrue(vf.content_hash)

    def test_upload_pdf_notes(self):
        pdf = SimpleUploadedFile("notes.pdf", b"%PDF-1.4 fake", content_type="application/pdf")
        self._create(notes_upload=pdf)
        lesson = self.module.lessons.get()
        self.assertIsNotNone(lesson.notes_file)
        self.assertEqual(lesson.notes_file.file_type, "pdf")

    def test_non_video_upload_rejected(self):
        bad = SimpleUploadedFile("notes.pdf", b"%PDF fake", content_type="application/pdf")
        resp = self._create(video_upload=bad)
        self.assertEqual(resp.status_code, 200)  # re-rendered with a field error
        self.assertEqual(self.module.lessons.count(), 0)

    def test_remove_video_clears_fk(self):
        self._create(video_upload=SimpleUploadedFile("lesson.mp4", b"fake", content_type="video/mp4"))
        lesson = self.module.lessons.get()
        self.assertIsNotNone(lesson.video_file)
        self.client.post(
            reverse("backoffice_courses:lesson-edit", kwargs={"pk": lesson.pk}),
            {"title": lesson.title, "summary": "", "order": "0",
             "attached_quizzes": [], "remove_video": "on"})
        lesson.refresh_from_db()
        self.assertIsNone(lesson.video_file)


# ---------------------------------------------------------------------------
# Enrollment-gated lesson playback (video streaming + Range)
# ---------------------------------------------------------------------------

_PLAYBACK_MEDIA = tempfile.mkdtemp(prefix="delta-playback-test-")


@override_settings(MEDIA_ROOT=_PLAYBACK_MEDIA)
class LessonPlaybackTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.user_field = _person_user_field()
        User = get_user_model()
        cls.staff = User.objects.create_user("pb-staff", password="x", is_staff=True)

        group = SkillGroup.objects.create(title="G", slug="pg")
        badge = SkillBadge.objects.create(group=group, title="B", slug="pb")
        cls.course = Course.objects.create(title="PB", slug="pb-course", is_published=True)
        module = CourseModule.objects.create(
            course=cls.course, title="M", slug="pm", unlocks_badge=badge)
        cls.lesson = Lesson.objects.create(module=module, title="L", slug="pl")

        from django.core.files.base import ContentFile
        from toto.vault.models import Bucket, VaultFile
        bucket = Bucket.objects.create(name="pb", slug="pb-bucket", owner=cls.staff)
        vf = VaultFile(owner=cls.staff, title="v.mp4", bucket=bucket,
                       file_type="video", is_public=False, is_encrypted=False)
        vf.file.save("v.mp4", ContentFile(b"0123456789"), save=False)
        vf.file_size_bytes = 10
        vf.save()
        cls.lesson.video_file = vf
        cls.lesson.save(update_fields=["video_file"])

        if cls.user_field is None:
            return
        cls.enrolled_user = User.objects.create_user("pb-enrolled", password="x")
        cls.other_user = User.objects.create_user("pb-other", password="x")
        enrolled_person = Person.objects.create(display_name="Enrolled")
        setattr(enrolled_person, cls.user_field, cls.enrolled_user)
        enrolled_person.save(update_fields=[cls.user_field])
        other_person = Person.objects.create(display_name="Other")
        setattr(other_person, cls.user_field, cls.other_user)
        other_person.save(update_fields=[cls.user_field])
        student = Student.objects.create(person=enrolled_person)
        CourseEnrollment.objects.create(student=student, course=cls.course)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_PLAYBACK_MEDIA, ignore_errors=True)

    def _video_url(self):
        return reverse("academy:lesson-video", kwargs={"pk": self.lesson.pk})

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(self._video_url())
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])

    def test_staff_can_watch(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self._video_url()).status_code, 200)

    def test_non_enrolled_forbidden(self):
        if self.user_field is None:
            self.skipTest("no Person->user link")
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(self._video_url()).status_code, 403)

    def test_enrolled_can_watch(self):
        if self.user_field is None:
            self.skipTest("no Person->user link")
        self.client.force_login(self.enrolled_user)
        self.assertEqual(self.client.get(self._video_url()).status_code, 200)

    def test_range_request_returns_206(self):
        self.client.force_login(self.staff)
        resp = self.client.get(self._video_url(), HTTP_RANGE="bytes=0-3")
        self.assertEqual(resp.status_code, 206)
        self.assertEqual(resp["Content-Range"], "bytes 0-3/10")
        self.assertEqual(b"".join(resp.streaming_content), b"0123")

    def test_notes_404_when_absent(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("academy:lesson-notes", kwargs={"pk": self.lesson.pk}))
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Cohort management + enrolment
# ---------------------------------------------------------------------------

class CohortEnrolmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-roster")
        cls.course = Course.objects.create(title="RC", slug="rc")
        cls.p1 = Person.objects.create(display_name="Jan Kowalski", email="jan@example.com")
        cls.p2 = Person.objects.create(display_name="Anna Nowak", email="anna@example.com")

    def setUp(self):
        self.client.force_login(self.staff)

    def _url(self, name, pk):
        return reverse(f"backoffice_courses:{name}", kwargs={"pk": pk})

    def test_roster_gated_to_staff(self):
        user = get_user_model().objects.create_user("plain-roster", password="x")
        self.client.force_login(user)
        self.assertEqual(self.client.get(self._url("roster", self.course.pk)).status_code, 404)

    def test_enrol_creates_student_and_enrollment(self):
        resp = self.client.post(self._url("enrol-student", self.course.pk), {"person": self.p1.pk})
        self.assertRedirects(resp, self._url("roster", self.course.pk))
        student = Student.objects.get(person=self.p1)
        self.assertTrue(CourseEnrollment.objects.filter(student=student, course=self.course).exists())

    def test_unenrol_removes_enrollment(self):
        student, _created = Student.objects.get_or_create(person=self.p1)
        CourseEnrollment.objects.create(student=student, course=self.course)
        self.client.post(self._url("unenrol-student", self.course.pk), {"person": self.p1.pk})
        self.assertFalse(CourseEnrollment.objects.filter(student=student, course=self.course).exists())

    def test_people_search_matches_name(self):
        resp = self.client.get(self._url("roster", self.course.pk), {"q": "kowalski"})
        self.assertContains(resp, "Jan Kowalski")
        self.assertNotContains(resp, "Anna Nowak")

    def test_cohort_create_and_delete(self):
        resp = self.client.post(self._url("cohort-create", self.course.pk),
                                {"title": "Group A", "capacity": "", "starts_at": "",
                                 "ends_at": "", "is_active": "on"})
        cohort = Cohort.objects.get(title="Group A")
        self.assertRedirects(resp, self._url("cohort-edit", cohort.pk))
        self.assertEqual(cohort.course, self.course)
        self.assertTrue(cohort.slug)
        self.client.post(self._url("cohort-delete", cohort.pk))
        self.assertFalse(Cohort.objects.filter(pk=cohort.pk).exists())

    def test_add_member_auto_enrols_and_enforces_capacity(self):
        cohort = Cohort.objects.create(course=self.course, title="Cap", slug="cap", capacity=1)
        self.client.post(self._url("cohort-add-member", cohort.pk), {"person": self.p1.pk})
        student = Student.objects.get(person=self.p1)
        self.assertTrue(CohortMembership.objects.filter(cohort=cohort, student=student).exists())
        # auto-enrolled in the course
        self.assertTrue(CourseEnrollment.objects.filter(student=student, course=self.course).exists())
        # capacity 1 -> a second add is rejected
        self.client.post(self._url("cohort-add-member", cohort.pk), {"person": self.p2.pk})
        self.assertEqual(cohort.memberships.count(), 1)

    def test_remove_member_keeps_enrollment(self):
        cohort = Cohort.objects.create(course=self.course, title="C2", slug="c2")
        self.client.post(self._url("cohort-add-member", cohort.pk), {"person": self.p1.pk})
        student = Student.objects.get(person=self.p1)
        self.client.post(self._url("cohort-remove-member", cohort.pk), {"person": self.p1.pk})
        self.assertFalse(CohortMembership.objects.filter(cohort=cohort, student=student).exists())
        self.assertTrue(CourseEnrollment.objects.filter(student=student, course=self.course).exists())


# ---------------------------------------------------------------------------
# Completion + badge awards + certificates (per-student page)
# ---------------------------------------------------------------------------

class CompletionBadgeCertTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-progress")
        cls.group = SkillGroup.objects.create(title="G", slug="pg")
        cls.badge = SkillBadge.objects.create(group=cls.group, title="Sets", slug="sets")
        cls.other_badge = SkillBadge.objects.create(group=cls.group, title="Functions", slug="functions")
        cls.course = Course.objects.create(title="PC", slug="pc")
        cls.module = CourseModule.objects.create(
            course=cls.course, title="Algebra Basics", slug="pm", unlocks_badge=cls.badge)
        cls.person = Person.objects.create(display_name="Stud", email="stud@example.com")
        cls.student = Student.objects.create(person=cls.person)
        CourseEnrollment.objects.create(student=cls.student, course=cls.course)

    def setUp(self):
        self.client.force_login(self.staff)

    def _url(self, name):
        return reverse(f"backoffice_courses:{name}",
                       kwargs={"pk": self.course.pk, "student_pk": self.student.pk})

    def test_gate_student_404(self):
        user = get_user_model().objects.create_user("plain-progress", password="x")
        self.client.force_login(user)
        self.assertEqual(self.client.get(self._url("student-detail")).status_code, 404)

    def test_detail_renders_module_checklist(self):
        resp = self.client.get(self._url("student-detail"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Algebra Basics")

    def test_complete_sets_and_issues_certificate(self):
        self.client.post(self._url("student-complete"))
        enrollment = CourseEnrollment.objects.get(student=self.student, course=self.course)
        self.assertIsNotNone(enrollment.completed_at)
        self.assertTrue(Certificate.objects.filter(person=self.person, course=self.course).exists())

    def test_uncomplete_clears_and_removes_unsigned_cert(self):
        self.client.post(self._url("student-complete"))
        self.assertTrue(Certificate.objects.filter(person=self.person, course=self.course).exists())
        self.client.post(self._url("student-uncomplete"))
        enrollment = CourseEnrollment.objects.get(student=self.student, course=self.course)
        self.assertIsNone(enrollment.completed_at)
        self.assertFalse(Certificate.objects.filter(person=self.person, course=self.course).exists())

    def test_award_and_revoke_module_badge(self):
        self.client.post(self._url("student-award-badge"), {"badge": self.badge.pk})
        self.assertTrue(self.student.has_badge(self.badge))
        self.client.post(self._url("student-revoke-badge"), {"badge": self.badge.pk})
        self.assertFalse(self.student.has_badge(self.badge))

    def test_award_any_badge(self):
        self.client.post(self._url("student-award-badge"), {"badge": self.other_badge.pk})
        self.assertTrue(StudentBadge.objects.filter(student=self.student, badge=self.other_badge).exists())

    def test_certificate_links_shown_after_completion(self):
        self.client.post(self._url("student-complete"))
        cert = Certificate.objects.get(person=self.person, course=self.course)
        resp = self.client.get(self._url("student-detail"))
        self.assertContains(resp, reverse("academy:certificate-detail", kwargs={"uuid": cert.uuid}))
        self.assertContains(resp, reverse("academy:certificate-sign", kwargs={"uuid": cert.uuid}))


# ---------------------------------------------------------------------------
# KaTeX math rendering on student pages (Part A)
# ---------------------------------------------------------------------------

class CourseMathRenderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-math")
        group = SkillGroup.objects.create(title="G", slug="mg")
        badge = SkillBadge.objects.create(group=group, title="B", slug="mb")
        cls.course = Course.objects.create(
            title="MC", slug="mc", description="Solve $x^2$", is_published=True)
        module = CourseModule.objects.create(
            course=cls.course, title="M", slug="mm", unlocks_badge=badge,
            description="Sets $\\mathbb{R}$")
        Lesson.objects.create(module=module, title="L", slug="ml", summary="limit $x_1$")

    def test_course_detail_loads_katex_and_wraps_math(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("academy:course-detail", kwargs={"slug": self.course.slug}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "katex")      # the _katex.html include
        self.assertContains(resp, "js-math")    # math wrappers present
        self.assertContains(resp, "limit $x_1$")  # summary $…$ survives for KaTeX


# ---------------------------------------------------------------------------
# Markdown + math for script sections (Part B)
# ---------------------------------------------------------------------------

class ScriptMarkdownMathTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-scriptmd")
        group = SkillGroup.objects.create(title="G", slug="smg")
        badge = SkillBadge.objects.create(group=group, title="B", slug="smb")
        course = Course.objects.create(title="SC", slug="sc", is_published=True)
        module = CourseModule.objects.create(
            course=course, title="M", slug="smm", unlocks_badge=badge)
        from toto.academy.models import Script, ScriptSection
        cls.script = Script.objects.create(module=module, title="S1", slug="s1")
        ScriptSection.objects.create(
            page=cls.script, title="Sec", content="Solve $a*b$ and **bold**", order=1)

    def test_mdx_math_protects_latex(self):
        from markdownx.utils import markdownify
        out = markdownify("mass $a*b*c$ and $x_1$ and **bold**")
        self.assertIn("$a*b*c$", out)   # asterisks not turned into <em>
        self.assertIn("$x_1$", out)
        self.assertIn("<strong>bold</strong>", out)

    def test_script_form_uses_markdownx_widget(self):
        from toto.academy.backoffice_forms import ScriptSectionForm
        from markdownx.widgets import MarkdownxWidget
        self.assertIsInstance(ScriptSectionForm().fields["content"].widget, MarkdownxWidget)

    def test_script_detail_renders_markdown_and_loads_katex(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("academy:script-detail", kwargs={"pk": self.script.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "<strong>bold</strong>", html=False)  # markdown rendered
        self.assertContains(resp, "$a*b$")   # math delimiter survives for KaTeX
        self.assertContains(resp, "katex")   # _katex.html include present


# ---------------------------------------------------------------------------
# Live math preview on plain-text editor fields (Part C)
# ---------------------------------------------------------------------------

class MathPreviewEditorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-mathprev")
        group = SkillGroup.objects.create(title="G", slug="mpg")
        badge = SkillBadge.objects.create(group=group, title="B", slug="mpb")
        cls.course = Course.objects.create(title="C", slug="mpc")
        cls.module = CourseModule.objects.create(
            course=cls.course, title="M", slug="mpm", unlocks_badge=badge)

    def setUp(self):
        self.client.force_login(self.staff)

    def test_course_form_wires_math_preview(self):
        r = self.client.get(reverse("backoffice_courses:course-create"))
        self.assertContains(r, "js-mathsource")   # description tagged
        self.assertContains(r, "mathpreview.js")   # preview script loaded
        self.assertContains(r, "katex")            # KaTeX include

    def test_lesson_form_wires_math_preview(self):
        r = self.client.get(reverse("backoffice_courses:lesson-create", kwargs={"pk": self.module.pk}))
        self.assertContains(r, "js-mathsource")
        self.assertContains(r, "mathpreview.js")

    def test_module_form_wires_math_preview(self):
        r = self.client.get(reverse("backoffice_courses:module-edit", kwargs={"pk": self.module.pk}))
        self.assertContains(r, "js-mathsource")
        self.assertContains(r, "mathpreview.js")

    def test_quiz_form_tags_description(self):
        r = self.client.get(reverse("backoffice_quizzes:quiz-create"))
        self.assertContains(r, "js-mathsource")


# ---------------------------------------------------------------------------
# Drag-and-drop reorder — modules / lessons / questions (Part 1)
# ---------------------------------------------------------------------------

class ReorderDragDropTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-reorder")
        group = SkillGroup.objects.create(title="G", slug="rog")
        b1 = SkillBadge.objects.create(group=group, title="B1", slug="rob1")
        b2 = SkillBadge.objects.create(group=group, title="B2", slug="rob2")
        b3 = SkillBadge.objects.create(group=group, title="B3", slug="rob3")
        cls.course = Course.objects.create(title="C", slug="roc")
        cls.m1 = CourseModule.objects.create(course=cls.course, title="M1", slug="rom1", unlocks_badge=b1, order=1)
        cls.m2 = CourseModule.objects.create(course=cls.course, title="M2", slug="rom2", unlocks_badge=b2, order=2)
        cls.m3 = CourseModule.objects.create(course=cls.course, title="M3", slug="rom3", unlocks_badge=b3, order=3)

    def setUp(self):
        self.client.force_login(self.staff)

    def test_module_reorder_bulk_order_list(self):
        url = reverse("backoffice_courses:module-reorder", kwargs={"pk": self.course.pk})
        r = self.client.post(url, {"order": [self.m3.pk, self.m1.pk, self.m2.pk]})
        self.assertEqual(r.status_code, 204)
        self.m1.refresh_from_db(); self.m2.refresh_from_db(); self.m3.refresh_from_db()
        self.assertEqual((self.m3.order, self.m1.order, self.m2.order), (1, 2, 3))

    def test_module_reorder_up_down_fallback(self):
        url = reverse("backoffice_courses:module-reorder", kwargs={"pk": self.course.pk})
        r = self.client.post(url, {"item": self.m1.pk, "direction": "down"})
        self.assertEqual(r.status_code, 302)  # form fallback redirects
        self.m1.refresh_from_db(); self.m2.refresh_from_db()
        self.assertEqual(self.m1.order, 2)
        self.assertEqual(self.m2.order, 1)

    def test_lesson_reorder_bulk(self):
        l1 = Lesson.objects.create(module=self.m1, title="L1", slug="rol1", order=1)
        l2 = Lesson.objects.create(module=self.m1, title="L2", slug="rol2", order=2)
        url = reverse("backoffice_courses:lesson-reorder", kwargs={"pk": self.m1.pk})
        r = self.client.post(url, {"order": [l2.pk, l1.pk]})
        self.assertEqual(r.status_code, 204)
        l1.refresh_from_db(); l2.refresh_from_db()
        self.assertEqual(l2.order, 1)
        self.assertEqual(l1.order, 2)

    def test_question_reorder_bulk(self):
        from toto.quizzes.models import Quiz, QuizQuestion
        quiz = Quiz.objects.create(title="Q", slug="roq")
        q1 = QuizQuestion.objects.create(quiz=quiz, text="q1", order=1)
        q2 = QuizQuestion.objects.create(quiz=quiz, text="q2", order=2)
        url = reverse("backoffice_quizzes:question-reorder", kwargs={"pk": quiz.pk})
        r = self.client.post(url, {"order": [q2.pk, q1.pk]})
        self.assertEqual(r.status_code, 204)
        q1.refresh_from_db(); q2.refresh_from_db()
        self.assertEqual(q2.order, 1)
        self.assertEqual(q1.order, 2)

    def test_module_list_loads_dnd_assets(self):
        r = self.client.get(reverse("backoffice_courses:module-list", kwargs={"pk": self.course.pk}))
        self.assertContains(r, "Sortable.min.js")
        self.assertContains(r, "reorder.js")
        self.assertContains(r, "data-reorder-id")


# ---------------------------------------------------------------------------
# Script section reorder (Part 2 — formset field mode)
# ---------------------------------------------------------------------------

class ScriptSectionReorderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-secreorder")
        group = SkillGroup.objects.create(title="G", slug="srg")
        badge = SkillBadge.objects.create(group=group, title="B", slug="srb")
        course = Course.objects.create(title="C", slug="src")
        module = CourseModule.objects.create(course=course, title="M", slug="srm", unlocks_badge=badge)
        from toto.academy.models import Script, ScriptSection
        cls.script = Script.objects.create(module=module, title="S", slug="srs")
        cls.s1 = ScriptSection.objects.create(page=cls.script, title="A", content="a", order=1)
        cls.s2 = ScriptSection.objects.create(page=cls.script, title="B", content="b", order=2)

    def setUp(self):
        self.client.force_login(self.staff)

    def test_saving_reordered_sections_persists_order(self):
        data = {
            "title": "S", "description": "",
            "sections-TOTAL_FORMS": "2", "sections-INITIAL_FORMS": "2",
            "sections-MIN_NUM_FORMS": "0", "sections-MAX_NUM_FORMS": "1000",
            "sections-0-id": str(self.s1.pk), "sections-0-title": "A",
            "sections-0-content": "a", "sections-0-order": "2",
            "sections-1-id": str(self.s2.pk), "sections-1-title": "B",
            "sections-1-content": "b", "sections-1-order": "1",
        }
        r = self.client.post(reverse("backoffice_courses:script-edit", kwargs={"pk": self.script.pk}), data)
        self.assertEqual(r.status_code, 302)
        self.s1.refresh_from_db(); self.s2.refresh_from_db()
        self.assertEqual(self.s2.order, 1)  # dragged to first
        self.assertEqual(self.s1.order, 2)

    def test_script_editor_loads_reorder_assets(self):
        r = self.client.get(reverse("backoffice_courses:script-edit", kwargs={"pk": self.script.pk}))
        self.assertContains(r, "data-reorder-fields")
        self.assertContains(r, "Sortable.min.js")


# ---------------------------------------------------------------------------
# Skill group / badge reorder (Part 3)
# ---------------------------------------------------------------------------

class SkillReorderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-skreorder")
        cls.g1 = SkillGroup.objects.create(title="G1", slug="skg1", order=1)
        cls.g2 = SkillGroup.objects.create(title="G2", slug="skg2", order=2)
        cls.g3 = SkillGroup.objects.create(title="G3", slug="skg3", order=3)
        cls.b1 = SkillBadge.objects.create(group=cls.g1, title="B1", slug="skb1", order=1)
        cls.b2 = SkillBadge.objects.create(group=cls.g1, title="B2", slug="skb2", order=2)
        cls.b3 = SkillBadge.objects.create(group=cls.g1, title="B3", slug="skb3", order=3)
        # a badge in another group must stay untouched by g1's badge reorder
        cls.other = SkillBadge.objects.create(group=cls.g2, title="X", slug="skbx", order=1)

    def setUp(self):
        self.client.force_login(self.staff)

    def test_group_reorder_bulk_order_list(self):
        url = reverse("backoffice_skills:group-reorder")
        r = self.client.post(url, {"order": [self.g3.pk, self.g1.pk, self.g2.pk]})
        self.assertEqual(r.status_code, 204)
        self.g1.refresh_from_db(); self.g2.refresh_from_db(); self.g3.refresh_from_db()
        self.assertEqual((self.g3.order, self.g1.order, self.g2.order), (1, 2, 3))

    def test_group_reorder_up_down_fallback(self):
        url = reverse("backoffice_skills:group-reorder")
        r = self.client.post(url, {"item": self.g1.pk, "direction": "down"})
        self.assertEqual(r.status_code, 302)  # form fallback redirects
        self.g1.refresh_from_db(); self.g2.refresh_from_db()
        self.assertEqual(self.g1.order, 2)
        self.assertEqual(self.g2.order, 1)

    def test_badge_reorder_bulk_scoped_to_group(self):
        url = reverse("backoffice_skills:badge-reorder", kwargs={"pk": self.g1.pk})
        r = self.client.post(url, {"order": [self.b3.pk, self.b1.pk, self.b2.pk]})
        self.assertEqual(r.status_code, 204)
        self.b1.refresh_from_db(); self.b2.refresh_from_db(); self.b3.refresh_from_db()
        self.assertEqual((self.b3.order, self.b1.order, self.b2.order), (1, 2, 3))
        self.other.refresh_from_db()
        self.assertEqual(self.other.order, 1)  # other group left untouched

    def test_get_is_405(self):
        self.assertEqual(
            self.client.get(reverse("backoffice_skills:group-reorder")).status_code, 405)

    def test_student_is_gated(self):
        user = get_user_model().objects.create_user("plain-skreorder", password="x")
        self.client.force_login(user)
        r = self.client.post(reverse("backoffice_skills:group-reorder"), {"order": [self.g1.pk]})
        self.assertEqual(r.status_code, 404)

    def test_overview_loads_reorder_assets(self):
        r = self.client.get(reverse("backoffice_skills:skill-overview"))
        self.assertContains(r, "Sortable.min.js")
        self.assertContains(r, "data-reorder-id")
        self.assertContains(r, reverse("backoffice_skills:group-reorder"))


# ---------------------------------------------------------------------------
# Cohort batch certificate awarding
# ---------------------------------------------------------------------------

class CohortBatchCertTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-batchcert")
        cls.course = Course.objects.create(title="BC", slug="bc")
        cls.cohort = Cohort.objects.create(course=cls.course, title="Group A", slug="ga")
        cls.p1 = Person.objects.create(display_name="Jan Kowalski", email="jan@example.com")
        cls.p2 = Person.objects.create(display_name="Anna Nowak", email="anna@example.com")
        cls.s1 = Student.objects.create(person=cls.p1)
        cls.s2 = Student.objects.create(person=cls.p2)
        for s in (cls.s1, cls.s2):
            CourseEnrollment.objects.create(student=s, course=cls.course)
            CohortMembership.objects.create(cohort=cls.cohort, student=s)

    def setUp(self):
        self.client.force_login(self.staff)

    def _url(self, name, pk):
        return reverse(f"backoffice_courses:{name}", kwargs={"pk": pk})

    def test_batch_gated_to_staff(self):
        user = get_user_model().objects.create_user("plain-batchcert", password="x")
        self.client.force_login(user)
        self.assertEqual(
            self.client.post(self._url("cohort-award-certificates", self.cohort.pk)).status_code, 404)

    def test_batch_award_completes_and_issues_certs(self):
        resp = self.client.post(self._url("cohort-award-certificates", self.cohort.pk))
        self.assertRedirects(resp, self._url("cohort-edit", self.cohort.pk))
        for person, student in ((self.p1, self.s1), (self.p2, self.s2)):
            enrollment = CourseEnrollment.objects.get(student=student, course=self.course)
            self.assertIsNotNone(enrollment.completed_at)
            self.assertTrue(Certificate.objects.filter(person=person, course=self.course).exists())

    def test_batch_is_idempotent_and_preserves_completion_date(self):
        from datetime import timedelta

        from django.utils import timezone
        earlier = timezone.now() - timedelta(days=3)
        e1 = CourseEnrollment.objects.get(student=self.s1, course=self.course)
        e1.completed_at = earlier
        e1.save()  # issues p1's certificate now
        self.client.post(self._url("cohort-award-certificates", self.cohort.pk))
        e1.refresh_from_db()
        self.assertEqual(e1.completed_at, earlier)  # already-complete member untouched
        self.assertEqual(Certificate.objects.filter(course=self.course).count(), 2)  # no duplicates

    def test_award_button_rendered_on_editor_and_roster(self):
        award = self._url("cohort-award-certificates", self.cohort.pk)
        self.assertContains(self.client.get(self._url("cohort-edit", self.cohort.pk)), award)
        self.assertContains(self.client.get(self._url("roster", self.course.pk)), award)


# ---------------------------------------------------------------------------
# Promote / demote teachers ("People" module)
# ---------------------------------------------------------------------------

class PromoteToTeacherTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-promote")
        cls.user_field = _person_user_field()
        cls.person = Person.objects.create(display_name="Nowy Nauczyciel", email="nn@example.com")
        if cls.user_field is not None:
            cls.promo_user = get_user_model().objects.create_user("promo", password="x")
            setattr(cls.person, cls.user_field, cls.promo_user)
            cls.person.save(update_fields=[cls.user_field])

    def setUp(self):
        self.client.force_login(self.staff)

    def _url(self, name, **kw):
        return reverse(f"backoffice_people:{name}", kwargs=kw)

    def test_gated_to_staff(self):
        user = get_user_model().objects.create_user("plain-promote", password="x")
        self.client.force_login(user)
        self.assertEqual(self.client.get(self._url("people-list")).status_code, 404)
        self.assertEqual(
            self.client.post(self._url("promote-teacher", pk=self.person.pk)).status_code, 404)

    def test_promote_creates_active_teacher(self):
        from toto.academy.models import Teacher
        resp = self.client.post(self._url("promote-teacher", pk=self.person.pk))
        self.assertRedirects(resp, self._url("people-list"))
        self.assertTrue(Teacher.objects.get(person=self.person).is_active)

    def test_promote_is_idempotent(self):
        from toto.academy.models import Teacher
        self.client.post(self._url("promote-teacher", pk=self.person.pk))
        self.client.post(self._url("promote-teacher", pk=self.person.pk))
        self.assertEqual(Teacher.objects.filter(person=self.person).count(), 1)

    def test_demote_soft_deactivates_and_preserves_row(self):
        from toto.academy.models import Teacher
        self.client.post(self._url("promote-teacher", pk=self.person.pk))
        self.client.post(self._url("demote-teacher", pk=self.person.pk))
        self.assertFalse(Teacher.objects.get(person=self.person).is_active)  # row kept

    def test_promote_after_demote_reactivates(self):
        from toto.academy.models import Teacher
        self.client.post(self._url("promote-teacher", pk=self.person.pk))
        self.client.post(self._url("demote-teacher", pk=self.person.pk))
        self.client.post(self._url("promote-teacher", pk=self.person.pk))
        self.assertTrue(Teacher.objects.get(person=self.person).is_active)

    def test_soft_demote_closes_the_gate(self):
        if self.user_field is None:
            self.skipTest("Person model exposes no auth-user link")
        from toto.academy.models import Teacher
        Teacher.objects.create(person=self.person, is_active=True)
        self.client.force_login(self.promo_user)
        self.assertEqual(self.client.get(reverse("backoffice:dashboard")).status_code, 200)
        Teacher.objects.filter(person=self.person).update(is_active=False)
        self.assertEqual(self.client.get(reverse("backoffice:dashboard")).status_code, 404)

    def test_people_list_shows_teacher(self):
        self.client.post(self._url("promote-teacher", pk=self.person.pk))
        self.assertContains(self.client.get(self._url("people-list")), "Nowy Nauczyciel")


# ---------------------------------------------------------------------------
# Soft-demote must revoke teacher powers gated outside the backoffice too
# (certificate signing + course metrics), not just /panel/ access.
# ---------------------------------------------------------------------------

class SoftDemoteRevokesTeacherPowersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.user_field = _person_user_field()
        cls.course = Course.objects.create(title="SC", slug="sc", is_published=True)
        grad = Person.objects.create(display_name="Grad", email="grad@example.com")
        cls.cert = Certificate.objects.create(person=grad, course=cls.course)

    def setUp(self):
        if self.user_field is None:
            self.skipTest("Person model exposes no auth-user link")
        from toto.academy.models import Teacher
        self.user = get_user_model().objects.create_user("demoted", password="x")
        self.person = Person.objects.create(display_name="Demoted", email="dt@example.com")
        setattr(self.person, self.user_field, self.user)
        self.person.save(update_fields=[self.user_field])
        Teacher.objects.create(person=self.person, is_active=True)
        self.client.force_login(self.user)

    def _deactivate(self):
        from toto.academy.models import Teacher
        Teacher.objects.filter(person=self.person).update(is_active=False)

    def test_metrics_allowed_while_active_then_404_after_demote(self):
        url = reverse("academy:course-metrics", kwargs={"slug": self.course.slug})
        self.assertEqual(self.client.get(url).status_code, 200)
        self._deactivate()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_demoted_teacher_cannot_sign_certificate(self):
        self._deactivate()
        url = reverse("academy:certificate-sign", kwargs={"uuid": self.cert.uuid})
        resp = self.client.post(url, {"strongbox_password": "x"})
        self.assertEqual(resp.status_code, 302)  # bounced to detail with an error
        self.cert.refresh_from_db()
        self.assertEqual(self.cert.cryptographic_signature, "")

    def test_metrics_link_hidden_for_demoted_teacher(self):
        course_url = reverse("academy:course-detail", kwargs={"slug": self.course.slug})
        metrics_url = reverse("academy:course-metrics", kwargs={"slug": self.course.slug})
        self.assertContains(self.client.get(course_url), metrics_url)  # active: link shown
        self._deactivate()
        self.assertNotContains(self.client.get(course_url), metrics_url)


# ---------------------------------------------------------------------------
# Welcome-page copy editor (backoffice_welcome)
# ---------------------------------------------------------------------------

class WelcomeEditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-welcome")

    def setUp(self):
        self.client.force_login(self.staff)

    def test_staff_can_open_editor(self):
        self.assertEqual(
            self.client.get(reverse("backoffice_welcome:welcome-edit")).status_code, 200)

    def test_post_updates_copy(self):
        from toto.academy.models import WelcomeCopy
        resp = self.client.post(reverse("backoffice_welcome:welcome-edit"), {
            "headline_pl": "", "headline_en": "New headline",
            "subtitle_pl": "", "subtitle_en": "",
            "invitation_pl": "", "invitation_en": "",
            "student_cta_pl": "", "student_cta_en": "",
            "teacher_cta_pl": "", "teacher_cta_en": "",
        })
        self.assertRedirects(resp, reverse("backoffice_welcome:welcome-edit"))
        self.assertEqual(WelcomeCopy.current().headline_en, "New headline")

    def test_student_is_gated(self):
        user = get_user_model().objects.create_user("plain-welcome", password="x")
        self.client.force_login(user)
        self.assertEqual(
            self.client.get(reverse("backoffice_welcome:welcome-edit")).status_code, 404)

    def test_dashboard_shows_welcome_card(self):
        self.assertContains(
            self.client.get(reverse("backoffice:dashboard")),
            reverse("backoffice_welcome:welcome-edit"))


# ---------------------------------------------------------------------------
# Role-aware post-login landing (RoleAwareLandingMiddleware)
# ---------------------------------------------------------------------------

class RoleAwareLandingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.user_field = _person_user_field()
        cls.staff = _staff("rl-staff")
        cls.teacher_user = get_user_model().objects.create_user("rl-teacher", password="x")
        cls.student_user = get_user_model().objects.create_user("rl-student", password="x")
        if cls.user_field:
            from toto.academy.models import Teacher
            tp = Person.objects.create(display_name="RL Teacher")
            setattr(tp, cls.user_field, cls.teacher_user)
            tp.save(update_fields=[cls.user_field])
            Teacher.objects.create(person=tp, is_active=True)
            sp = Person.objects.create(display_name="RL Student")
            setattr(sp, cls.user_field, cls.student_user)
            sp.save(update_fields=[cls.user_field])

    def _login_pending(self, user):
        """force_login + set the one-shot flag the login signal would set."""
        self.client.force_login(user)
        session = self.client.session
        session["role_landing_pending"] = True
        session.save()

    def test_teacher_routed_to_panel(self):
        if not self.user_field:
            self.skipTest("Person model exposes no auth-user link")
        self._login_pending(self.teacher_user)
        self.assertRedirects(
            self.client.get(reverse("core:dashboard")), reverse("backoffice:dashboard"))

    def test_student_routed_to_student_home(self):
        if not self.user_field:
            self.skipTest("Person model exposes no auth-user link")
        self._login_pending(self.student_user)
        self.assertRedirects(
            self.client.get(reverse("core:dashboard")), reverse("academy:student-home"))

    def test_staff_keeps_generic_dashboard(self):
        self._login_pending(self.staff)
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 200)

    def test_landing_is_one_shot(self):
        if not self.user_field:
            self.skipTest("Person model exposes no auth-user link")
        self._login_pending(self.student_user)
        self.client.get(reverse("core:dashboard"))  # consumes flag + redirects
        # second visit has no flag -> the generic dashboard renders normally
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 200)

    def test_no_redirect_without_flag(self):
        if not self.user_field:
            self.skipTest("Person model exposes no auth-user link")
        self.client.force_login(self.student_user)
        session = self.client.session  # force_login arms the flag; clear it
        session.pop("role_landing_pending", None)
        session.save()
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 200)


# ---------------------------------------------------------------------------
# Slide-presentation editor (backoffice_courses:presentation-edit)
# ---------------------------------------------------------------------------

class PresentationEditorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-pres")
        group = SkillGroup.objects.create(title="G", slug="pg", order=1)
        badge = SkillBadge.objects.create(group=group, title="B", slug="pb", order=1)
        course = Course.objects.create(title="C", slug="pc")
        module = CourseModule.objects.create(
            course=course, unlocks_badge=badge, title="M", slug="pm", order=1)
        cls.lesson = Lesson.objects.create(module=module, title="L", slug="pl", order=1)

    def setUp(self):
        self.client.force_login(self.staff)

    def _url(self):
        return reverse("backoffice_courses:presentation-edit", kwargs={"pk": self.lesson.pk})

    def test_opening_creates_the_deck_and_hands_over_to_memo(self):
        # The panel's job shrank to plumbing: make the .pml exist, then open
        # memo's editor -- the same editor every other deck on the platform uses.
        from toto.academy.models import Lesson
        from toto.memo import presentation_format as pf

        r = self.client.get(self._url())
        lesson = Lesson.objects.get(pk=self.lesson.pk)
        self.assertIsNotNone(lesson.presentation_file)
        vf = lesson.presentation_file
        self.assertEqual(vf.file_type, "presentation")
        self.assertEqual(vf.owner, self.staff)
        self.assertRedirects(r, reverse("memo:edit", args=[vf.pk]),
                             fetch_redirect_response=False)
        with vf.file.storage.open(vf.file.name, "rb") as fh:
            deck = pf.loads(fh.read().decode())
        self.assertEqual(deck.title, "L")     # seeded with the lesson's title

    def test_a_second_visit_reuses_the_same_deck(self):
        from toto.academy.models import Lesson
        self.client.get(self._url())
        first = Lesson.objects.get(pk=self.lesson.pk).presentation_file_id
        self.client.get(self._url())
        self.assertEqual(
            Lesson.objects.get(pk=self.lesson.pk).presentation_file_id, first)

    def test_another_teachers_deck_is_not_editable_here(self):
        # memo's editor is owner-only; the panel says who owns it instead of
        # bouncing the second teacher off a 404 that looks like a broken button.
        self.client.get(self._url())                     # staff now owns it
        other = _staff("bo-pres-2")
        self.client.force_login(other)
        r = self.client.get(self._url(), follow=True)
        self.assertRedirects(r, reverse("backoffice_courses:lesson-edit",
                                        kwargs={"pk": self.lesson.pk}))
        self.assertContains(r, "bo-pres")                # the owner is named

    def test_student_is_gated(self):
        user = get_user_model().objects.create_user("plain-pres", password="x")
        self.client.force_login(user)
        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_lesson_editor_shows_presentation_link(self):
        r = self.client.get(reverse("backoffice_courses:lesson-edit", kwargs={"pk": self.lesson.pk}))
        self.assertContains(r, self._url())


class NotesDocumentEditorTests(TestCase):
    """The notes button: cyprian's writer over a file created on demand."""

    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = _staff("bo-notes")
        group = SkillGroup.objects.create(title="G", slug="ng", order=1)
        badge = SkillBadge.objects.create(group=group, title="B", slug="nb", order=1)
        course = Course.objects.create(title="C", slug="nc")
        module = CourseModule.objects.create(
            course=course, unlocks_badge=badge, title="M", slug="nm", order=1)
        cls.lesson = Lesson.objects.create(module=module, title="Granice", slug="nl", order=1)

    def setUp(self):
        self.client.force_login(self.staff)

    def _url(self):
        return reverse("backoffice_courses:notes-document-edit", kwargs={"pk": self.lesson.pk})

    def test_opening_creates_the_document_and_hands_over_to_cyprian(self):
        from toto.academy.models import Lesson
        from toto.cyprian import document_format as df

        r = self.client.get(self._url())
        lesson = Lesson.objects.get(pk=self.lesson.pk)
        self.assertIsNotNone(lesson.notes_document)
        vf = lesson.notes_document
        self.assertEqual(vf.file_type, "document")
        self.assertEqual(vf.owner, self.staff)
        self.assertRedirects(r, reverse("cyprian:edit", args=[vf.pk]),
                             fetch_redirect_response=False)
        with vf.file.storage.open(vf.file.name, "rb") as fh:
            doc = df.loads(fh.read().decode())
        self.assertEqual(doc.title, "Granice")   # seeded with the lesson's title

    def test_a_second_visit_reuses_the_same_document(self):
        from toto.academy.models import Lesson
        self.client.get(self._url())
        first = Lesson.objects.get(pk=self.lesson.pk).notes_document_id
        self.client.get(self._url())
        self.assertEqual(
            Lesson.objects.get(pk=self.lesson.pk).notes_document_id, first)

    def test_another_teachers_document_is_not_editable_here(self):
        self.client.get(self._url())                     # staff now owns it
        other = _staff("bo-notes-2")
        self.client.force_login(other)
        r = self.client.get(self._url(), follow=True)
        self.assertRedirects(r, reverse("backoffice_courses:lesson-edit",
                                        kwargs={"pk": self.lesson.pk}))
        self.assertContains(r, "bo-notes")               # the owner is named

    def test_student_is_gated(self):
        user = get_user_model().objects.create_user("plain-notes", password="x")
        self.client.force_login(user)
        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_lesson_editor_shows_the_notes_button(self):
        r = self.client.get(reverse("backoffice_courses:lesson-edit",
                                    kwargs={"pk": self.lesson.pk}))
        self.assertContains(r, self._url())
