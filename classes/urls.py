from django.urls import path
from . import views
urlpatterns=[path('create/',views.create_class_view,name='create_class'),path('assign/',views.create_class_view,name='assign_class'),path('mine/create/',views.create_teacher_class_view,name='create_teacher_class'),path('<int:pk>/',views.class_detail_view,name='class_detail'),path('<int:pk>/import/',views.import_students_view,name='import_students')]
