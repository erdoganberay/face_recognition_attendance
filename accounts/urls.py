# accounts/urls.py
from django.urls import path
from .views import SignUpView, CustomPasswordChangeView, HomeView, StartSessionView, AttendanceView, EndSessionView, recognize_face, ManageCourseStudentsView, StudentProfileView, SessionReportView, SessionExportView, CourseExportView, ManualAttendanceView, AdminPanelView, CreateTeacherView, CreateCourseView



urlpatterns = [
    path("signup/", SignUpView.as_view(), name="signup"),
    path("password_change/", CustomPasswordChangeView.as_view(), name="password_change"),
    path("course/<int:course_id>/start/", StartSessionView.as_view(), name="start_session"),
    path("session/<int:session_id>/attendance/", AttendanceView.as_view(), name="attendance_page"),
    path("session/<int:session_id>/end/", EndSessionView.as_view(), name="end_session"),
    path("recognize/", recognize_face, name="recognize_face"),
    path("course/<int:course_id>/students/", ManageCourseStudentsView.as_view(), name="manage_course_students"),
    path("profile/", StudentProfileView.as_view(), name="student_profile"),
    path("session/<int:session_id>/report/", SessionReportView.as_view(), name="session_report"),
    path("session/<int:session_id>/export/", SessionExportView.as_view(), name="session_export"),
    path("course/<int:course_id>/export/", CourseExportView.as_view(), name="course_export"),
    path("session/<int:session_id>/manual/", ManualAttendanceView.as_view(), name="manual_attendance"),
    path("admin-panel/", AdminPanelView.as_view(), name="admin_panel"),
    path("admin-panel/create-teacher/", CreateTeacherView.as_view(), name="create_teacher"),
    path("admin-panel/create-course/", CreateCourseView.as_view(), name="create_course"),
]

