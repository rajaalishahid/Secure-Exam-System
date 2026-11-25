import secrets

from django import forms
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.csrf import csrf_failure as default_csrf_failure
from django.views.decorators.http import require_http_methods, require_POST

from .authentication import begin_auth_session
from .forms import CreateStudentForm, CreateTeacherForm, LoginForm
from .lifecycle import invitation_url
from .models import AccountToken, User
from .security import audit, digest, issue_token, limited
from classes.models import ClassMembership, ClassRoom
from exams.models import Attempt, ExamSession, Quiz, QuizEnrollment
from exams.network import network_allowed


def page(request, title, form=None, **context):
    return render(request, 'form_page.html', {'title': title, 'form': form, **context})


def _login_qr_exam(request):
    session_id = request.session.get('join_session')
    token_digest = request.session.get('join_digest')
    if not session_id or not token_digest:
        return None
    return ExamSession.objects.select_related('quiz').filter(
        pk=session_id,
        token_digest=token_digest,
        status='open',
        ends_at__gt=timezone.now(),
    ).first()


def _login_render(request, form):
    return render(request, 'login.html', {
        'form': form,
        'login_page': True,
        'qr_exam': _login_qr_exam(request),
    })


def _finish_login(request, user, backend):
    login(request, user, backend=backend)
    begin_auth_session(request, user, backend)
    if user.role == 'student':
        user.active_session = request.session.session_key
        user.save(update_fields=['active_session'])

    exam = _login_qr_exam(request)
    if (exam and user.role == 'student'
            and QuizEnrollment.objects.filter(quiz=exam.quiz, student=user).exists()
            and network_allowed(request, exam)):
        audit(user, 'auth.login_success')
        return redirect('exam_enter')

    request.session.pop('join_session', None)
    request.session.pop('join_digest', None)
    audit(user, 'auth.login_success')
    return redirect('dashboard')


@login_required
def dashboard_view(request):
    user = request.user
    if user.role == 'admin':
        return render(request, 'dashboard_admin.html', {
            'teachers': User.objects.filter(role='teacher').count(),
            'students': User.objects.filter(role='student').count(),
            'classes': ClassRoom.objects.count(),
            'quizzes': Quiz.objects.count(),
            'recent_attempts': Attempt.objects.select_related(
                'student', 'quiz',
            ).order_by('-pk')[:100],
        })
    if user.role == 'teacher':
        return render(request, 'teacher_dashboard.html', {
            'classes': ClassRoom.objects.filter(teacher=user),
            'quizzes': Quiz.objects.filter(teacher=user),
            'attempts': Attempt.objects.filter(quiz__teacher=user),
        })
    now = timezone.now()
    sessions = ExamSession.objects.filter(
        quiz__enrollments__student=user,
        quiz__class_room__memberships__student=user,
        quiz__is_published=True,
        status='open',
        starts_at__lte=now,
        ends_at__gt=now,
        network_profile__enabled=True,
    ).select_related('quiz', 'quiz__class_room', 'network_profile').order_by('ends_at')
    available_sessions = [
        session for session in sessions if network_allowed(request, session)
    ]
    return render(request, 'student_dashboard.html', {
        'classes': ClassRoom.objects.filter(memberships__student=user),
        'quizzes': Quiz.objects.filter(
            enrollments__student=user,
            is_published=True,
        ),
        'available_sessions': available_sessions,
        'attempts': user.attempts.all(),
    })


@login_required
def profile_view(request):
    return render(request, 'profile.html')


@require_http_methods(['GET', 'POST'])
def login_view(request):
    if request.user.is_authenticated:
        return redirect('exam_enter' if _login_qr_exam(request) else 'dashboard')

    form = LoginForm(request.POST or None)
    if request.method == 'POST':
        if request.POST.get('cancel_qr'):
            request.session.pop('join_session', None)
            request.session.pop('join_digest', None)
            return _login_render(request, LoginForm())
        username = request.POST.get('username', '')[:150]
        if limited(request, 'login', username):
            return HttpResponse('Too many attempts. Try again in five minutes.', status=429)
        if form.is_valid():
            candidate = authenticate(
                request,
                username=form.cleaned_data['username'],
                password=form.cleaned_data['password'],
            )
            with transaction.atomic():
                user = (
                    User.objects.select_for_update().filter(pk=candidate.pk).first()
                    if candidate else None
                )
                if (user and candidate and user.password == candidate.password
                        and user.is_active and user.status in ('active', 'pending_mfa')):
                    if user.status == 'pending_mfa':
                        user.status = 'active'
                        user.security_version += 1
                        user.setup_expires_at = None
                        user.save(update_fields=[
                            'status', 'security_version', 'setup_expires_at',
                        ])
                    return _finish_login(request, user, candidate.backend)
                audit(user, 'auth.login_failed')
            form.add_error(None, 'Invalid credentials or account unavailable.')
    return _login_render(request, form)


@require_POST
def logout_view(request):
    if request.user.is_authenticated:
        user = request.user
        audit(user, 'auth.logout')
        if (user.role == 'student'
                and user.active_session == request.session.session_key):
            user.active_session = ''
            user.save(update_fields=['active_session'])
        logout(request)
    else:
        request.session.flush()
    return redirect('login')


class ActivationPasswordForm(forms.Form):
    password = forms.CharField(
        widget=forms.PasswordInput,
        label='New password (12+ characters)',
    )
    confirm = forms.CharField(widget=forms.PasswordInput, label='Confirm password')

    def clean(self):
        data = super().clean()
        if data.get('password') != data.get('confirm'):
            raise forms.ValidationError('Passwords do not match.')
        return data


def _private_response(response):
    response['Cache-Control'] = 'no-store, no-cache, max-age=0, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _invalid_activation(request):
    audit(None, 'account.activation_rejected')
    return _private_response(render(
        request,
        'activation.html',
        {'form': ActivationPasswordForm(), 'login_page': True, 'invalid_invitation': True},
        status=400,
    ))


@require_http_methods(['GET', 'POST'])
def activate_view(request):
    raw = (
        request.GET.get('token', '').strip()
        or request.session.get('account_token_activate', '')
    ) if request.method == 'GET' else request.session.get('account_token_activate', '')
    if request.method == 'GET' and request.GET.get('token'):
        valid = AccountToken.objects.filter(
            digest=digest(raw),
            purpose='activate',
            used_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
            user__status='pending_activation',
            user__is_active=True,
        ).exists()
        if not valid:
            return _invalid_activation(request)
        request.session['account_token_activate'] = raw
        return _private_response(redirect(request.path))
    if not raw:
        return _invalid_activation(request)

    form = ActivationPasswordForm(request.POST or None)
    if request.method == 'POST':
        if limited(request, 'activation', digest(raw)):
            return _private_response(HttpResponse('Too many attempts.', status=429))
        if form.is_valid():
            candidate = AccountToken.objects.filter(
                digest=digest(raw), purpose='activate',
            ).first()
            with transaction.atomic():
                user = (
                    User.objects.select_for_update().filter(pk=candidate.user_id).first()
                    if candidate else None
                )
                token = (
                    AccountToken.objects.select_for_update().filter(pk=candidate.pk).first()
                    if candidate else None
                )
                now = timezone.now()
                valid = bool(
                    user and token and user.is_active
                    and user.status == 'pending_activation'
                    and token.used_at is None and token.revoked_at is None
                    and now < token.expires_at
                )
                if not valid:
                    return _invalid_activation(request)
                try:
                    validate_password(form.cleaned_data['password'], user)
                except ValidationError as error:
                    for message in error.messages:
                        form.add_error('password', message)
                else:
                    user.set_password(form.cleaned_data['password'])
                    user.status = 'active'
                    user.security_version += 1
                    user.active_session = ''
                    user.save(update_fields=[
                        'password', 'status', 'security_version', 'active_session',
                    ])
                    token.used_at = now
                    token.save(update_fields=['used_at'])
                    request.session.pop('account_token_activate', None)
                    audit(user, 'account.activation_completed')
                    return _private_response(redirect('login'))
    return _private_response(render(request, 'activation.html', {
        'form': form,
        'login_page': True,
        'invalid_invitation': False,
    }))


class PasswordChangeForm(forms.Form):
    current_password = forms.CharField(widget=forms.PasswordInput, label='Current password')
    new_password = forms.CharField(
        widget=forms.PasswordInput,
        label='New password (12+ characters)',
    )
    confirm = forms.CharField(widget=forms.PasswordInput, label='Confirm new password')

    def clean(self):
        data = super().clean()
        if data.get('new_password') != data.get('confirm'):
            raise forms.ValidationError('Passwords do not match.')
        return data


@login_required
@require_http_methods(['GET', 'POST'])
def change_password_view(request):
    form = PasswordChangeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        if not check_password(form.cleaned_data['current_password'], request.user.password):
            form.add_error('current_password', 'Current password is incorrect.')
        else:
            try:
                validate_password(form.cleaned_data['new_password'], request.user)
            except ValidationError as error:
                for message in error.messages:
                    form.add_error('new_password', message)
            else:
                request.user.set_password(form.cleaned_data['new_password'])
                request.user.security_version += 1
                request.user.active_session = ''
                request.user.save(update_fields=[
                    'password', 'security_version', 'active_session',
                ])
                audit(request.user, 'auth.password_changed')
                logout(request)
                messages.success(request, 'Password changed. Please sign in again.')
                return redirect('login')
    return page(request, 'Change Password', form)


def _full_name_parts(value):
    parts = value.strip().split(maxsplit=1)
    return parts[0], parts[1] if len(parts) == 2 else ''


@login_required
@require_http_methods(['GET', 'POST'])
def create_student_view(request):
    if request.user.role != 'teacher':
        return HttpResponse(status=403)
    form = CreateStudentForm(request.POST or None, user=request.user)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        first_name, last_name = _full_name_parts(data['full_name'])
        temporary_password = secrets.token_urlsafe(18)
        try:
            with transaction.atomic():
                student = User(
                    username=data['institution_id'],
                    institution_id=data['institution_id'],
                    first_name=first_name,
                    last_name=last_name,
                    role='student',
                    status='active',
                    is_active=True,
                )
                student.set_password(temporary_password)
                student.save()
                ClassMembership.objects.create(
                    class_room=data['class_id'],
                    student=student,
                )
                audit(request.user, 'account.student_created',
                      user_id=student.pk, class_id=data['class_id'].pk)
            return _private_response(page(
                request,
                'Student account created',
                account=student,
                account_label='Student',
                temporary_password=temporary_password,
                notice='Share this temporary password privately. It will not be shown again.',
            ))
        except IntegrityError:
            form.add_error(None, 'Student ID or username is already in use.')
    return page(request, 'Add Student', form)


@login_required
@require_http_methods(['GET', 'POST'])
def create_teacher_view(request):
    if request.user.role != 'admin':
        return HttpResponse(status=403)
    form = CreateTeacherForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        first_name, last_name = _full_name_parts(data['full_name'])
        temporary_password = secrets.token_urlsafe(18)
        try:
            with transaction.atomic():
                teacher = User(
                    username=data['username'],
                    email=data['email'],
                    verified_email=data['email'],
                    first_name=first_name,
                    last_name=last_name,
                    role='teacher',
                    status='active',
                    is_active=True,
                )
                teacher.set_password(temporary_password)
                teacher.save()
                audit(request.user, 'account.teacher_created', user_id=teacher.pk)
            return _private_response(page(
                request,
                'Teacher account created',
                account=teacher,
                account_label='Teacher',
                temporary_password=temporary_password,
                notice='Share this temporary password privately.',
            ))
        except IntegrityError:
            form.add_error(None, 'Username or email address is already in use.')
    return page(request, 'Register teacher', form)


@login_required
@require_http_methods(['GET', 'POST'])
def activation_invitations_view(request):
    actor = request.user
    if actor.role == 'admin':
        users = User.objects.filter(status='pending_activation').order_by('role', 'username')
    elif actor.role == 'teacher':
        users = User.objects.filter(
            role='student',
            status='pending_activation',
            class_memberships__class_room__teacher=actor,
        ).distinct().order_by('username')
    else:
        return HttpResponse(status=403)
    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()
        if not reason:
            return HttpResponse('A reason is required.', status=400)
        user = users.filter(pk=request.POST.get('user_id'), is_active=True).first()
        if not user:
            return HttpResponse(status=404)
        raw = issue_token(user, 'activate', actor, delivery_method='private')
        audit(actor, 'account.activation_invitation_reissued',
              user_id=user.pk, reason=reason[:500], delivery='private')
        return _private_response(page(
            request,
            'Private activation invitation',
            link=invitation_url('activate', raw),
            notice='Deliver privately. The link expires in 30 minutes.',
        ))
    return render(request, 'activation_invitations.html', {'users': users[:500]})


@login_required
@require_http_methods(['GET', 'POST'])
def manage_users_view(request):
    if request.user.role != 'admin':
        return HttpResponse(status=403)
    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()
        if not reason:
            return HttpResponse('A reason is required.', status=400)
        with transaction.atomic():
            user = get_object_or_404(
                User.objects.select_for_update(),
                pk=request.POST.get('user_id'),
            )
            if user == request.user:
                return HttpResponse('Use another administrator for this action.', status=400)
            action = request.POST.get('action')
            if action == 'suspend':
                user.status = 'suspended'
                user.is_active = False
                user.active_session = ''
                AccountToken.objects.filter(
                    user=user,
                    used_at__isnull=True,
                    revoked_at__isnull=True,
                ).update(revoked_at=timezone.now())
            elif action == 'activate':
                if not user.has_usable_password():
                    return HttpResponse('Set a password before activating this account.', status=400)
                user.is_active = True
                user.status = 'active'
            elif action in ('reset_password', 'change_password'):
                password = request.POST.get('new_password', '')
                if not password:
                    return HttpResponse('New password is required.', status=400)
                try:
                    validate_password(password, user)
                except ValidationError as error:
                    return HttpResponse('; '.join(error.messages), status=400)
                user.set_password(password)
                user.active_session = ''
            elif action == 'reissue' and user.status == 'pending_activation':
                raw = issue_token(user, 'activate', request.user, delivery_method='private')
                user.security_version += 1
                user.save(update_fields=['security_version'])
                audit(request.user, 'account.activation_invitation_reissued',
                      user_id=user.pk, reason=reason[:500])
                return _private_response(page(
                    request,
                    'Private activation invitation',
                    link=invitation_url('activate', raw),
                ))
            else:
                return HttpResponse(status=400)
            user.security_version += 1
            user.save()
            audit(request.user, 'account.' + action,
                  user_id=user.pk, reason=reason[:500])
        return redirect('manage_users')
    return render(request, 'manage_users.html', {
        'users': User.objects.all().order_by('username')[:500],
    })


def csrf_failure(request, reason=''):
    if (request.resolver_match
            and request.resolver_match.url_name == 'activate'
            and request.method == 'POST'
            and request.META.get('HTTP_ORIGIN') == 'null'):
        return render(request, 'activation_origin_error.html', status=403)
    return default_csrf_failure(request, reason)
