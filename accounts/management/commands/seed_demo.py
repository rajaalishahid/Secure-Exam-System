"""Create a small synthetic demonstration dataset without changing existing accounts."""
import getpass
import os
import sys

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from accounts.models import User
from classes.models import ClassRoom, ClassMembership
from exams.models import Quiz, Question


DEMO_ACCOUNTS = (
    ('demo_admin', 'admin', None, 'SECURE_EXAM_DEMO_ADMIN_PASSWORD'),
    ('demo_teacher', 'teacher', None, 'SECURE_EXAM_DEMO_TEACHER_PASSWORD'),
    ('demo_student1', 'student', 'demo_student1', 'SECURE_EXAM_DEMO_STUDENT_PASSWORD'),
)


class Command(BaseCommand):
    help = 'Create the initial synthetic admin, teacher, student, class, and draft quiz.'

    def handle(self, *args, **options):
        existing = {}
        missing = []
        for username, role, _, _ in DEMO_ACCOUNTS:
            user = User.objects.filter(username=username).first()
            if user:
                if user.role != role:
                    raise CommandError(
                        f'The existing {username} account has a different role; it was not changed.'
                    )
                existing[username] = user
            else:
                missing.append((username, role))

        passwords = self._passwords(missing)
        try:
            with transaction.atomic():
                users = dict(existing)
                for username, role, student_id, _ in DEMO_ACCOUNTS:
                    if username in users:
                        continue
                    user = User(
                        username=username,
                        role=role,
                        status='active',
                        institution_id=student_id,
                    )
                    user.set_password(passwords[username])
                    user.save()
                    users[username] = user

                teacher = users['demo_teacher']
                student = users['demo_student1']
                class_room = ClassRoom.objects.filter(
                    teacher=teacher, name='Synthetic Network Security Lab',
                ).first()
                if class_room is None:
                    class_room = ClassRoom.objects.create(
                        teacher=teacher, name='Synthetic Network Security Lab',
                    )
                ClassMembership.objects.get_or_create(
                    class_room=class_room, student=student,
                )
                quiz = Quiz.objects.filter(
                    teacher=teacher, class_room=class_room,
                    title='Network Security Fundamentals',
                ).first()
                if quiz is None:
                    quiz = Quiz.objects.create(
                        teacher=teacher,
                        class_room=class_room,
                        title='Network Security Fundamentals',
                    )
                    questions = (
                        ('What protects data in transit?', ('TLS', 'Plain HTTP', 'A public QR', 'A username'), 'A'),
                        ('Which identity proof is used to sign in?', ('Username and password', 'Shared exam code', 'Classroom SSID', 'Browser address'), 'A'),
                        ('Who determines the exam deadline?', ('Browser clock', 'Student device', 'Server clock', 'QR scanner'), 'C'),
                    )
                    for prompt, options, correct in questions:
                        Question.objects.create(
                            quiz=quiz,
                            prompt=prompt,
                            option_a=options[0],
                            option_b=options[1],
                            option_c=options[2],
                            option_d=options[3],
                            correct_option=correct,
                            marks=2,
                        )
        except IntegrityError as exc:
            raise CommandError('Demo setup conflicted with existing data; no partial changes were kept.') from exc

        if missing:
            self.stdout.write(self.style.SUCCESS('Synthetic demo setup complete.'))
            for username, role, _, _ in DEMO_ACCOUNTS:
                self.stdout.write(f'{username} ({role})')
        else:
            self.stdout.write('Synthetic demo accounts already exist; no account or password was changed.')

    def _passwords(self, missing):
        if not missing:
            return {}

        values = {username: os.environ.get(env_name, '') for username, _, _, env_name in DEMO_ACCOUNTS}
        supplied = [values[username] for username, _ in missing]
        if any(supplied) and not all(supplied):
            raise CommandError(
                'Set a private demo password environment variable for every new account, or leave them all unset and run interactively.'
            )
        if not any(supplied):
            if not sys.stdin.isatty():
                raise CommandError(
                    'Run interactively to enter demo passwords, or set the private SECURE_EXAM_DEMO_*_PASSWORD variables.'
                )
            for username, _ in missing:
                while True:
                    password = getpass.getpass(f'Password for {username}: ')
                    confirmation = getpass.getpass(f'Confirm password for {username}: ')
                    if password != confirmation:
                        self.stderr.write('Passwords did not match; try again.')
                        continue
                    values[username] = password
                    break

        for username, role in missing:
            user = User(username=username, role=role)
            try:
                validate_password(values[username], user)
            except ValidationError as exc:
                raise CommandError(
                    f'The supplied password for {username} does not meet the configured password policy.'
                ) from exc
        return {username: values[username] for username, _ in missing}
