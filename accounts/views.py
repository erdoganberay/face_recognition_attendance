# accounts/views.py
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
from django.views.decorators.csrf import csrf_exempt
logger = logging.getLogger(__name__)
from django.http import JsonResponse
import json



class SignUpView(View):
    template_name = "registration/signup.html"

    def get(self, request):
        form = SignUpForm()
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        form = SignUpForm(request.POST)

        if form.is_valid():
            # Get face encoding from hidden field
            face_base64_data = request.POST.get('face_base64_data', '')

            if not face_base64_data:
                form.add_error(None, 'Please capture your face photo before signing up.')
                return render(request, self.template_name, {'form': form})
            
            try:
                #Convert Base64 image to binary
                image_data = face_base64_data.split(',')[1]
                img_bytes = base64.b64decode(image_data)
                img_file = io.BytesIO(img_bytes)

                # Encode the image
                user_image = face_recognition.load_image_file(img_file) 
                encodings = face_recognition.face_encodings(user_image)
                
                #Check the face count in the image >1 is not allowed.
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
        

           # Save the User
            user = form.save(commit=False)
            user.first_name = form.cleaned_data['first_name']
            user.last_name = form.cleaned_data['last_name']
            user.email = form.cleaned_data['email']
            user.save()

            # Save the Student Fields
            student = Student(
                user=user,
                student_id=form.cleaned_data['student_id'],
                date_of_birth=form.cleaned_data['date_of_birth'],   
            )
            #Serializing Encoding data
            student.set_encoding(encodings[0])
            student.save()
            #Account Creation Succesfull
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
            courses = request.user.teacher_profile.courses.all()
            return render(request, self.template_name, {'role': 'teacher', 'courses': courses})
        elif hasattr(request.user, 'student_profile'):
            student = request.user.student_profile
            courses = student.courses.all()
            course_data = []
            for course in courses:
                sessions = course.sessions.all().order_by('-started_at')
                session_data = []
                for session in sessions:
                    attended = Attendance.objects.filter(session=session, student=student).exists()
                    session_data.append({
                        'session': session,
                        'attended': attended,
                    })
                course_data.append({
                    'course': course,
                    'sessions': session_data,
                })
            return render(request, self.template_name, {'role': 'student', 'courses': courses, 'course_data': course_data})
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
