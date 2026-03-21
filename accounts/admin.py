from django.contrib import admin
from .models import Student, Teacher, Course, ClassSession, Attendance

class ClassSessionAdmin(admin.ModelAdmin):
    readonly_fields = ('started_at',)

admin.site.register(Student)
admin.site.register(Teacher)
admin.site.register(Course)
admin.site.register(ClassSession, ClassSessionAdmin)
admin.site.register(Attendance)
