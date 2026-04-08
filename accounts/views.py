from django.contrib.auth import logout
from django.contrib.auth.views import PasswordChangeView
from django.contrib import messages
from django.shortcuts import render, redirect
from django.urls import reverse_lazy
from django.views import View
from .forms import SignUpForm, CreateTeacherForm, CreateCourseForm
from .models import Student, Teacher, Course, ClassSession, Attendance
import face_recognition
import numpy as np
import base64
import logging
import io
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.core.paginator import Paginator
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, HttpResponse
import json
import csv
from PIL import Image, ImageEnhance
import cv2

logger = logging.getLogger(__name__)

# ====================== IMPROVED FACE RECOGNITION UTILS ======================
def preprocess_face(rgb_image):
    """Preprocess image for better accuracy in varying lighting/conditions"""
    if rgb_image is None:
        return None
    try:
        # Ensure uint8 format
        if rgb_image.dtype != np.uint8:
            rgb_image = (rgb_image * 255).astype(np.uint8) if rgb_image.max() <= 1.0 else rgb_image.astype(np.uint8)

        # CLAHE - local contrast normalization (major accuracy boost)
        lab = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        cl = clahe.apply(l)
        lab = cv2.merge((cl, a, b))
        enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)

        # Global contrast + brightness adjustment
        pil_img = Image.fromarray(enhanced)
        pil_img = ImageEnhance.Contrast(pil_img).enhance(1.4)
        pil_img = ImageEnhance.Brightness(pil_img).enhance(1.15)
        enhanced = np.array(pil_img)

        # Light sharpening
        kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])
        enhanced = cv2.filter2D(enhanced, -1, kernel)

        # Resize if image is too large (prevents CNN detector issues)
        h, w = enhanced.shape[:2]
        if max(h, w) > 1000:
            scale = 1000 / max(h, w)
            enhanced = cv2.resize(enhanced, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

        return enhanced
    except Exception as e:
        logger.error(f"Preprocessing error: {e}")
        return rgb_image  # fallback to original


def get_robust_encodings(image, num_jitters=5, model="cnn"):
    """Generate stable face encodings with preprocessing"""
    processed = preprocess_face(image)
    if processed is None:
        return []

    face_locations = face_recognition.face_locations(processed, model=model, number_of_times_to_upsample=1)
    if not face_locations and model == "cnn":
        face_locations = face_recognition.face_locations(processed, model="hog")

    if not face_locations:
        return []

    encodings = face_recognition.face_encodings(
        processed,
        known_face_locations=face_locations,
        num_jitters=num_jitters
    )
    return encodings


# ====================== VIEWS ======================
class SignUpView(View):
    template_name = "registration/signup.html"

    def get(self, request):
        form = SignUpForm()
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        form = SignUpForm(request.POST)
        if form.is_valid():
            face_base64_data = request.POST.get('face_base64_data', '')

            if not face_base64_data:
                form.add_error(None, 'Please capture your face photo before signing up.')
                return render(request, self.template_name, {'form': form})

            try:
                image_data = face_base64_data.split(',')[1]
                img_bytes = base64.b64decode(image_data)
                img_file = io.BytesIO(img_bytes)
                user_image = face_recognition.load_image_file(img_file)

                encodings = get_robust_encodings(user_image, num_jitters=5, model="cnn")

                if len(encodings) == 0:
                    form.add_error(None, 'No face detected. Please try again in good lighting.')
                    return render(request, self.template_name, {'form': form})
                if len(encodings) > 1:
                    form.add_error(None, 'Multiple faces detected. Please take a photo alone.')
                    return render(request, self.template_name, {'form': form})

                encoding = encodings[0]

            except Exception as e:
                logger.error(f"Face processing error: {e}")
                form.add_error(None, 'Error processing photo. Please ensure good lighting and try again.')
                return render(request, self.template_name, {'form': form})

            # Save user and student
            user = form.save(commit=False)
            user.first_name = form.cleaned_data['first_name']
            user.last_name = form.cleaned_data['last_name']
            user.email = form.cleaned_data['email']
            user.save()

            student = Student(
                user=user,
                student_id=form.cleaned_data['student_id'],
                date_of_birth=form.cleaned_data['date_of_birth'],
            )
            student.set_encoding(encoding)
            student.save()

            messages.success(request, 'Account created successfully! Please log in.')
            return redirect('login')

        return render(request, self.template_name, {'form': form})


class CustomPasswordChangeView(PasswordChangeView):
    template_name = "registration/password_change_form.html"

    def form_valid(self, form):
        form.save()
        logout(self.request)
        messages.success(self.request, "Password changed successfully. Please log in again.")
        return redirect(reverse_lazy("login"))


@method_decorator(login_required, name='dispatch')
class HomeView(View):
    template_name = 'home.html'

    def get(self, request):
        if hasattr(request.user, 'teacher_profile'):
            courses_qs = request.user.teacher_profile.courses.prefetch_related('sessions').order_by('code')
            paginator = Paginator(courses_qs, 6)
            page_obj = paginator.get_page(request.GET.get('page'))
            return render(request, self.template_name, {'role': 'teacher', 'courses': page_obj, 'page_obj': page_obj})
        elif hasattr(request.user, 'student_profile'):
            student = request.user.student_profile
            courses_qs = student.courses.order_by('code')
            paginator = Paginator(courses_qs, 6)
            page_obj = paginator.get_page(request.GET.get('page'))
            course_data = []
            for course in page_obj:
                sessions = course.sessions.all().order_by('-started_at')
                session_data = []
                for session in sessions:
                    attended = Attendance.objects.filter(session=session, student=student).exists()
                    session_data.append({'session': session, 'attended': attended})
                total = len(session_data)
                attended_count = sum(1 for s in session_data if s['attended'])
                percentage = round((attended_count / total) * 100) if total > 0 else None
                course_data.append({
                    'course': course,
                    'sessions': session_data,
                    'total': total,
                    'attended_count': attended_count,
                    'percentage': percentage,
                })
            return render(request, self.template_name, {'role': 'student', 'courses': page_obj, 'course_data': course_data, 'page_obj': page_obj})
        elif request.user.is_superuser:
            stats = {
                'teacher_count': Teacher.objects.count(),
                'student_count': Student.objects.count(),
                'course_count': Course.objects.count(),
                'session_count': ClassSession.objects.count(),
            }
            recent_sessions = ClassSession.objects.select_related('course').order_by('-started_at')[:5]
            return render(request, self.template_name, {'role': 'superuser', 'stats': stats, 'recent_sessions': recent_sessions})
        return render(request, self.template_name, {})


@method_decorator(login_required, name='dispatch')
class StartSessionView(View):
    def post(self, request, course_id):
        course = Course.objects.get(id=course_id, teacher=request.user.teacher_profile)
        # Close any already active session
        ClassSession.objects.filter(course=course, is_active=True).update(
            is_active=False, ended_at=timezone.now()
        )
        session = ClassSession.objects.create(course=course)
        return redirect('attendance_page', session_id=session.id)


@method_decorator(login_required, name='dispatch')
class AttendanceView(View):
    template_name = 'attendance.html'

    def get(self, request, session_id):
        session = ClassSession.objects.get(id=session_id, is_active=True)
        return render(request, self.template_name, {'session': session})


@method_decorator(login_required, name='dispatch')
class EndSessionView(View):
    def post(self, request, session_id):
        session = ClassSession.objects.get(id=session_id, course__teacher=request.user.teacher_profile)
        session.is_active = False
        session.ended_at = timezone.now()
        session.save()
        return redirect('home')


@method_decorator(login_required, name='dispatch')
class SessionReportView(View):
    template_name = 'session_report.html'

    def get(self, request, session_id):
        try:
            session = ClassSession.objects.get(id=session_id, course__teacher=request.user.teacher_profile)
        except (ClassSession.DoesNotExist, Teacher.DoesNotExist):
            messages.error(request, 'Session not found or access denied.')
            return redirect('home')

        attended_students = Student.objects.filter(attendance__session=session)
        absent_students = session.course.students.exclude(pk__in=attended_students)

        return render(request, self.template_name, {
            'session': session,
            'attended': attended_students,
            'absent': absent_students,
        })


@method_decorator(login_required, name='dispatch')
class ManualAttendanceView(View):
    def post(self, request, session_id):
        try:
            session = ClassSession.objects.get(id=session_id, course__teacher=request.user.teacher_profile)
        except (ClassSession.DoesNotExist, Teacher.DoesNotExist):
            messages.error(request, 'Session not found or access denied.')
            return redirect('home')

        action = request.POST.get('action')
        student_pk = request.POST.get('student_pk')

        try:
            student = Student.objects.get(pk=student_pk)
        except Student.DoesNotExist:
            messages.error(request, 'Student not found.')
            return redirect('session_report', session_id=session_id)

        if action == 'mark':
            Attendance.objects.get_or_create(session=session, student=student)
            messages.success(request, f'{student.user.get_full_name()} marked as present.')
        elif action == 'unmark':
            Attendance.objects.filter(session=session, student=student).delete()
            messages.success(request, f'{student.user.get_full_name()} removed from attendance.')

        return redirect('session_report', session_id=session_id)


@method_decorator(login_required, name='dispatch')
class SessionExportView(View):
    def get(self, request, session_id):
        try:
            session = ClassSession.objects.get(id=session_id, course__teacher=request.user.teacher_profile)
        except (ClassSession.DoesNotExist, Teacher.DoesNotExist):
            messages.error(request, 'Session not found or access denied.')
            return redirect('home')

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="session_{session_id}_{session.course.code}.csv"'

        writer = csv.writer(response)
        writer.writerow(['Student ID', 'Name', 'Status'])

        attended_ids = set(
            Attendance.objects.filter(session=session).values_list('student_id', flat=True)
        )
        for student in session.course.students.all().order_by('student_id'):
            status = 'Present' if student.pk in attended_ids else 'Absent'
            writer.writerow([student.student_id, student.user.get_full_name(), status])

        return response


@method_decorator(login_required, name='dispatch')
class CourseExportView(View):
    def get(self, request, course_id):
        try:
            course = Course.objects.get(id=course_id, teacher=request.user.teacher_profile)
        except (Course.DoesNotExist, Teacher.DoesNotExist):
            messages.error(request, 'Course not found or access denied.')
            return redirect('home')

        sessions = list(course.sessions.order_by('started_at'))
        students = list(course.students.all().order_by('student_id'))

        attended_pairs = set(
            Attendance.objects.filter(session__course=course)
            .values_list('student_id', 'session_id')
        )

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="course_{course.code}_attendance.csv"'

        writer = csv.writer(response)
        session_headers = [s.started_at.strftime('%b %d %Y %H:%M') for s in sessions]
        writer.writerow(['Student ID', 'Name'] + session_headers + ['Total Present', 'Attendance %'])

        for student in students:
            row = [student.student_id, student.user.get_full_name()]
            total = 0
            for session in sessions:
                present = (student.pk, session.pk) in attended_pairs
                row.append('Present' if present else 'Absent')
                if present:
                    total += 1
            percentage = round((total / len(sessions)) * 100) if sessions else 0
            row += [total, f'{percentage}%']
            writer.writerow(row)

        return response


@method_decorator(login_required, name='dispatch')
class StudentProfileView(View):
    template_name = 'student_profile.html'

    def get(self, request):
        try:
            student = request.user.student_profile
        except Student.DoesNotExist:
            return redirect('home')
        return render(request, self.template_name, {'student': student})


@method_decorator(login_required, name='dispatch')
class ManageCourseStudentsView(View):
    template_name = 'course_students.html'

    def get_course_for_teacher(self, request, course_id):
        if request.user.is_superuser:
            try:
                return Course.objects.get(id=course_id)
            except Course.DoesNotExist:
                return None
        try:
            return Course.objects.get(id=course_id, teacher=request.user.teacher_profile)
        except (Course.DoesNotExist, Teacher.DoesNotExist):
            return None

    def get(self, request, course_id):
        course = self.get_course_for_teacher(request, course_id)
        if course is None:
            messages.error(request, 'Course not found or access denied.')
            return redirect('home')
        enrolled = course.students.all().order_by('student_id')
        available = Student.objects.exclude(pk__in=course.students.all()).order_by('student_id')
        return render(request, self.template_name, {'course': course, 'enrolled': enrolled, 'available': available})

    def post(self, request, course_id):
        course = self.get_course_for_teacher(request, course_id)
        if course is None:
            messages.error(request, 'Course not found or access denied.')
            return redirect('home')

        action = request.POST.get('action')
        student_id = request.POST.get('student_id', '').strip()

        if action == 'add':
            try:
                student = Student.objects.get(student_id=student_id)
                if course.students.filter(pk=student.pk).exists():
                    messages.warning(request, f'{student.user.get_full_name()} is already enrolled.')
                else:
                    course.students.add(student)
                    messages.success(request, f'{student.user.get_full_name()} added to {course.code}.')
            except Student.DoesNotExist:
                messages.error(request, f'No student found with ID "{student_id}".')

        elif action == 'remove':
            try:
                student = Student.objects.get(student_id=student_id)
                course.students.remove(student)
                messages.success(request, f'{student.user.get_full_name()} removed from {course.code}.')
            except Student.DoesNotExist:
                messages.error(request, f'No student found with ID "{student_id}".')

        return redirect('manage_course_students', course_id=course.id)


superuser_required = user_passes_test(lambda u: u.is_superuser, login_url='login')


@method_decorator(superuser_required, name='dispatch')
class AdminPanelView(View):
    template_name = 'admin_panel.html'

    def get(self, request):
        teachers = Teacher.objects.select_related('user').all().order_by('staff_id')
        courses = Course.objects.select_related('teacher__user').all().order_by('code')
        return render(request, self.template_name, {'teachers': teachers, 'courses': courses})


@method_decorator(superuser_required, name='dispatch')
class CreateTeacherView(View):
    template_name = 'create_teacher.html'

    def get(self, request):
        return render(request, self.template_name, {'form': CreateTeacherForm()})

    def post(self, request):
        form = CreateTeacherForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.first_name = form.cleaned_data['first_name']
            user.last_name = form.cleaned_data['last_name']
            user.email = form.cleaned_data['email']
            user.save()
            Teacher.objects.create(
                user=user,
                staff_id=form.cleaned_data['staff_id'],
                department=form.cleaned_data['department'],
            )
            messages.success(request, f'Teacher {user.get_full_name()} created successfully.')
            return redirect('admin_panel')
        return render(request, self.template_name, {'form': form})


@method_decorator(superuser_required, name='dispatch')
class CreateCourseView(View):
    template_name = 'create_course.html'

    def get(self, request):
        return render(request, self.template_name, {'form': CreateCourseForm()})

    def post(self, request):
        form = CreateCourseForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, f'Course {form.cleaned_data["code"]} created successfully.')
            return redirect('admin_panel')
        return render(request, self.template_name, {'form': form})


# ====================== IMPROVED RECOGNITION ENDPOINT ======================
@csrf_exempt
def recognize_face(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST required'}, status=405)
    
    try:
        data = json.loads(request.body)
        image_data = data.get('image')
        session_id = data.get('session_id')

        session = ClassSession.objects.filter(id=session_id, is_active=True).first()
        if not session:
            return JsonResponse({'status': 'error', 'message': 'No active session found'})

        # Decode base64 image
        header, encoded = image_data.split(',', 1)
        img_bytes = base64.b64decode(encoded)
        img_file = io.BytesIO(img_bytes)
        rgb_frame = face_recognition.load_image_file(img_file)

        # Preprocess + robust encoding
        processed_frame = preprocess_face(rgb_frame)
        if processed_frame is None:
            return JsonResponse({'status': 'error', 'message': 'Image processing failed'})

        face_locations = face_recognition.face_locations(processed_frame, model="cnn")
        if not face_locations:
            face_locations = face_recognition.face_locations(processed_frame, model="hog")

        if not face_locations:
            return JsonResponse({'status': 'no_face', 'message': 'No face detected. Check lighting and position.'})

        face_encodings = face_recognition.face_encodings(processed_frame, face_locations)

        # Load known students with encodings
        students = session.course.students.exclude(face_encoding=None)
        known_encodings = []
        known_students = []
        for student in students:
            enc = student.get_encoding()
            if enc is not None:
                known_encodings.append(enc)
                known_students.append(student)

        if not known_encodings:
            return JsonResponse({'status': 'error', 'message': 'No enrolled students with face data'})

        best_match = None
        best_confidence = 0

        for face_encoding in face_encodings:
            distances = face_recognition.face_distance(known_encodings, face_encoding)
            best_idx = np.argmin(distances)
            distance = distances[best_idx]
            confidence = (1 - distance) * 100

            # Tuned thresholds for higher precision
            if distance < 0.45 and confidence > 68:
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = known_students[best_idx]

        if best_match:
            record, created = Attendance.objects.get_or_create(
                session=session,
                student=best_match,
            )
            return JsonResponse({
                'status': 'recognized',
                'name': best_match.user.get_full_name(),
                'student_id': best_match.student_id,
                'confidence': round(best_confidence, 1),
                'already_marked': not created,
            })

        return JsonResponse({'status': 'unknown', 'message': 'Face not recognized. Try better lighting or angle.'})

    except Exception as e:
        logger.error(f"Recognition error: {e}")
        return JsonResponse({'status': 'error', 'message': 'Server error during recognition'}, status=500)
