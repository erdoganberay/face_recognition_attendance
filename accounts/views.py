from django.contrib.auth import logout
from django.contrib.auth.views import PasswordChangeView
from django.contrib import messages
from django.shortcuts import render, redirect
from django.urls import reverse_lazy
from django.views import View
from .forms import SignUpForm
from .models import Student, Teacher, Course, ClassSession,Attendance
import face_recognition
import numpy as np
import base64
import logging
import io
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.core.paginator import Paginator
from django.views.decorators.csrf import csrf_exempt
logger = logging.getLogger(__name__)
from django.http import JsonResponse, HttpResponse
import json
import csv



class SignUpView(View):
    template_name = "registration/signup.html"

    def get(self, request):
        form = SignUpForm()
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        form = SignUpForm(request.POST)

        if form.is_valid():
            # get face encoding from hidden field
            face_base64_data = request.POST.get('face_base64_data', '')

            if not face_base64_data:
                form.add_error(None, 'Please capture your face photo before signing up.')
                return render(request, self.template_name, {'form': form})
            
            try:
                # convert Base64 image to binary
                image_data = face_base64_data.split(',')[1]
                img_bytes = base64.b64decode(image_data)
                img_file = io.BytesIO(img_bytes)

                # encode the image
                user_image = face_recognition.load_image_file(img_file) 
                encodings = face_recognition.face_encodings(user_image)
                
                # check the face count in the image >1 is not allowed.
                if len(encodings) == 0:
                    form.add_error(None, 'No face detected in the photo. Please try again.')
                    return render(request, self.template_name, {'form': form})
                if len(encodings) > 1:
                    form.add_error(None, 'Multiple faces detected. Please take a photo alone.')
                    return render(request, self.template_name, {'form': form})
            except Exception as e:
                logger.error(f"Face processing error: {e}")
                form.add_error(None, 'Error processing photo. Please try again.')
                return render(request, self.template_name, {'form': form})    
        

           # save the user
            user = form.save(commit=False)
            user.first_name = form.cleaned_data['first_name']
            user.last_name = form.cleaned_data['last_name']
            user.email = form.cleaned_data['email']
            user.save()

            # save the student fields
            student = Student(
                user=user,
                student_id=form.cleaned_data['student_id'],
                date_of_birth=form.cleaned_data['date_of_birth'],   
            )
            # serializing encoding data
            student.set_encoding(encodings[0])
            student.save()
            #account creation succesfull
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
            courses_qs = request.user.teacher_profile.courses.prefetch_related('sessions').all()
            paginator = Paginator(courses_qs, 6)
            page_obj = paginator.get_page(request.GET.get('page'))
            return render(request, self.template_name, {'role': 'teacher', 'courses': page_obj, 'page_obj': page_obj})
        elif hasattr(request.user, 'student_profile'):
            student = request.user.student_profile
            courses_qs = student.courses.all()
            paginator = Paginator(courses_qs, 6)
            page_obj = paginator.get_page(request.GET.get('page'))
            course_data = []
            for course in page_obj:
                sessions = course.sessions.all().order_by('-started_at')
                session_data = []
                for session in sessions:
                    attended = Attendance.objects.filter(session=session, student=student).exists()
                    session_data.append({
                        'session': session,
                        'attended': attended,
                    })
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
        else:
            return render(request, self.template_name, {})


@method_decorator(login_required, name='dispatch')
class StartSessionView(View):
    def post(self, request, course_id):
        course = Course.objects.get(id=course_id, teacher=request.user.teacher_profile)
        # Close any already active session for this course
        ClassSession.objects.filter(course=course, is_active=True).update(
            is_active=False, ended_at=timezone.now()
        )
        # Create new session
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

        # Build a set of (student_id, session_id) pairs that have attendance
        attended_pairs = set(
            Attendance.objects.filter(session__course=course)
            .values_list('student_id', 'session_id')
        )

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="course_{course.code}_attendance.csv"'

        writer = csv.writer(response)

        # Header row: Student ID, Name, then one column per session, then Total and %
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
        """Return the course only if the logged-in user is its teacher."""
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
        # Decode base64 image
        header, encoded = image_data.split(',', 1)
        img_bytes = base64.b64decode(encoded)
        img_file = io.BytesIO(img_bytes)
        rgb_frame = face_recognition.load_image_file(img_file)
      
        # Detect faces
        face_locations = face_recognition.face_locations(rgb_frame)
        if not face_locations:
            return JsonResponse({'status': 'no_face', 'message': 'No face detected'})

        face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)

        # Load enrolled students only
        students = session.course.students.exclude(face_encoding=None)
        known_encodings = []
        known_students = []
        for student in students:
            known_encodings.append(student.get_encoding())
            known_students.append(student)

        if not known_encodings:
            return JsonResponse({'status': 'error', 'message': 'No enrolled students with encodings'})

        for face_encoding in face_encodings:
            matches = face_recognition.compare_faces(known_encodings, face_encoding, tolerance=0.5)
            distances = face_recognition.face_distance(known_encodings, face_encoding)
            best = np.argmin(distances)

            if matches[best]:
                matched_student = known_students[best]
                record, created = Attendance.objects.get_or_create(
                    session=session,
                    student=matched_student,
                )
                return JsonResponse({
                    'status': 'recognized',
                    'name': matched_student.user.get_full_name(),
                    'student_id': matched_student.student_id,
                    'already_marked': not created,
                })

        return JsonResponse({'status': 'unknown', 'message': 'Face not recognized'})

    except Exception as e:
        logger.error(f"Recognition error: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
