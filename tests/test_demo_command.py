import os
import secrets
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from accounts.models import User
from classes.models import ClassMembership, ClassRoom
from exams.models import Quiz


class DemoCommandTests(TestCase):
    def test_seed_demo_is_explicit_idempotent_and_does_not_replace_passwords(self):
        variables = {
            'SECURE_EXAM_DEMO_ADMIN_PASSWORD': secrets.token_urlsafe(24),
            'SECURE_EXAM_DEMO_TEACHER_PASSWORD': secrets.token_urlsafe(24),
            'SECURE_EXAM_DEMO_STUDENT_PASSWORD': secrets.token_urlsafe(24),
        }
        output = StringIO()
        with patch.dict(os.environ, variables):
            call_command('seed_demo', stdout=output)
        for password in variables.values():
            self.assertNotIn(password, output.getvalue())

        admin = User.objects.get(username='demo_admin')
        teacher = User.objects.get(username='demo_teacher')
        student = User.objects.get(username='demo_student1')
        self.assertEqual((admin.role, teacher.role, student.role),
                         ('admin', 'teacher', 'student'))
        self.assertTrue(admin.check_password(variables['SECURE_EXAM_DEMO_ADMIN_PASSWORD']))
        self.assertTrue(teacher.check_password(variables['SECURE_EXAM_DEMO_TEACHER_PASSWORD']))
        self.assertTrue(student.check_password(variables['SECURE_EXAM_DEMO_STUDENT_PASSWORD']))
        class_room = ClassRoom.objects.get(teacher=teacher)
        self.assertTrue(ClassMembership.objects.filter(
            class_room=class_room, student=student,
        ).exists())
        self.assertTrue(Quiz.objects.filter(
            teacher=teacher, class_room=class_room, is_published=False,
        ).exists())

        replacement = secrets.token_urlsafe(24)
        admin.set_password(replacement)
        admin.save(update_fields=['password'])
        call_command('seed_demo', stdout=StringIO())
        admin.refresh_from_db()
        self.assertTrue(admin.check_password(replacement))
