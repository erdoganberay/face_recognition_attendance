# accounts/views.py
from django.contrib.auth import logout
from django.contrib.auth.views import PasswordChangeView
from django.contrib import messages
from django.shortcuts import render, redirect
from django.urls import reverse_lazy
from django.views import View
from .forms import SignUpForm
from .models import Student
import face_recognition
import numpy as np
import base64
import logging
import io
logger = logging.getLogger(__name__)


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
