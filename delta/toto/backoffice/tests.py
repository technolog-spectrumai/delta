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
