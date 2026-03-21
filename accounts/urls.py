# accounts/urls.py
from django.urls import path
from .views import SignUpView, CustomPasswordChangeView, HomeView, StartSessionView, AttendanceView,EndSessionView, recognize_face



urlpatterns = [
    path("signup/", SignUpView.as_view(), name="signup"),
    path("password_change/", CustomPasswordChangeView.as_view(), name="password_change"),
    path("course/<int:course_id>/start/", StartSessionView.as_view(), name="start_session"),
    path("session/<int:session_id>/attendance/", AttendanceView.as_view(), name="attendance_page"),
    path("session/<int:session_id>/end/", EndSessionView.as_view(), name="end_session"),
    path("recognize/", recognize_face, name="recognize_face"),
]

