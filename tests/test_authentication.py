from django.test import Client, TestCase
from django.urls import Resolver404, resolve, reverse

from accounts.models import User
from tests.test_system import Fixture, PASSWORD


class PasswordAuthenticationTests(Fixture, TestCase):
    def setUp(self):
        self.setup_data()

    def test_admin_teacher_and_student_use_username_password(self):
        for user, template in (
            (self.admin, 'dashboard_admin.html'),
            (self.teacher, 'teacher_dashboard.html'),
            (self.student, 'student_dashboard.html'),
        ):
            with self.subTest(role=user.role):
                client = Client()
                response = client.post(reverse('login'), {
                    'username': user.username,
                    'password': PASSWORD,
                })
                self.assertRedirects(response, reverse('dashboard'))
                self.assertIn('_auth_user_id', client.session)
                self.assertNotIn('mfa_authenticated', client.session)
                self.assertTemplateUsed(client.get(reverse('dashboard')), template)

    def test_wrong_password_is_rejected(self):
        client = Client()
        response = client.post(reverse('login'), {
            'username': self.student.username,
            'password': 'incorrect password',
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', client.session)

    def test_suspended_account_cannot_login(self):
        self.student.status = 'suspended'
        self.student.is_active = False
        self.student.save(update_fields=['status', 'is_active'])
        client = Client()
        response = client.post(reverse('login'), {
            'username': self.student.username,
            'password': PASSWORD,
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', client.session)

    def test_suspension_invalidates_existing_session(self):
        client = Client()
        client.post(reverse('login'), {
            'username': self.student.username,
            'password': PASSWORD,
        })
        User.objects.filter(pk=self.student.pk).update(status='suspended', is_active=False)
        response = client.get(reverse('dashboard'))
        self.assertRedirects(response, '/login/?next=/dashboard/')
        self.assertNotIn('_auth_user_id', client.session)

    def test_login_has_no_otp_redirect_or_route(self):
        client = Client()
        response = client.post(reverse('login'), {
            'username': self.student.username,
            'password': PASSWORD,
        })
        self.assertRedirects(response, reverse('dashboard'))
        with self.assertRaises(Resolver404):
            resolve('/accounts/otp/')

    def test_protected_routes_redirect_anonymous_users_to_login(self):
        for path in (
            reverse('dashboard'),
            reverse('manage_users'),
            reverse('create_student'),
            reverse('create_quiz'),
            reverse('exam_enter'),
            reverse('student_session_instructions', args=[self.s.pk]),
        ):
            with self.subTest(path=path):
                response = Client().get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith('/login/?next='))

    def test_logout_ends_the_authenticated_session(self):
        client = Client()
        client.post(reverse('login'), {
            'username': self.student.username,
            'password': PASSWORD,
        })
        response = client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'))
        self.assertNotIn('_auth_user_id', client.session)

    def test_login_is_rate_limited(self):
        client = Client()
        for _ in range(9):
            response = client.post(reverse('login'), {
                'username': 'missing-user',
                'password': 'wrong',
            })
        self.assertEqual(response.status_code, 429)

    def test_login_post_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse('login'), {
            'username': self.student.username,
            'password': PASSWORD,
        })
        self.assertEqual(response.status_code, 403)
