import json
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from audit.models import AuditEvent
from classes.models import ClassMembership, ClassRoom
from exams.deletion import delete_quiz
from exams.models import (
    Answer,
    Attempt,
    ExamSession,
    NetworkProfile,
    Question,
    Quiz,
    QuizDeletionAuthorization,
    QuizEnrollment,
    SaveReceipt,
    ScoreRevision,
)
from exams.qr import invitation, rotate


PASSWORD = 'Synthetic-Deletion-Test-579!'


class SecureClient(Client):
    def get(self, *args, **kwargs):
        kwargs.setdefault('secure', True)
        return super().get(*args, **kwargs)

    def post(self, *args, **kwargs):
        kwargs.setdefault('secure', True)
        return super().post(*args, **kwargs)


class QuizDeletionFixture:
    def build_fixture(self):
        self.teacher = User.objects.create_user(
            username='deletion-teacher', password=PASSWORD, role='teacher', status='active'
        )
        self.other_teacher = User.objects.create_user(
            username='other-deletion-teacher', password=PASSWORD, role='teacher', status='active'
        )
        self.student = User.objects.create_user(
            username='deletion-student', password=PASSWORD, role='student', status='active',
            institution_id='DEL-STUDENT-1',
        )
        self.admin = User.objects.create_user(
            username='deletion-admin', password=PASSWORD, role='admin', status='active'
        )
        self.classroom = ClassRoom.objects.create(name='Deletion Test Class', teacher=self.teacher)
        self.membership = ClassMembership.objects.create(
            class_room=self.classroom, student=self.student
        )
        self.quiz = Quiz.objects.create(
            teacher=self.teacher, class_room=self.classroom, title='Quiz to delete'
        )
        self.question = Question.objects.create(
            quiz=self.quiz, prompt='Synthetic question?', option_a='A1', option_b='B1',
            option_c='C1', option_d='D1', correct_option='A', marks=5,
        )
        self.quiz.status = 'published'
        self.quiz.is_published = True
        self.quiz.published_at = timezone.now()
        self.quiz.save()
        self.enrollment = QuizEnrollment.objects.create(quiz=self.quiz, student=self.student)
        self.network = NetworkProfile.objects.create(
            name='Deletion test loopback', cidrs='127.0.0.1/32'
        )
        now = timezone.now()
        self.session = ExamSession.objects.create(
            quiz=self.quiz, starts_at=now, ends_at=now + timezone.timedelta(minutes=15),
            status='closed', network_profile=self.network, authorized_networks=self.network.cidrs,
        )
        rotate(self.session)
        self.raw_token = invitation(self.session)['token']
        self.attempt = Attempt.objects.create(
            student=self.student, quiz=self.quiz, session=self.session, status='submitted',
            deadline_at=self.session.ends_at, submitted_at=now, score=5, total_marks=5,
            question_order=[self.question.pk],
        )
        self.answer = Answer.objects.create(
            attempt=self.attempt, question=self.question, selected_option='A', version=1
        )
        self.receipt = SaveReceipt.objects.create(
            attempt=self.attempt, key='synthetic-save-key', question_id_value=self.question.pk,
            selected='A', version=1,
        )
        self.revision = ScoreRevision.objects.create(
            attempt=self.attempt, previous=4, corrected=5,
            reason='Synthetic correction', approver=self.admin,
        )
        self.other_quiz = Quiz.objects.create(
            teacher=self.teacher, class_room=self.classroom, title='Quiz to preserve'
        )
        self.replacement = Quiz.objects.create(
            teacher=self.teacher, class_room=self.classroom,
            title='Replacement to preserve', replaces=self.quiz,
        )

    def client_for(self, user, *, csrf=False):
        client = SecureClient(REMOTE_ADDR='192.168.137.2')
        response = client.post(
            reverse('login'), {'username': user.username, 'password': PASSWORD}
        )
        if response.status_code != 302:
            raise AssertionError('Synthetic account could not establish a valid application session.')
        client.handler.enforce_csrf_checks = csrf
        return client

    def delete_url(self):
        return reverse('delete_quiz', args=[self.quiz.pk])

    def confirmation_url(self):
        return reverse('confirm_delete_quiz', args=[self.quiz.pk])

    def confirmed_data(self):
        return {'confirmation': 'permanently-delete', 'reason': 'Obsolete assessment'}


@override_settings(EXAM_HOTSPOT_CIDR='192.168.137.0/24')
class QuizDeletionTests(QuizDeletionFixture, TestCase):
    def setUp(self):
        self.build_fixture()

    def test_owner_sees_confirmation_counts_and_can_delete(self):
        client = self.client_for(self.teacher)
        confirmation = client.get(self.confirmation_url())
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, 'Permanently Delete Quiz?')
        self.assertContains(confirmation, 'Total questions')
        response = client.post(self.delete_url(), self.confirmed_data())
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
        self.assertFalse(Quiz.objects.filter(pk=self.quiz.pk).exists())

    def test_authorized_admin_can_delete(self):
        response = self.client_for(self.admin).post(self.delete_url(), self.confirmed_data())
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Quiz.objects.filter(pk=self.quiz.pk).exists())

    def test_other_teacher_cannot_view_confirmation_or_delete(self):
        client = self.client_for(self.other_teacher)
        self.assertEqual(client.get(self.confirmation_url()).status_code, 403)
        self.assertEqual(client.post(self.delete_url(), self.confirmed_data()).status_code, 403)
        self.assertTrue(Quiz.objects.filter(pk=self.quiz.pk).exists())

    def test_student_cannot_view_confirmation_or_delete(self):
        client = self.client_for(self.student)
        self.assertEqual(client.get(self.confirmation_url()).status_code, 403)
        self.assertEqual(client.post(self.delete_url(), self.confirmed_data()).status_code, 403)
        self.assertTrue(Quiz.objects.filter(pk=self.quiz.pk).exists())

    def test_get_request_cannot_delete(self):
        response = self.client_for(self.teacher).get(self.delete_url())
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Quiz.objects.filter(pk=self.quiz.pk).exists())

    def test_csrf_protection_rejects_post(self):
        response = self.client_for(self.teacher, csrf=True).post(
            self.delete_url(), self.confirmed_data()
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Quiz.objects.filter(pk=self.quiz.pk).exists())

    def test_explicit_confirmation_and_reason_are_required(self):
        client = self.client_for(self.teacher)
        self.assertEqual(client.post(self.delete_url(), {'reason': 'reason'}).status_code, 400)
        self.assertEqual(
            client.post(self.delete_url(), {'confirmation': 'permanently-delete'}).status_code,
            400,
        )
        self.assertTrue(Quiz.objects.filter(pk=self.quiz.pk).exists())

    def test_all_exclusive_quiz_records_are_deleted(self):
        ids = {
            'question': self.question.pk,
            'enrollment': self.enrollment.pk,
            'session': self.session.pk,
            'attempt': self.attempt.pk,
            'answer': self.answer.pk,
            'receipt': self.receipt.pk,
            'revision': self.revision.pk,
        }
        response = self.client_for(self.teacher).post(self.delete_url(), self.confirmed_data())
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Question.objects.filter(pk=ids['question']).exists())
        self.assertFalse(QuizEnrollment.objects.filter(pk=ids['enrollment']).exists())
        self.assertFalse(ExamSession.objects.filter(pk=ids['session']).exists())
        self.assertFalse(Attempt.objects.filter(pk=ids['attempt']).exists())
        self.assertFalse(Answer.objects.filter(pk=ids['answer']).exists())
        self.assertFalse(SaveReceipt.objects.filter(pk=ids['receipt']).exists())
        self.assertFalse(ScoreRevision.objects.filter(pk=ids['revision']).exists())
        self.assertFalse(QuizDeletionAuthorization.objects.exists())

    def test_other_quizzes_and_replacement_quizzes_remain(self):
        self.client_for(self.teacher).post(self.delete_url(), self.confirmed_data())
        self.assertTrue(Quiz.objects.filter(pk=self.other_quiz.pk).exists())
        self.replacement.refresh_from_db()
        self.assertIsNone(self.replacement.replaces_id)

    def test_accounts_classes_and_memberships_remain(self):
        self.client_for(self.teacher).post(self.delete_url(), self.confirmed_data())
        for user in (self.teacher, self.other_teacher, self.student, self.admin):
            self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertTrue(ClassRoom.objects.filter(pk=self.classroom.pk).exists())
        self.assertTrue(ClassMembership.objects.filter(pk=self.membership.pk).exists())

    def test_open_exam_cannot_be_deleted(self):
        self.session.status = 'open'
        self.session.save(update_fields=['status'])
        response = self.client_for(self.teacher).post(self.delete_url(), self.confirmed_data())
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, 'Close the open exam session', status_code=409)
        self.assertTrue(Quiz.objects.filter(pk=self.quiz.pk).exists())
        self.assertTrue(Attempt.objects.filter(pk=self.attempt.pk).exists())

    def test_old_qr_and_session_urls_are_invalid_after_deletion(self):
        session_url = reverse('session_detail', args=[self.session.pk])
        scan_url = reverse('scan_exam') + '?token=' + self.raw_token
        client = self.client_for(self.teacher)
        client.post(self.delete_url(), self.confirmed_data())
        self.assertEqual(client.get(session_url).status_code, 404)
        scan = client.get(scan_url)
        self.assertEqual(scan.status_code, 200)
        self.assertContains(scan, 'Invalid, expired, or unauthorized invitation')

    def test_failed_deletion_rolls_back_every_change(self):
        with patch('exams.deletion.audit', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.client_for(self.teacher).post(self.delete_url(), self.confirmed_data())
        self.assertTrue(Quiz.objects.filter(pk=self.quiz.pk).exists())
        self.assertTrue(Question.objects.filter(pk=self.question.pk).exists())
        self.assertTrue(ExamSession.objects.filter(pk=self.session.pk).exists())
        self.assertTrue(Attempt.objects.filter(pk=self.attempt.pk).exists())
        self.assertTrue(Answer.objects.filter(pk=self.answer.pk).exists())
        self.assertTrue(ScoreRevision.objects.filter(pk=self.revision.pk).exists())
        self.replacement.refresh_from_db()
        self.assertEqual(self.replacement.replaces_id, self.quiz.pk)
        self.assertFalse(QuizDeletionAuthorization.objects.exists())

    def test_minimal_audit_event_records_actor_identifier_time_and_reason(self):
        quiz_id = self.quiz.pk
        self.client_for(self.teacher).post(self.delete_url(), self.confirmed_data())
        event = AuditEvent.objects.get(action='quiz.deleted')
        details = json.loads(event.details)
        self.assertEqual(event.actor_id, self.teacher.pk)
        self.assertEqual(details['quiz_id'], quiz_id)
        self.assertEqual(details['reason'], 'Obsolete assessment')
        self.assertIsNotNone(event.created_at)
        forbidden = {'title', 'prompt', 'selected_option', 'score', 'answers', 'questions'}
        self.assertTrue(forbidden.isdisjoint(details))

    def test_success_message_and_dashboards_no_longer_contain_quiz(self):
        response = self.client_for(self.teacher).post(
            self.delete_url(), self.confirmed_data(), follow=True
        )
        self.assertContains(response, 'Quiz and all associated records deleted successfully.')
        self.assertNotContains(response, 'Quiz to delete')
        student_dashboard = self.client_for(self.student).get(reverse('dashboard'))
        self.assertNotContains(student_dashboard, 'Quiz to delete')


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL concurrency database is not configured')
class PostgreSQLQuizDeletionConcurrencyTests(QuizDeletionFixture, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.build_fixture()

    def test_concurrent_deletions_serialize_without_partial_data(self):
        quiz_id = self.quiz.pk
        actor_id = self.teacher.pk

        def worker():
            close_old_connections()
            try:
                actor = User.objects.get(pk=actor_id)
                delete_quiz(actor=actor, quiz_id=quiz_id, reason='Concurrent synthetic test')
                return 'deleted'
            except Quiz.DoesNotExist:
                return 'already-deleted'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: worker(), range(2)))
        self.assertCountEqual(results, ['deleted', 'already-deleted'])
        self.assertFalse(Quiz.objects.filter(pk=quiz_id).exists())
        self.assertFalse(Attempt.objects.filter(quiz_id=quiz_id).exists())
        self.assertFalse(QuizDeletionAuthorization.objects.exists())
