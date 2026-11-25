from django.urls import path
from . import views

urlpatterns = [
    path('activate/', views.activate_view, name='activate'),
    path('invitations/', views.activation_invitations_view, name='activation_invitations'),
    path('profile/', views.profile_view, name='profile'),
    path('change-password/', views.change_password_view, name='change_password'),
    path('teacher-create/', views.create_teacher_view, name='create_teacher'),
    path('student-create/', views.create_student_view, name='create_student'),
    path('manage/', views.manage_users_view, name='manage_users'),
]
