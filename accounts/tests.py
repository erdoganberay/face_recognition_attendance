import base64
import json
import numpy as np

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch, MagicMock

from .models import Teacher, Student, Course, ClassSession, Attendance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_teacher(username='teacher1', staff_id='T001'):
    """Create a User + Teacher and return the Teacher instance."""
    user = User.objects.create_user(username=username, password='pass')
    return Teacher.objects.create(user=user, staff_id=staff_id, department='CS')


def make_student(username='student1', student_id='S001', with_encoding=True):
    """Create a User + Student.  Optionally store a fake 128-d face encoding."""
    user = User.objects.create_user(
        username=username,
        password='pass',
        first_name='John',
        last_name='Doe',
    )
    student = Student(
        user=user,
        student_id=student_id,
        date_of_birth='2000-01-01',
    )
    if with_encoding:
        # 128 zeros — valid shape for face_recognition, content doesn't matter
        # because the library is mocked in every test that reaches this code.
        student.set_encoding(np.zeros(128))
    student.save()
    return student


# A minimal valid base64 image string.
# The view does `header, encoded = image_data.split(',', 1)` then
# `base64.b64decode(encoded)`.  The actual bytes are never used because
# face_recognition.load_image_file is mocked, so a single byte is enough.
FAKE_IMAGE = 'data:image/jpeg;base64,' + base64.b64encode(b'x').decode()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class RecognizeFaceViewTests(TestCase):
    """
    Tests for the recognize_face view (accounts/views.py).

    The face_recognition library is mocked in every test that needs it so
    that tests run without real images and without the native C extension.
    """

    def setUp(self):
        """
        setUp runs before *every* test method.
        Django rolls the database back after each test, so each test starts
        with a clean slate.
        """
        teacher = make_teacher()
        course = Course.objects.create(code='CS101', name='Intro to CS', teacher=teacher)
        self.session = ClassSession.objects.create(course=course, is_active=True)

        self.student = make_student()
        course.students.add(self.student)

        self.url = '/accounts/recognize/'

    # ------------------------------------------------------------------
    # 1. Wrong HTTP method
    # ------------------------------------------------------------------

    def test_get_returns_405(self):
        """
        The view only accepts POST.  Any other method should return 405.
        No mock needed — the method check happens before any image processing.
        """
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()['status'], 'error')

    # ------------------------------------------------------------------
    # 2. Session lookup
    # ------------------------------------------------------------------

    def test_inactive_session_returns_error(self):
        """
        If the session_id doesn't match an *active* session the view should
        return an error without touching face_recognition at all.
        """
        response = self.client.post(
            self.url,
            data=json.dumps({'image': FAKE_IMAGE, 'session_id': 99999}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'error')
        self.assertIn('No active session', response.json()['message'])

    # ------------------------------------------------------------------
    # 3. No face in the frame
    # ------------------------------------------------------------------

    @patch('accounts.views.face_recognition')
    def test_no_face_detected(self, mock_fr):
        """
        When face_recognition.face_locations returns an empty list the view
        should respond with status 'no_face'.

        @patch replaces the `face_recognition` name inside accounts/views.py
        with a MagicMock for the duration of this test only.
        mock_fr is passed in as the second argument automatically by @patch.
        """
        mock_fr.load_image_file.return_value = MagicMock()
        mock_fr.face_locations.return_value = []   # ← pretend camera sees nothing

        response = self.client.post(
            self.url,
            data=json.dumps({'image': FAKE_IMAGE, 'session_id': self.session.id}),
            content_type='application/json',
        )

        self.assertEqual(response.json()['status'], 'no_face')

    # ------------------------------------------------------------------
    # 4. Face detected but no enrolled students have encodings
    # ------------------------------------------------------------------

    @patch('accounts.views.face_recognition')
    def test_no_enrolled_students_with_encodings(self, mock_fr):
        """
        If a face is detected but none of the enrolled students have a stored
        encoding the view should return an error rather than crash.
        """
        # Remove the encoding so the view's .exclude(face_encoding=None) skips
        # this student, leaving known_encodings empty.
        self.student.face_encoding = None
        self.student.save()

        mock_fr.load_image_file.return_value = MagicMock()
        mock_fr.face_locations.return_value = [(0, 100, 100, 0)]   # one face found
        mock_fr.face_encodings.return_value = [np.zeros(128)]

        response = self.client.post(
            self.url,
            data=json.dumps({'image': FAKE_IMAGE, 'session_id': self.session.id}),
            content_type='application/json',
        )

        self.assertEqual(response.json()['status'], 'error')
        self.assertIn('No enrolled students', response.json()['message'])

    # ------------------------------------------------------------------
    # 5. Face detected but doesn't match any student
    # ------------------------------------------------------------------

    @patch('accounts.views.face_recognition')
    def test_face_not_recognized(self, mock_fr):
        """
        compare_faces returns [False] (distance too high) → status 'unknown'.

        face_distance returns [0.6] — above the 0.5 tolerance used in the
        view — so the best match is still rejected.
        """
        mock_fr.load_image_file.return_value = MagicMock()
        mock_fr.face_locations.return_value = [(0, 100, 100, 0)]
        mock_fr.face_encodings.return_value = [np.zeros(128)]
        mock_fr.compare_faces.return_value = [False]
        mock_fr.face_distance.return_value = np.array([0.6])

        response = self.client.post(
            self.url,
            data=json.dumps({'image': FAKE_IMAGE, 'session_id': self.session.id}),
            content_type='application/json',
        )

        self.assertEqual(response.json()['status'], 'unknown')

    # ------------------------------------------------------------------
    # 6. Face recognised — first time (creates Attendance row)
    # ------------------------------------------------------------------

    @patch('accounts.views.face_recognition')
    def test_face_recognized_creates_attendance(self, mock_fr):
        """
        compare_faces returns [True] → status 'recognized', already_marked False,
        and a new Attendance row should exist in the database.
        """
        mock_fr.load_image_file.return_value = MagicMock()
        mock_fr.face_locations.return_value = [(0, 100, 100, 0)]
        mock_fr.face_encodings.return_value = [np.zeros(128)]
        mock_fr.compare_faces.return_value = [True]
        mock_fr.face_distance.return_value = np.array([0.3])

        response = self.client.post(
            self.url,
            data=json.dumps({'image': FAKE_IMAGE, 'session_id': self.session.id}),
            content_type='application/json',
        )

        data = response.json()
        self.assertEqual(data['status'], 'recognized')
        self.assertEqual(data['student_id'], self.student.student_id)
        self.assertFalse(data['already_marked'])

        # Verify the Attendance row was actually written to the database.
        self.assertTrue(
            Attendance.objects.filter(session=self.session, student=self.student).exists()
        )

    # ------------------------------------------------------------------
    # 7. Face recognised — duplicate (Attendance already exists)
    # ------------------------------------------------------------------

    @patch('accounts.views.face_recognition')
    def test_face_recognized_already_marked(self, mock_fr):
        """
        If the student was already marked present the view should still return
        'recognized' but with already_marked = True.
        """
        # Pre-create the attendance record before the request.
        Attendance.objects.create(session=self.session, student=self.student)

        mock_fr.load_image_file.return_value = MagicMock()
        mock_fr.face_locations.return_value = [(0, 100, 100, 0)]
        mock_fr.face_encodings.return_value = [np.zeros(128)]
        mock_fr.compare_faces.return_value = [True]
        mock_fr.face_distance.return_value = np.array([0.3])

        response = self.client.post(
            self.url,
            data=json.dumps({'image': FAKE_IMAGE, 'session_id': self.session.id}),
            content_type='application/json',
        )

        data = response.json()
        self.assertEqual(data['status'], 'recognized')
        self.assertTrue(data['already_marked'])

        # Should still be exactly one Attendance row (no duplicate created).
        self.assertEqual(
            Attendance.objects.filter(session=self.session, student=self.student).count(),
            1,
        )


class ManualAttendanceViewTests(TestCase):
    """
    Tests for ManualAttendanceView — the endpoint teachers use to manually
    mark or unmark a student when face recognition fails.

    URL: POST /accounts/session/<session_id>/manual/
    """

    def setUp(self):
        self.teacher = make_teacher()
        self.course = Course.objects.create(code='CS101', name='Intro', teacher=self.teacher)
        self.session = ClassSession.objects.create(course=self.course, is_active=True)
        self.student = make_student()
        self.course.students.add(self.student)
        self.url = reverse('manual_attendance', args=[self.session.id])

    # ------------------------------------------------------------------
    # 1. Authentication
    # ------------------------------------------------------------------

    def test_unauthenticated_redirects_to_login(self):
        """
        A visitor who is not logged in should be bounced to the login page.
        Django's login_required decorator adds ?next=<url> so the user lands
        back here after logging in.
        """
        response = self.client.post(
            self.url,
            {'action': 'mark', 'student_pk': self.student.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    # ------------------------------------------------------------------
    # 2. Authorisation — wrong teacher
    # ------------------------------------------------------------------

    def test_teacher_who_does_not_own_course_is_denied(self):
        """
        A different teacher (not the course owner) should be redirected to
        home with an error, and no Attendance row should be created.
        """
        other_teacher = make_teacher(username='teacher2', staff_id='T002')
        self.client.login(username='teacher2', password='pass')

        response = self.client.post(
            self.url,
            {'action': 'mark', 'student_pk': self.student.pk},
        )

        # Redirected away — not allowed
        self.assertRedirects(response, reverse('home'))
        # Nothing written to the database
        self.assertFalse(
            Attendance.objects.filter(session=self.session, student=self.student).exists()
        )

    def test_student_user_is_denied(self):
        """
        A student account has no teacher_profile, so the view's
        ClassSession.objects.get(..., course__teacher=request.user.teacher_profile)
        raises Teacher.DoesNotExist and redirects to home.
        """
        self.client.login(username='student1', password='pass')

        response = self.client.post(
            self.url,
            {'action': 'mark', 'student_pk': self.student.pk},
        )

        self.assertRedirects(response, reverse('home'))
        self.assertFalse(
            Attendance.objects.filter(session=self.session, student=self.student).exists()
        )

    # ------------------------------------------------------------------
    # 3. mark action
    # ------------------------------------------------------------------

    def test_mark_creates_attendance_and_redirects(self):
        """
        Happy path: teacher marks an absent student as present.
        An Attendance row should be created and the teacher is sent back
        to the session report page.
        """
        self.client.login(username='teacher1', password='pass')

        response = self.client.post(
            self.url,
            {'action': 'mark', 'student_pk': self.student.pk},
        )

        self.assertRedirects(response, reverse('session_report', args=[self.session.id]))
        self.assertTrue(
            Attendance.objects.filter(session=self.session, student=self.student).exists()
        )

    def test_mark_is_idempotent(self):
        """
        Marking a student who is already present should not create a second
        Attendance row (get_or_create guarantees this), and should not crash.
        """
        Attendance.objects.create(session=self.session, student=self.student)
        self.client.login(username='teacher1', password='pass')

        self.client.post(
            self.url,
            {'action': 'mark', 'student_pk': self.student.pk},
        )

        self.assertEqual(
            Attendance.objects.filter(session=self.session, student=self.student).count(),
            1,
        )

    # ------------------------------------------------------------------
    # 4. unmark action
    # ------------------------------------------------------------------

    def test_unmark_deletes_attendance_and_redirects(self):
        """
        Teacher removes an incorrect attendance record.
        The Attendance row should be gone and the teacher is redirected back.
        """
        Attendance.objects.create(session=self.session, student=self.student)
        self.client.login(username='teacher1', password='pass')

        response = self.client.post(
            self.url,
            {'action': 'unmark', 'student_pk': self.student.pk},
        )

        self.assertRedirects(response, reverse('session_report', args=[self.session.id]))
        self.assertFalse(
            Attendance.objects.filter(session=self.session, student=self.student).exists()
        )

    def test_unmark_on_absent_student_does_not_crash(self):
        """
        Calling unmark on a student who was never marked is safe.
        filter().delete() on an empty queryset does nothing — no exception.
        """
        self.client.login(username='teacher1', password='pass')

        response = self.client.post(
            self.url,
            {'action': 'unmark', 'student_pk': self.student.pk},
        )

        # Should still redirect cleanly, not 500
        self.assertRedirects(response, reverse('session_report', args=[self.session.id]))

    # ------------------------------------------------------------------
    # 5. Invalid student_pk
    # ------------------------------------------------------------------

    def test_invalid_student_pk_redirects_with_error(self):
        """
        If the student_pk doesn't exist the view should redirect back to the
        session report with an error message rather than raising a 500.
        """
        self.client.login(username='teacher1', password='pass')

        response = self.client.post(
            self.url,
            {'action': 'mark', 'student_pk': 99999},
        )

        self.assertRedirects(response, reverse('session_report', args=[self.session.id]))


class SessionReportViewTests(TestCase):
    """
    Tests for SessionReportView — shows attended/absent students for a session.

    URL: GET /accounts/session/<session_id>/report/
    """

    def setUp(self):
        self.teacher = make_teacher()
        self.course = Course.objects.create(code='CS101', name='Intro', teacher=self.teacher)
        self.session = ClassSession.objects.create(course=self.course, is_active=False)

        # Three students enrolled in the course
        self.student_a = make_student(username='student_a', student_id='S001')
        self.student_b = make_student(username='student_b', student_id='S002')
        self.student_c = make_student(username='student_c', student_id='S003')
        self.course.students.add(self.student_a, self.student_b, self.student_c)

        self.url = reverse('session_report', args=[self.session.id])

    # ------------------------------------------------------------------
    # 1. Authentication
    # ------------------------------------------------------------------

    def test_unauthenticated_redirects_to_login(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    # ------------------------------------------------------------------
    # 2. Authorisation
    # ------------------------------------------------------------------

    def test_owner_teacher_gets_200(self):
        """The course's teacher can view the report."""
        self.client.login(username='teacher1', password='pass')

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'session_report.html')

    def test_non_owner_teacher_is_redirected(self):
        """A teacher who does not own this course is sent to home."""
        make_teacher(username='teacher2', staff_id='T002')
        self.client.login(username='teacher2', password='pass')

        response = self.client.get(self.url)

        self.assertRedirects(response, reverse('home'))

    def test_student_user_is_redirected(self):
        """
        A student has no teacher_profile so the view's .get() raises
        Teacher.DoesNotExist and redirects to home.
        """
        self.client.login(username='student_a', password='pass')

        response = self.client.get(self.url)

        self.assertRedirects(response, reverse('home'))

    # ------------------------------------------------------------------
    # 3. Attended / absent split — the core logic
    # ------------------------------------------------------------------

    def test_partial_attendance_split(self):
        """
        With 3 enrolled students and 2 marked as attended, the context should
        contain exactly 2 in 'attended' and 1 in 'absent'.

        response.context gives us the template variables Django passed in,
        so we can assert on the actual querysets without parsing HTML.
        """
        Attendance.objects.create(session=self.session, student=self.student_a)
        Attendance.objects.create(session=self.session, student=self.student_b)
        self.client.login(username='teacher1', password='pass')

        response = self.client.get(self.url)

        attended = list(response.context['attended'])
        absent = list(response.context['absent'])

        self.assertIn(self.student_a, attended)
        self.assertIn(self.student_b, attended)
        self.assertNotIn(self.student_c, attended)

        self.assertIn(self.student_c, absent)
        self.assertNotIn(self.student_a, absent)
        self.assertNotIn(self.student_b, absent)

    def test_no_attendance_all_students_absent(self):
        """When nobody has been marked, every enrolled student should be absent."""
        self.client.login(username='teacher1', password='pass')

        response = self.client.get(self.url)

        self.assertEqual(response.context['attended'].count(), 0)
        self.assertEqual(response.context['absent'].count(), 3)

    def test_full_attendance_absent_list_empty(self):
        """When every enrolled student attended, the absent list should be empty."""
        Attendance.objects.create(session=self.session, student=self.student_a)
        Attendance.objects.create(session=self.session, student=self.student_b)
        Attendance.objects.create(session=self.session, student=self.student_c)
        self.client.login(username='teacher1', password='pass')

        response = self.client.get(self.url)

        self.assertEqual(response.context['attended'].count(), 3)
        self.assertEqual(response.context['absent'].count(), 0)


class ManageCourseStudentsViewTests(TestCase):
    """
    Tests for ManageCourseStudentsView — teachers (and superusers) add/remove
    students from a course.

    URL: /accounts/course/<course_id>/students/
    """

    def setUp(self):
        self.teacher = make_teacher()
        self.course = Course.objects.create(code='CS101', name='Intro', teacher=self.teacher)

        # One student already enrolled, one not enrolled yet
        self.enrolled = make_student(username='enrolled', student_id='S001')
        self.available = make_student(username='available', student_id='S002')
        self.course.students.add(self.enrolled)

        self.url = reverse('manage_course_students', args=[self.course.id])

    # ------------------------------------------------------------------
    # GET — authentication & authorisation
    # ------------------------------------------------------------------

    def test_unauthenticated_redirects_to_login(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_owner_teacher_gets_200_with_correct_context(self):
        """
        The owner sees the page and the context splits students correctly:
        enrolled contains only enrolled students, available contains the rest.
        """
        self.client.login(username='teacher1', password='pass')

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'course_students.html')

        enrolled = list(response.context['enrolled'])
        available = list(response.context['available'])

        self.assertIn(self.enrolled, enrolled)
        self.assertNotIn(self.available, enrolled)

        self.assertIn(self.available, available)
        self.assertNotIn(self.enrolled, available)

    def test_non_owner_teacher_is_redirected(self):
        make_teacher(username='teacher2', staff_id='T002')
        self.client.login(username='teacher2', password='pass')

        response = self.client.get(self.url)

        self.assertRedirects(response, reverse('home'))

    def test_superuser_can_access(self):
        """
        Superusers bypass the teacher ownership check and can manage
        any course's students.
        """
        User.objects.create_superuser(username='admin', password='pass')
        self.client.login(username='admin', password='pass')

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

    # ------------------------------------------------------------------
    # POST add
    # ------------------------------------------------------------------

    def test_add_enrolls_student(self):
        """Adding an available student should add them to course.students."""
        self.client.login(username='teacher1', password='pass')

        self.client.post(self.url, {'action': 'add', 'student_id': self.available.student_id})

        self.assertTrue(self.course.students.filter(pk=self.available.pk).exists())

    def test_add_already_enrolled_student_does_not_duplicate(self):
        """
        Trying to add a student who is already enrolled should not create a
        duplicate M2M relationship.  The view checks and shows a warning.
        """
        self.client.login(username='teacher1', password='pass')

        self.client.post(self.url, {'action': 'add', 'student_id': self.enrolled.student_id})

        # Still exactly one relationship, not two
        self.assertEqual(self.course.students.filter(pk=self.enrolled.pk).count(), 1)

    def test_add_nonexistent_student_id_does_not_crash(self):
        """A bad student_id should produce an error message, not a 500."""
        self.client.login(username='teacher1', password='pass')

        response = self.client.post(self.url, {'action': 'add', 'student_id': 'NOPE'})

        # Redirects back to the same page cleanly
        self.assertRedirects(response, self.url)

    # ------------------------------------------------------------------
    # POST remove
    # ------------------------------------------------------------------

    def test_remove_unenrolls_student(self):
        """Removing an enrolled student should drop them from course.students."""
        self.client.login(username='teacher1', password='pass')

        self.client.post(self.url, {'action': 'remove', 'student_id': self.enrolled.student_id})

        self.assertFalse(self.course.students.filter(pk=self.enrolled.pk).exists())

    def test_remove_nonexistent_student_id_does_not_crash(self):
        """A bad student_id on remove should produce an error message, not a 500."""
        self.client.login(username='teacher1', password='pass')

        response = self.client.post(self.url, {'action': 'remove', 'student_id': 'NOPE'})

        self.assertRedirects(response, self.url)

    # ------------------------------------------------------------------
    # POST authorisation
    # ------------------------------------------------------------------

    def test_non_owner_post_is_denied_and_db_unchanged(self):
        """
        A teacher who doesn't own the course cannot modify its enrolment.
        The available student should remain unenrolled after the attempt.
        """
        make_teacher(username='teacher2', staff_id='T002')
        self.client.login(username='teacher2', password='pass')

        self.client.post(self.url, {'action': 'add', 'student_id': self.available.student_id})

        self.assertFalse(self.course.students.filter(pk=self.available.pk).exists())


class AdminPanelViewTests(TestCase):
    """
    Tests for AdminPanelView, CreateTeacherView, and CreateCourseView.
    All three are guarded by superuser_required (user_passes_test).
    """

    def setUp(self):
        self.superuser = User.objects.create_superuser(username='admin', password='pass')
        # A regular teacher to use as course owner in CreateCourse tests
        self.teacher = make_teacher()

    # ------------------------------------------------------------------
    # AdminPanelView
    # ------------------------------------------------------------------

    def test_unauthenticated_redirects(self):
        response = self.client.get(reverse('admin_panel'))

        self.assertEqual(response.status_code, 302)

    def test_regular_teacher_is_blocked(self):
        """
        user_passes_test redirects anyone who fails the check — including
        logged-in teachers.  They are not superusers so they get bounced.
        """
        self.client.login(username='teacher1', password='pass')

        response = self.client.get(reverse('admin_panel'))

        self.assertEqual(response.status_code, 302)

    def test_superuser_sees_admin_panel(self):
        self.client.login(username='admin', password='pass')

        response = self.client.get(reverse('admin_panel'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'admin_panel.html')

    def test_admin_panel_context_contains_teachers_and_courses(self):
        """Teachers and courses created beforehand should appear in context."""
        course = Course.objects.create(code='CS101', name='Intro', teacher=self.teacher)
        self.client.login(username='admin', password='pass')

        response = self.client.get(reverse('admin_panel'))

        self.assertIn(self.teacher, list(response.context['teachers']))
        self.assertIn(course, list(response.context['courses']))

    # ------------------------------------------------------------------
    # CreateTeacherView
    # ------------------------------------------------------------------

    def test_create_teacher_valid_post_creates_user_and_teacher(self):
        """
        A valid form submission should create both a User and a linked
        Teacher profile, then redirect to the admin panel.
        """
        self.client.login(username='admin', password='pass')

        response = self.client.post(reverse('create_teacher'), {
            'first_name': 'Jane',
            'last_name': 'Smith',
            'username': 'jsmith',
            'email': 'jane@example.com',
            'password1': 'Str0ng!Pass',
            'password2': 'Str0ng!Pass',
            'staff_id': 'T999',
            'department': 'Mathematics',
        })

        self.assertRedirects(response, reverse('admin_panel'))
        # User was created
        self.assertTrue(User.objects.filter(username='jsmith').exists())
        # Teacher profile was linked to that user
        self.assertTrue(Teacher.objects.filter(staff_id='T999').exists())

    def test_create_teacher_invalid_post_rerenders_form(self):
        """
        Mismatched passwords should fail validation, re-render the form,
        and leave the database unchanged.
        """
        self.client.login(username='admin', password='pass')
        teacher_count_before = Teacher.objects.count()

        response = self.client.post(reverse('create_teacher'), {
            'first_name': 'Jane',
            'last_name': 'Smith',
            'username': 'jsmith',
            'email': 'jane@example.com',
            'password1': 'Str0ng!Pass',
            'password2': 'WrongPass!',   # mismatch
            'staff_id': 'T999',
            'department': 'Mathematics',
        })

        # Stays on the form page
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'create_teacher.html')
        # Nothing was created
        self.assertEqual(Teacher.objects.count(), teacher_count_before)

    # ------------------------------------------------------------------
    # CreateCourseView
    # ------------------------------------------------------------------

    def test_create_course_valid_post_creates_course(self):
        """A valid form should create a Course and redirect to admin panel."""
        self.client.login(username='admin', password='pass')

        response = self.client.post(reverse('create_course'), {
            'code': 'MATH101',
            'name': 'Calculus I',
            'teacher': self.teacher.pk,
            'room': 'B204',
            'schedule': 'Mon/Wed 10:00',
        })

        self.assertRedirects(response, reverse('admin_panel'))
        self.assertTrue(Course.objects.filter(code='MATH101').exists())

    def test_create_course_duplicate_code_rerenders_form(self):
        """
        Course codes are unique. Submitting an existing code should fail
        validation, re-render the form, and not create a second course.
        """
        Course.objects.create(code='CS101', name='Existing', teacher=self.teacher)
        self.client.login(username='admin', password='pass')

        response = self.client.post(reverse('create_course'), {
            'code': 'CS101',   # already exists
            'name': 'Duplicate',
            'teacher': self.teacher.pk,
        })

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'create_course.html')
        self.assertEqual(Course.objects.filter(code='CS101').count(), 1)


class HomeViewTests(TestCase):
    """
    Tests for HomeView — the dashboard shown after login.
    The view has three branches: teacher, student, and superuser.
    """

    def setUp(self):
        self.teacher = make_teacher()
        self.course = Course.objects.create(code='CS101', name='Intro', teacher=self.teacher)
        self.student = make_student()
        self.course.students.add(self.student)
        self.url = reverse('home')

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def test_unauthenticated_redirects_to_login(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    # ------------------------------------------------------------------
    # Teacher branch
    # ------------------------------------------------------------------

    def test_teacher_sees_teacher_role(self):
        self.client.login(username='teacher1', password='pass')

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['role'], 'teacher')

    def test_teacher_only_sees_own_courses(self):
        """
        A second teacher with their own course should not appear in
        the first teacher's course list.
        """
        other_teacher = make_teacher(username='teacher2', staff_id='T002')
        Course.objects.create(code='MATH101', name='Maths', teacher=other_teacher)
        self.client.login(username='teacher1', password='pass')

        response = self.client.get(self.url)

        codes = [c.code for c in response.context['courses']]
        self.assertIn('CS101', codes)
        self.assertNotIn('MATH101', codes)

    # ------------------------------------------------------------------
    # Student branch — attendance percentage calculation
    # ------------------------------------------------------------------

    def test_student_sees_student_role(self):
        self.client.login(username='student1', password='pass')

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['role'], 'student')

    def test_student_attendance_percentage_calculation(self):
        """
        4 sessions, student attended 2 → percentage should be 50.
        This tests the core arithmetic inside the HomeView loop.
        """
        sessions = [
            ClassSession.objects.create(course=self.course, is_active=False)
            for _ in range(4)
        ]
        # Mark attendance for the first two sessions only
        Attendance.objects.create(session=sessions[0], student=self.student)
        Attendance.objects.create(session=sessions[1], student=self.student)

        self.client.login(username='student1', password='pass')
        response = self.client.get(self.url)

        course_entry = response.context['course_data'][0]
        self.assertEqual(course_entry['total'], 4)
        self.assertEqual(course_entry['attended_count'], 2)
        self.assertEqual(course_entry['percentage'], 50)

    def test_student_percentage_is_none_when_no_sessions(self):
        """
        A course with no sessions should have percentage=None so the
        template can show 'No sessions' instead of 0%.
        """
        self.client.login(username='student1', password='pass')

        response = self.client.get(self.url)

        course_entry = response.context['course_data'][0]
        self.assertEqual(course_entry['total'], 0)
        self.assertIsNone(course_entry['percentage'])

    # ------------------------------------------------------------------
    # Superuser branch
    # ------------------------------------------------------------------

    def test_superuser_sees_superuser_role(self):
        User.objects.create_superuser(username='admin', password='pass')
        self.client.login(username='admin', password='pass')

        response = self.client.get(self.url)

        self.assertEqual(response.context['role'], 'superuser')

    def test_superuser_stats_are_accurate(self):
        """
        The stats dict should reflect exactly what is in the database.
        setUp already created 1 teacher, 1 student, 1 course, 0 sessions.
        """
        ClassSession.objects.create(course=self.course, is_active=False)
        User.objects.create_superuser(username='admin', password='pass')
        self.client.login(username='admin', password='pass')

        response = self.client.get(self.url)

        stats = response.context['stats']
        self.assertEqual(stats['teacher_count'], 1)
        self.assertEqual(stats['student_count'], 1)
        self.assertEqual(stats['course_count'], 1)
        self.assertEqual(stats['session_count'], 1)

    def test_superuser_recent_sessions_capped_at_five(self):
        """
        Even if more than 5 sessions exist, only the 5 most recent
        should appear in recent_sessions.
        """
        for _ in range(7):
            ClassSession.objects.create(course=self.course, is_active=False)
        User.objects.create_superuser(username='admin', password='pass')
        self.client.login(username='admin', password='pass')

        response = self.client.get(self.url)

        self.assertEqual(len(response.context['recent_sessions']), 5)


class StudentModelTests(TestCase):
    """
    Tests for the Student model's face encoding helpers.

    set_encoding() serialises a numpy array to JSON and stores it in the
    face_encoding TextField.  get_encoding() deserialises it back.
    These are pure model tests — no HTTP client, no views, no mocking.
    """

    def setUp(self):
        self.student = make_student()

    def test_set_encoding_stores_non_null_value(self):
        """After set_encoding the field should not be None or empty."""
        self.assertIsNotNone(self.student.face_encoding)
        self.assertNotEqual(self.student.face_encoding, '')

    def test_get_encoding_returns_numpy_array(self):
        """get_encoding should give back a numpy ndarray, not a list or string."""
        encoding = self.student.get_encoding()

        self.assertIsInstance(encoding, np.ndarray)

    def test_encoding_roundtrip_preserves_values(self):
        """
        The values that go in via set_encoding must come back unchanged
        via get_encoding.  Uses a non-trivial array (not all zeros) so
        the test would catch a silent data corruption.
        """
        original = np.array([0.1, 0.5, 0.9] + [0.0] * 125)  # 128 elements
        self.student.set_encoding(original)
        self.student.save()

        # Reload from DB to confirm the value survived the write
        reloaded = Student.objects.get(pk=self.student.pk)
        recovered = reloaded.get_encoding()

        np.testing.assert_array_almost_equal(original, recovered)

    def test_encoding_has_correct_shape(self):
        """face_recognition always produces 128-element vectors."""
        encoding = self.student.get_encoding()

        self.assertEqual(encoding.shape, (128,))
