from django.urls import include, path
from django.views.generic import RedirectView
from accounts.views import dashboard_view, login_view, logout_view
urlpatterns = [path('', RedirectView.as_view(url='/dashboard/')), path('login/', login_view, name='login'), path('logout/', logout_view, name='logout'), path('dashboard/', dashboard_view, name='dashboard'), path('accounts/', include('accounts.urls')), path('classes/', include('classes.urls')), path('exams/', include('exams.urls')), path('audit/', include('audit.urls'))]
