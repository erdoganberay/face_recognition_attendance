# accounts/models.py
from django.db import models
from django.contrib.auth.models import User
import numpy as np
import json
 
 
class Student(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    student_id = models.CharField(max_length=20, unique=True)
    date_of_birth = models.DateField()
    face_encoding = models.TextField(blank=True, null=True)
 
    def set_encoding(self, encoding_array):
        self.face_encoding = json.dumps(encoding_array.tolist())
 
    def get_encoding(self):
        return np.array(json.loads(self.face_encoding))
 
    def __str__(self):
        return f"{self.user.get_full_name()} - {self.student_id}"
 

