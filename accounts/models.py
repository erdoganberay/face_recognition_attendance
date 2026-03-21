# accounts/models.py
from django.db import models
from django.contrib.auth.models import User
import numpy as np
import json
 

class Teacher(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='teacher_profile')
    staff_id = models.CharField(max_length=20, unique=True)
    department = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.user.get_full_name()} ({self.staff_id})"



class Student(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='student_profile')
    student_id = models.CharField(max_length=20, unique=True)
    date_of_birth = models.DateField()
    face_encoding = models.TextField(blank=True, null=True)
    
    #Encoding Serialization
    def set_encoding(self, encoding_array):
        self.face_encoding = json.dumps(encoding_array.tolist())
    #Encodin Deserialization
    def get_encoding(self):
        return np.array(json.loads(self.face_encoding))
 
    def __str__(self):
        return f"{self.user.get_full_name()} - {self.student_id}"


class Course(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    teacher = models.ForeignKey(Teacher, on_delete=models.SET_NULL, null=True, related_name='courses')
    room = models.CharField(max_length=50, blank=True)
    schedule = models.CharField(max_length=100, blank=True)  # e.g. "Mon/Wed 9:00-10:30"
    students = models.ManyToManyField(Student, related_name='courses', blank=True)
    def __str__(self):
        return f"{self.code} - {self.name}"


class ClassSession(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='sessions')
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    def __str__(self):
        return f"{self.course.code} | {self.started_at.strftime('%Y-%m-%d %H:%M')}"

class Attendance(models.Model):
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name='attendance_records')
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('session', 'student')

    def __str__(self):
        return f"{self.student.user.get_full_name()} - {self.session}"
 

