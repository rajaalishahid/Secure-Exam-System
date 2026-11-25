import re

from django.contrib.auth.hashers import check_password
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from classes.models import ClassMembership, ClassRoom
from tests.test_system import Fixture, PASSWORD


class StudentProvisioningTests(Fixture, TestCase):
    def setUp(self):
        self.setup_data()
        self.teacher_client = self.auth_client(self.teacher)

    def student_data(self, **overrides):
        data = {
            'full_name': 'New Student',
            'institution_id': 'ST003',
            'class_id': str(self.c.pk),
        }
        data.update(overrides)
        return data

    def test_teacher_creates_student_with_one_time_temporary_password(self):
        response = self.teacher_client.post(
            reverse('create_student'),
            self.student_data(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Temporary password:')
        self.assertEqual(response['Cache-Control'].split(',')[0], 'no-store')
        student = User.objects.get(username='ST003')
        self.assertEqual(student.institution_id, 'ST003')
        self.assertEqual(student.role, 'student')
        self.assertEqual(student.status, 'active')
        self.assertTrue(student.has_usable_password())
        self.assertFalse(check_password(PASSWORD, student.password))
        membership = ClassMembership.objects.get(student=student)
        self.assertEqual(membership.class_room, self.c)
        password = re.search(
            rb'Temporary password:.*?<code>([^<]+)</code>',
            response.content,
        ).group(1).decode()
        self.assertTrue(check_password(password, student.password))

    def test_duplicate_student_id_is_rejected(self):
        response = self.teacher_client.post(
            reverse('create_student'),
            self.student_data(institution_id='ST001'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already in use')
        self.assertEqual(User.objects.filter(institution_id='ST001').count(), 1)

    def test_teacher_cannot_assign_student_to_another_teachers_class(self):
        another_teacher = User.objects.create_user(
            username='another-teacher',
            password=PASSWORD,
            role='teacher',
            status='active',
        )
        another_class = ClassRoom.objects.create(
            name='Unrelated class',
            teacher=another_teacher,
        )
        response = self.teacher_client.post(
            reverse('create_student'),
            self.student_data(class_id=str(another_class.pk)),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='ST003').exists())
        class_response = self.teacher_client.get(
            reverse('class_detail', args=[another_class.pk]),
        )
        self.assertEqual(class_response.status_code, 403)

    def test_single_assigned_class_is_preselected(self):
        response = self.teacher_client.get(reverse('create_student'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'<option value="{self.c.pk}" selected>')

    def test_student_cannot_create_accounts(self):
        client = self.auth_client(self.student)
        response = client.get(reverse('create_student'))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(User.objects.count(), 4)

    def test_student_can_change_password(self):
        client = self.auth_client(self.student)
        new_password = 'New-Exam-Passphrase-907!'
        response = client.post(reverse('change_password'), {
            'current_password': PASSWORD,
            'new_password': new_password,
            'confirm': new_password,
        })
        self.assertRedirects(response, reverse('login'))
        self.assertNotIn('_auth_user_id', client.session)

        rejected = Client().post(reverse('login'), {
            'username': self.student.username,
            'password': PASSWORD,
        })
        self.assertNotIn('_auth_user_id', rejected.wsgi_request.session)
        accepted = Client().post(reverse('login'), {
            'username': self.student.username,
            'password': new_password,
        })
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(accepted.url, reverse('dashboard'))
