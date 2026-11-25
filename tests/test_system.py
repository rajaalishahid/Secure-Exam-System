import time, threading
import re
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import patch
from django.test import TestCase,TransactionTestCase,Client,RequestFactory
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import connection,transaction,close_old_connections,IntegrityError
from django.core.management import call_command
from accounts.models import User,AccountToken
from accounts.security import issue_token,digest
from classes.models import ClassRoom,ClassMembership
from exams.models import *
from exams.services import start,save,submit,close,reconcile
from exams.qr import rotate,invitation,qr_url
from audit.models import AuditEvent
PASSWORD='Synthetic-Test-Passphrase-579!'
class Fixture:
    def setup_data(self):
        self.teacher=User.objects.create_user(username='teacher',password=PASSWORD,role='teacher',status='active')
        self.student=User.objects.create_user(username='student',password=PASSWORD,role='student',status='active',institution_id='ST001')
        self.other=User.objects.create_user(username='other',password=PASSWORD,role='student',status='active',institution_id='ST002')
        self.admin=User.objects.create_user(username='adminuser',password=PASSWORD,role='admin',status='active')
        self.c=ClassRoom.objects.create(name='Test',teacher=self.teacher)
        ClassMembership.objects.create(class_room=self.c,student=self.student)
        self.q=Quiz.objects.create(teacher=self.teacher,class_room=self.c,title='Test quiz')
        self.question=Question.objects.create(quiz=self.q,prompt='2+2?',option_a='4',option_b='3',option_c='2',option_d='1',correct_option='A',marks=5)
        self.q.is_published=True;self.q.status='published';self.q.save()
        QuizEnrollment.objects.create(quiz=self.q,student=self.student)
        self.n=NetworkProfile.objects.create(name='Loopback',cidrs='127.0.0.1/32\n::1/128')
        now=timezone.now()
        self.s=ExamSession.objects.create(quiz=self.q,starts_at=now,ends_at=now+timezone.timedelta(seconds=900),status='open',network_profile=self.n,authorized_networks=self.n.cidrs)
        rotate(self.s)
    def req(self,user=None,ip='127.0.0.1'):
        r=RequestFactory().post('/',REMOTE_ADDR=ip);r.user=user or self.student;r.session={'join_session':self.s.pk,'join_digest':self.s.token_digest};return r
    def auth_client(self,u):
        c=Client()
        if u.is_active and u.status=='active':
            response=c.post('/login/',{'username':u.username,'password':PASSWORD})
            if response.status_code!=302 or response.url!='/dashboard/':
                raise AssertionError('Synthetic username/password login did not reach the role dashboard.')
        return c

class CoreTests(Fixture,TestCase):
    def setUp(self): self.setup_data()
    def test_one_attempt_and_same_deadline(self):
        a=start(self.req(),self.s.pk);b=start(self.req(),self.s.pk)
        self.assertEqual(a.pk,b.pk);self.assertEqual(a.deadline_at,self.s.ends_at)
        s2=ExamSession.objects.create(quiz=self.q,ends_at=self.s.ends_at+timezone.timedelta(hours=1),status='open',network_profile=self.n,authorized_networks=self.n.cidrs)
        with self.assertRaises(ValidationError): start(self.req(),s2.pk)
        a.refresh_from_db();self.assertEqual(a.deadline_at,self.s.ends_at)
    def test_no_automatic_enrollment(self):
        with self.assertRaises(PermissionDenied): start(self.req(self.other),self.s.pk)
        self.assertFalse(ClassMembership.objects.filter(student=self.other).exists())
    def test_wrong_network_and_forged_header(self):
        r=self.req(ip='203.0.113.3');r.META['HTTP_X_FORWARDED_FOR']='127.0.0.1'
        with self.assertRaises(PermissionDenied): start(r,self.s.pk)
    def test_missing_global_hotspot_cidr_does_not_bypass_session_network(self):
        with self.settings(EXAM_HOTSPOT_CIDR='',EXAM_TRUSTED_PROXY_CIDRS=[]):
            self.assertIsNotNone(start(self.req(),self.s.pk))
            with self.assertRaises(PermissionDenied): start(self.req(ip='203.0.113.9'),self.s.pk)
    def test_ipv6_policy(self):
        self.assertIsNotNone(start(self.req(ip='::1'),self.s.pk))
        with self.assertRaises(PermissionDenied): start(self.req(ip='2001:db8::1'),self.s.pk)
    def test_idempotency_and_stale_save(self):
        r=self.req();a=start(r,self.s.pk)
        first=save(r,a.pk,self.question.pk,'A',0,'key1');self.assertEqual(first[1],200)
        self.assertEqual(save(r,a.pk,self.question.pk,'A',0,'key1'),first)
        self.assertEqual(save(r,a.pk,self.question.pk,'B',0,'key2')[1],409)
        self.assertEqual(a.answers.get().selected_option,'A')
    def test_foreign_question_rejected(self):
        a=start(self.req(),self.s.pk)
        with self.assertRaises(ValidationError):save(self.req(),a.pk,999999,'A',0,'key')
    def test_exact_deadline_rejects_and_grades(self):
        r=self.req();a=start(r,self.s.pk);save(r,a.pk,self.question.pk,'A',0,'one')
        with patch('exams.services.timezone.now',return_value=self.s.ends_at): self.assertEqual(save(r,a.pk,self.question.pk,'B',1,'two')[1],409)
        a.refresh_from_db();self.assertEqual((a.status,a.score),('expired',5))
    def test_save_after_deadline_is_rejected_and_attempt_stays_finalized(self):
        r=self.req();a=start(r,self.s.pk);save(r,a.pk,self.question.pk,'A',0,'before-deadline')
        ClassMembership.objects.create(class_room=self.c,student=self.other)
        QuizEnrollment.objects.create(quiz=self.q,student=self.other)
        other_attempt=start(self.req(self.other),self.s.pk)
        with patch('exams.services.timezone.now',return_value=self.s.ends_at+timezone.timedelta(microseconds=1)):
            result=save(self.req(self.other),other_attempt.pk,self.question.pk,'B',0,'after-deadline')
        self.assertEqual(result[1],409)
        other_attempt.refresh_from_db()
        self.assertEqual(other_attempt.status,'expired')
        self.assertEqual(other_attempt.answers.count(),0)
        self.assertEqual(save(self.req(self.other),other_attempt.pk,self.question.pk,'A',0,'later')[1],409)
    def test_submit_idempotent_and_no_more_writes(self):
        r=self.req();a=start(r,self.s.pk);save(r,a.pk,self.question.pk,'A',0,'one')
        self.assertEqual(submit(r,a.pk).score,5);self.assertEqual(submit(r,a.pk).score,5)
        self.assertEqual(save(r,a.pk,self.question.pk,'B',1,'two')[1],409)
        self.assertEqual(AuditEvent.objects.filter(action='attempt.finalized').count(),1)
    def test_close_grades_and_blocks(self):
        r=self.req();a=start(r,self.s.pk);save(r,a.pk,self.question.pk,'A',0,'one')
        close(self.s.pk,self.teacher,'Lab ended');a.refresh_from_db();self.assertEqual(a.score,5);self.assertEqual(a.status,'expired')
        self.s.refresh_from_db();self.assertLess(self.s.closed_at,self.s.ends_at)
        self.assertEqual(save(r,a.pk,self.question.pk,'B',1,'two')[1],409)
    def test_network_switch_denies_save_and_submit_but_reconnect_resumes(self):
        r=self.req();a=start(r,self.s.pk);deadline=a.deadline_at
        self.assertEqual(save(r,a.pk,self.question.pk,'A',0,'on-hotspot')[1],200)
        off_network=self.req(ip='192.0.2.44')
        with self.assertRaises(PermissionDenied): save(off_network,a.pk,self.question.pk,'B',1,'off-hotspot')
        with self.assertRaises(PermissionDenied): submit(off_network,a.pk)
        a.refresh_from_db()
        self.assertEqual(a.status,'in_progress')
        self.assertEqual(a.deadline_at,deadline)
        self.assertEqual(a.answers.get(question=self.question).selected_option,'A')
        resumed=start(self.req(),self.s.pk)
        self.assertEqual(resumed.pk,a.pk)
        self.assertEqual(resumed.deadline_at,deadline)
        self.assertEqual(Attempt.objects.filter(student=self.student,quiz=self.q).count(),1)
    def test_close_requires_reason(self):
        with self.assertRaises(ValidationError): close(self.s.pk,self.teacher,'')
    def test_worker_finalizes_without_browser(self):
        a=start(self.req(),self.s.pk);Attempt.objects.filter(pk=a.pk).update(deadline_at=timezone.now()-timezone.timedelta(seconds=1))
        call_command('finalize_exams',once=True);a.refresh_from_db();self.assertEqual(a.status,'expired')
    def test_no_answer_key_before_release(self):
        a=start(self.req(),self.s.pk);submit(self.req(),a.pk);c=self.auth_client(self.student)
        r=c.get(reverse('result_view',args=[a.pk]));self.assertEqual(r.status_code,200);self.assertNotContains(r,'Correct option:')
        close(self.s.pk,self.teacher,'Done');ExamSession.objects.filter(pk=self.s.pk).update(review_released=True)
        self.assertContains(c.get(reverse('result_view',args=[a.pk])),'Correct option: A')
    def test_cannot_read_another_result(self):
        a=start(self.req(),self.s.pk);c=self.auth_client(self.other)
        self.assertEqual(c.get(reverse('result_view',args=[a.pk])).status_code,403)
    def test_password_login_preserves_quiz_attempt_result_and_audit_history(self):
        a=start(self.req(),self.s.pk)
        save(self.req(),a.pk,self.question.pk,'A',0,'history-preservation')
        submit(self.req(),a.pk)
        attempt_id=a.pk
        self.assertTrue(AuditEvent.objects.filter(action='attempt.finalized').exists())

        c=self.auth_client(self.student)
        result=c.get(reverse('result_view',args=[attempt_id]))

        self.assertEqual(result.status_code,200)
        self.assertTrue(Quiz.objects.filter(pk=self.q.pk).exists())
        self.assertEqual(Attempt.objects.get(pk=attempt_id).score,5)
        self.assertTrue(AuditEvent.objects.filter(action='attempt.finalized').exists())
    def test_published_question_not_editable(self):
        self.question.prompt='Changed'
        with self.assertRaises(Exception),transaction.atomic(): self.question.save()
    def test_pending_account_cannot_enter(self):
        self.student.status='pending_activation';self.student.save();c=self.auth_client(self.student)
        self.assertEqual(c.get('/dashboard/').status_code,302)
    def test_username_password_authenticates_directly(self):
        c=Client();r=c.post('/login/',{'username':'student','password':PASSWORD})
        self.assertEqual(r.status_code,302);self.assertEqual(r.url,'/dashboard/')
        self.assertIn('_auth_user_id',c.session)
        self.assertEqual(c.get('/dashboard/').status_code,200)
    def test_suspension_and_new_login_revoke_sessions(self):
        old=self.auth_client(self.student);new=self.auth_client(self.student)
        self.assertEqual(old.get('/dashboard/').status_code,302);self.assertEqual(new.get('/dashboard/').status_code,200)
        User.objects.filter(pk=self.student.pk).update(status='suspended')
        self.assertEqual(new.get('/dashboard/').status_code,302)
    def test_activation_scanner_get_no_consumption(self):
        u=User.objects.create(username='pending');raw=issue_token(u,'activate',self.teacher)
        c=Client();c.get('/accounts/activate/?token='+raw)
        self.assertIsNone(AccountToken.objects.get(digest=digest(raw)).used_at)
    def test_activation_once_reissue_and_expired(self):
        u=User.objects.create(username='pending');old=issue_token(u,'activate');raw=issue_token(u,'activate')
        c=Client();c.get('/accounts/activate/?token='+old)
        self.assertEqual(c.post('/accounts/activate/',{'password':PASSWORD,'confirm':PASSWORD}).status_code,400)
        c.get('/accounts/activate/?token='+raw)
        self.assertEqual(c.post('/accounts/activate/',{'password':PASSWORD,'confirm':PASSWORD}).status_code,302)
        u.refresh_from_db();self.assertEqual(u.status,'active')
        c.get('/accounts/activate/?token='+raw)
        response=c.post('/accounts/activate/',{'password':PASSWORD,'confirm':PASSWORD})
        self.assertEqual(response.status_code,400)
    def test_wrong_purpose_and_expired_token(self):
        raw=issue_token(self.student,'reset');c=Client();c.get('/accounts/activate/?token='+raw)
        self.assertEqual(c.post('/accounts/activate/',{'password':PASSWORD,'confirm':PASSWORD}).status_code,400)
    def test_reset_preserves_password_login(self):
        client=self.auth_client(self.admin)
        password='Admin-Reset-Passphrase-907!'
        response=client.post('/accounts/manage/',{
            'action':'reset_password',
            'user_id':self.student.pk,
            'reason':'Approved operator reset',
            'new_password':password,
        })
        self.assertEqual(response.status_code,302)
        self.assertFalse(Client().post('/login/',{
            'username':self.student.username,
            'password':PASSWORD,
        }).wsgi_request.user.is_authenticated)
        accepted=Client().post('/login/',{
            'username':self.student.username,
            'password':password,
        })
        self.assertEqual(accepted.status_code,302)
        self.assertEqual(accepted.url,'/dashboard/')
    def test_join_get_does_not_start(self):
        c=self.auth_client(self.student);c.get('/exams/scan/#secret')
        self.assertEqual(Attempt.objects.count(),0)
        c.post('/exams/scan/',{'code':invitation(self.s)['code']});c.get('/exams/instructions/')
        self.assertEqual(Attempt.objects.count(),0)
        self.assertEqual(c.post('/exams/instructions/').status_code,302);self.assertEqual(Attempt.objects.count(),1)
    def test_rotation_revokes_pending_invitation(self):
        c=self.auth_client(self.student);c.post('/exams/scan/',{'code':invitation(self.s)['code']});rotate(self.s)
        self.assertEqual(c.post('/exams/instructions/').status_code,403)
    def test_csrf_required(self):
        c=Client(enforce_csrf_checks=True);self.assertEqual(c.post('/login/',{}).status_code,403)
    def test_login_throttle(self):
        c=Client()
        for _ in range(9): response=c.post('/login/',{'username':'missing','password':'wrong'})
        self.assertEqual(response.status_code,429)
    def test_all_main_pages_render(self):
        for u,urls in [(self.admin,['/dashboard/','/accounts/manage/','/exams/networks/','/classes/assign/','/audit/','/accounts/teacher-create/']),(self.teacher,['/dashboard/','/accounts/student-create/','/exams/quiz/create/',f'/exams/session/{self.s.pk}/',f'/classes/{self.c.pk}/']), (self.student,['/dashboard/','/accounts/profile/','/exams/scan/'])]:
            c=self.auth_client(u)
            for url in urls: self.assertEqual(c.get(url).status_code,200,url)
    def test_audit_is_append_only(self):
        e=AuditEvent.objects.create(action='test')
        with self.assertRaises(Exception),transaction.atomic(): AuditEvent.objects.filter(pk=e.pk).delete()
    def test_audit_failure_rolls_back_start(self):
        with patch('exams.services.audit',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError): start(self.req(),self.s.pk)
        self.assertEqual(Attempt.objects.count(),0)

@skipUnless(connection.vendor in ('postgresql','mysql'),'SQL row-lock tests require PostgreSQL or MySQL')
class ConcurrencyTests(Fixture,TransactionTestCase):
    def setUp(self): self.setup_data()
    def parallel(self,fn):
        barrier=threading.Barrier(2)
        def run(_):
            close_old_connections()
            try: barrier.wait();return fn()
            finally: connection.close()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,range(2)))
    def test_two_starts_create_one_attempt(self):
        ids=self.parallel(lambda:start(self.req(),self.s.pk).pk)
        self.assertEqual(ids[0],ids[1]);self.assertEqual(Attempt.objects.count(),1)
    def test_two_saves_one_version_wins(self):
        a=start(self.req(),self.s.pk)
        import uuid
        results=self.parallel(lambda:save(self.req(),a.pk,self.question.pk,'A',0,str(uuid.uuid4()))[1])
        self.assertEqual(sorted(results),[200,409]);self.assertEqual(a.answers.get().version,1)
    def test_submit_concurrently_finalizes_once(self):
        a=start(self.req(),self.s.pk)
        self.parallel(lambda:submit(self.req(),a.pk))
        self.assertEqual(AuditEvent.objects.filter(action='attempt.finalized').count(),1)

class WorkflowTests(Fixture,TestCase):
    def setUp(self):self.setup_data()
    def test_activation_browser_post_requires_csrf_and_same_origin(self):
        u=User.objects.create(username='browseractivation')
        raw=issue_token(u,'activate',self.teacher)
        c=Client(enforce_csrf_checks=True)
        initial=c.get('/accounts/activate/?token='+raw,secure=True)
        self.assertEqual((initial.status_code,initial.url),(302,'/accounts/activate/'))
        self.assertIn('account_token_activate',c.session)
        form=c.get('/accounts/activate/',secure=True)
        self.assertEqual(form.status_code,200,form.content.decode()[:300])
        self.assertEqual(form.headers['Referrer-Policy'],'no-referrer')
        self.assertContains(form,'name="csrfmiddlewaretoken"')
        csrf_token=re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"',form.content.decode()).group(1)
        post_data={'password':PASSWORD,'confirm':PASSWORD,'csrfmiddlewaretoken':csrf_token}
        rejected=c.post('/accounts/activate/',post_data,secure=True,HTTP_ORIGIN='null')
        self.assertEqual(rejected.status_code,403)
        self.assertContains(rejected,'Open this invitation in a browser',status_code=403)
        u.refresh_from_db();self.assertEqual(u.status,'pending_activation')
        accepted=c.post('/accounts/activate/',post_data,secure=True,HTTP_ORIGIN='https://testserver',HTTP_REFERER='https://testserver/accounts/activate/')
        self.assertEqual((accepted.status_code,accepted.url),(302,'/login/'))
        u.refresh_from_db();self.assertEqual(u.status,'active')
    def test_activation_then_authenticated_login(self):
        u=User.objects.create(username='newstudent');raw=issue_token(u,'activate',self.teacher)
        c=Client();c.get('/accounts/activate/?token='+raw)
        self.assertEqual(c.post('/accounts/activate/',{'password':PASSWORD,'confirm':PASSWORD}).status_code,302)
        u.refresh_from_db();self.assertEqual(u.status,'active')
        challenge=c.post('/login/',{'username':u.username,'password':PASSWORD})
        self.assertRedirects(challenge,'/dashboard/')
        self.assertEqual(c.get('/dashboard/').status_code,200)
    def test_quiz_builder_publish_and_fixed_duration(self):
        c=self.auth_client(self.teacher)
        data={'title':'New draft','description':'','class_id':self.c.pk,'question_count':1,'question_1_prompt':'Choose A','question_1_a':'A','question_1_b':'B','question_1_c':'C','question_1_d':'D','question_1_correct':'A','question_1_marks':3}
        self.assertEqual(c.post('/exams/quiz/create/',data).status_code,302)
        q=Quiz.objects.get(title='New draft');data['title']='Edited draft'
        self.assertEqual(c.post(reverse('edit_quiz',args=[q.pk]),data).status_code,302)
        self.assertEqual(c.post(reverse('publish_quiz',args=[q.pk])).status_code,302)
        self.assertTrue(QuizEnrollment.objects.filter(quiz=q,student=self.student).exists())
        self.assertEqual(c.post(reverse('edit_quiz',args=[q.pk]),data).status_code,404)
        self.assertEqual(c.post(reverse('open_exam_session',args=[q.pk]),{'network_profile':self.n.pk,'duration_minutes':60}).status_code,302)
        s=q.sessions.get();self.assertEqual((s.ends_at-s.starts_at).total_seconds(),900)
    def test_open_exam_uses_exactly_900_seconds_without_duration_input(self):
        previous_end=self.s.ends_at
        close(self.s.pk,self.teacher,'Open a new fixed-duration session')
        c=self.auth_client(self.teacher)
        response=c.post(reverse('open_exam_session',args=[self.q.pk]),{'network_profile':self.n.pk})
        self.assertEqual(response.status_code,302)
        session=self.q.sessions.exclude(pk=self.s.pk).get()
        self.assertEqual((session.ends_at-session.starts_at).total_seconds(),900)
        self.assertEqual(session.starts_at.utcoffset(),timezone.timedelta(0))
        self.s.refresh_from_db()
        self.assertEqual(self.s.ends_at,previous_end)
    def test_open_exam_ignores_manipulated_duration_input(self):
        close(self.s.pk,self.teacher,'Open a new fixed-duration session')
        c=self.auth_client(self.teacher)
        response=c.post(reverse('open_exam_session',args=[self.q.pk]),{'network_profile':self.n.pk,'duration_minutes':'120'})
        self.assertEqual(response.status_code,302)
        session=self.q.sessions.exclude(pk=self.s.pk).get()
        self.assertEqual((session.ends_at-session.starts_at).total_seconds(),900)
    def test_profile_disable_immediately_blocks(self):
        a=start(self.req(),self.s.pk);c=self.auth_client(self.admin)
        self.assertEqual(c.post('/exams/networks/?edit='+str(self.n.pk),{'name':self.n.name,'cidrs':self.n.cidrs,'interface':'loopback'}).status_code,302)
        with self.assertRaises(PermissionDenied): save(self.req(),a.pk,self.question.pk,'A',0,'one')
    def test_csv_duplicate_rolls_back_entire_roster(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        data=b'username,institution_id,full_name,email\nnew1,ID100,New One,one@example.test\nnew2,ST001,Duplicate,two@example.test\n'
        c=self.auth_client(self.teacher);r=c.post(reverse('import_students',args=[self.c.pk]),{'file':SimpleUploadedFile('students.csv',data)})
        self.assertContains(r,'Import rejected');self.assertFalse(User.objects.filter(username='new1').exists())
    def test_csv_export_escapes_formula(self):
        from exams.views import csv_cell
        for value in ['=SUM(A1)','+1','-2','@foo',' \t=2']:self.assertTrue(csv_cell(value).startswith("'"))
    def test_different_teacher_cannot_manage_class(self):
        other=User.objects.create_user(username='teacher2',password=PASSWORD,role='teacher',status='active')
        c=self.auth_client(other)
        for url in [reverse('session_detail',args=[self.s.pk]),reverse('export_results',args=[self.s.pk])]:self.assertEqual(c.get(url).status_code,404)
        self.assertEqual(c.post(reverse('publish_quiz',args=[self.q.pk])).status_code,404)
    def test_score_revision_preserves_original(self):
        a=start(self.req(),self.s.pk);submit(self.req(),a.pk);c=self.auth_client(self.admin)
        self.assertEqual(c.post(reverse('revise_score',args=[a.pk]),{'score':3,'reason':'Approved correction'}).status_code,302)
        a.refresh_from_db();self.assertEqual(a.score,0);self.assertEqual(a.revisions.get().corrected,3)
    def test_replacement_preserves_history(self):
        a=start(self.req(),self.s.pk);submit(self.req(),a.pk);c=self.auth_client(self.admin)
        self.assertEqual(c.post(reverse('replacement_quiz',args=[self.q.pk]),{'reason':'Incident replacement'}).status_code,200)
        q=Quiz.objects.get(replaces=self.q);self.assertFalse(q.is_published);self.assertEqual(q.questions.count(),1);self.assertTrue(Attempt.objects.filter(pk=a.pk).exists())
    def test_marked_sixty_second_fixture(self):
        # Test-only fixture; the actual start view always sets 900 seconds.
        self.s.ends_at=self.s.starts_at+timezone.timedelta(seconds=60);self.s.save()
        a=start(self.req(),self.s.pk)
        with patch('exams.services.timezone.now',return_value=self.s.ends_at):
            a=reconcile(a.pk);self.assertEqual(a.status,'expired')
    def test_late_start_receives_only_the_remaining_shared_time(self):
        server_now=timezone.now()
        self.s.starts_at=server_now-timezone.timedelta(minutes=12)
        self.s.ends_at=self.s.starts_at+timezone.timedelta(seconds=900)
        self.s.save(update_fields=['starts_at','ends_at'])
        a=start(self.req(),self.s.pk)
        self.assertEqual(a.deadline_at,self.s.ends_at)
        self.assertGreater(a.deadline_at-server_now,timezone.timedelta(minutes=2,seconds=59))
        self.assertLessEqual(a.deadline_at-server_now,timezone.timedelta(minutes=3))

@skipUnless(connection.vendor in ('postgresql','mysql'),'SQL row-lock tests require PostgreSQL or MySQL')
class RaceGateTests(Fixture,TransactionTestCase):
    def setUp(self):self.setup_data()
    def test_lock_wait_past_deadline_rejects_save(self):
        a=start(self.req(),self.s.pk)
        # Hold session lock until after a short test deadline; writer must read time after the lock.
        Attempt.objects.filter(pk=a.pk).update(deadline_at=timezone.now()+timezone.timedelta(seconds=1))
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                ExamSession.objects.select_for_update().get(pk=self.s.pk)
                def writer():
                    close_old_connections()
                    try:return save(self.req(),a.pk,self.question.pk,'A',0,'late')[1]
                    finally:connection.close()
                f=pool.submit(writer);time.sleep(1.2)
            self.assertEqual(f.result(timeout=10),409)
        self.assertFalse(a.answers.exists())
    def test_close_races_start(self):
        barrier=threading.Barrier(2)
        def runner(op):
            close_old_connections()
            try:
                barrier.wait()
                if op=='close':return close(self.s.pk,self.teacher,'Race closure')
                try:return start(self.req(),self.s.pk)
                except ValidationError:return None
            finally:connection.close()
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(runner,['close','start']))
        for a in Attempt.objects.all():self.assertEqual(reconcile(a.pk).status,'expired')
    def test_activation_token_concurrent_redemption(self):
        u=User.objects.create(username='newactivation');raw=issue_token(u,'activate')
        clients=[Client(),Client()]
        for c in clients:c.get('/accounts/activate/?token='+raw)
        barrier=threading.Barrier(2)
        def redeem(c):
            close_old_connections()
            try:barrier.wait();return c.post('/accounts/activate/',{'password':PASSWORD,'confirm':PASSWORD}).status_code
            finally:connection.close()
        with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(redeem,clients))
        self.assertEqual(sorted(outcomes),[302,400])

class HotspotAndDirectEntryTests(Fixture,TestCase):
    def setUp(self):self.setup_data()
    def test_student_dashboard_lists_open_enrolled_session_and_instructions(self):
        c=self.auth_client(self.student)
        dashboard=c.get('/dashboard/')
        self.assertContains(dashboard,'Available exam sessions')
        self.assertContains(dashboard,'Open instructions')
        response=c.get(reverse('student_session_instructions',args=[self.s.pk]))
        self.assertRedirects(response,reverse('exam_instructions'))
        self.assertEqual(c.get(reverse('exam_instructions')).status_code,200)
        self.assertEqual(Attempt.objects.count(),0)
    def test_unenrolled_student_cannot_open_session_instructions(self):
        c=self.auth_client(self.other)
        response=c.get(reverse('student_session_instructions',args=[self.s.pk]))
        self.assertEqual(response.status_code,403)
        self.assertEqual(Attempt.objects.count(),0)
    def test_qr_login_goes_to_exam_not_dashboard(self):
        c=Client();self.assertEqual(c.post('/exams/scan/',{'code':invitation(self.s)['token']}).url,'/login/')
        result=c.post('/login/',{'username':'student','password':PASSWORD})
        self.assertEqual(result.url,'/exams/enter/')
        self.assertContains(c.get(result.url),'enter-exam');self.assertEqual(Attempt.objects.count(),0)
        result=c.post('/exams/enter/');self.assertEqual(result.status_code,302)
        self.assertEqual(result.url,reverse('exam_view',args=[Attempt.objects.get().pk]))
    def test_signed_in_scan_uses_direct_entry(self):
        c=self.auth_client(self.student)
        self.assertEqual(c.post('/exams/scan/',{'code':invitation(self.s)['code']}).url,'/exams/enter/')
    def test_direct_entry_rejects_unenrolled(self):
        c=self.auth_client(self.other);c.post('/exams/scan/',{'code':invitation(self.s)['code']})
        self.assertEqual(c.post('/exams/enter/').status_code,403);self.assertEqual(Attempt.objects.count(),0)
    def test_global_hotspot_gate_blocks_upstream_even_on_login(self):
        with self.settings(EXAM_HOTSPOT_CIDR='192.168.137.0/24',EXAM_TRUSTED_PROXY_CIDRS=['127.0.0.1/32']):
            c=Client()
            self.assertEqual(c.get('/login/',REMOTE_ADDR='127.0.0.1',HTTP_X_FORWARDED_FOR='192.168.1.50').status_code,403)
            self.assertEqual(c.get('/login/',REMOTE_ADDR='127.0.0.1',HTTP_X_FORWARDED_FOR='192.168.137.50').status_code,200)
            self.assertEqual(c.get('/login/',REMOTE_ADDR='192.168.1.50',HTTP_X_FORWARDED_FOR='192.168.137.50').status_code,403)
    def test_hotspot_mode_cannot_choose_different_network(self):
        from exams.views import SessionForm
        with self.settings(EXAM_HOTSPOT_CIDR='192.168.137.0/24'):
            self.assertFalse(SessionForm({'network_profile':self.n.pk}).is_valid())
    def test_resume_direct_does_not_reset_deadline(self):
        a=start(self.req(),self.s.pk);deadline_at=a.deadline_at;c=self.auth_client(self.student)
        join_code=invitation(self.s)['token']
        c.post('/exams/scan/',{'code':join_code})
        c.post('/exams/enter/')
        self.assertEqual(c.get(reverse('exam_view',args=[a.pk])).status_code,200)  # Refresh.
        self.assertEqual(c.get(reverse('exam_access',args=[a.pk])).status_code,200)
        c.post('/exams/scan/',{'code':join_code})  # Reconnect and re-enter the same session.
        c.post('/exams/enter/')
        self.assertEqual(c.get(reverse('exam_view',args=[a.pk])).status_code,200)
        self.assertEqual(Attempt.objects.count(),1)
        a.refresh_from_db();self.assertEqual(a.deadline_at,deadline_at);self.assertEqual(a.deadline_at,self.s.ends_at)


class QROriginTests(Fixture,TestCase):
    def setUp(self):self.setup_data()
    def test_qr_never_uses_127_0_0_1_when_lan_available(self):
        with self.settings(EXAM_PUBLIC_ORIGIN=''):
            with patch('exams.qr._detect_lan_ip',return_value='192.168.43.120'):
                url=qr_url(self.req(),self.s)
                self.assertNotIn('127.0.0.1',url)
                self.assertNotIn('0.0.0.0',url)
                self.assertIn('http://192.168.43.120:8000/exams/scan/?token=',url)
    def test_qr_uses_configured_exam_public_origin(self):
        with self.settings(EXAM_PUBLIC_ORIGIN='http://custom.lan:8000'):
            url=qr_url(self.req(),self.s)
            self.assertIn('http://custom.lan:8000/exams/scan/?token=',url)
            self.assertNotIn('127.0.0.1',url)
    def test_hotspot_qr_uses_configured_https_address_and_port(self):
        with self.settings(EXAM_PUBLIC_ORIGIN='https://192.168.43.120:8443'):
            url=qr_url(self.req(),self.s)
        self.assertTrue(url.startswith('https://192.168.43.120:8443/exams/scan/?token='))
        self.assertNotIn('localhost',url)
        self.assertNotIn('127.0.0.1',url)
    def test_qr_contains_valid_token(self):
        with self.settings(EXAM_PUBLIC_ORIGIN=''):
            with patch('exams.qr._detect_lan_ip',return_value='10.0.0.5'):
                url=qr_url(self.req(),self.s)
                token=invitation(self.s)['token']
                self.assertIn('?token='+token,url)
    def test_qr_falls_back_to_base_url_when_no_lan(self):
        with self.settings(EXAM_PUBLIC_ORIGIN=''):
            with patch('exams.qr._detect_lan_ip',return_value=''):
                url=qr_url(self.req(),self.s)
            self.assertNotIn('127.0.0.1',url)
            self.assertNotIn('0.0.0.0',url)
            self.assertTrue(url.startswith('http'))


class NetworkDetectionTests(Fixture,TestCase):
    def setUp(self):self.setup_data()
    def test_excludes_loopback_addresses(self):
        from detect_network import _is_excluded, detect_interfaces, select_ip
        import ipaddress
        self.assertTrue(_is_excluded(ipaddress.ip_address('127.0.0.1')))
        self.assertTrue(_is_excluded(ipaddress.ip_address('::1')))
        self.assertTrue(_is_excluded(ipaddress.ip_address('0.0.0.0')))
        self.assertTrue(_is_excluded(ipaddress.ip_address('169.254.1.1')))
        self.assertFalse(_is_excluded(ipaddress.ip_address('192.168.1.10')))
    def test_select_ip_manual_override(self):
        from detect_network import select_ip, _is_excluded
        import ipaddress
        ip,mode,ifaces=select_ip('auto',manual_ip='192.168.100.125')
        self.assertEqual(ip,'192.168.100.125')
        self.assertEqual(mode,'manual')
    def test_select_ip_rejects_loopback_manual(self):
        from detect_network import select_ip
        with self.assertRaises(ValueError):
            select_ip('auto',manual_ip='127.0.0.1')
    def test_detect_interfaces_auto_mode(self):
        from detect_network import detect_interfaces
        ifaces=detect_interfaces('auto')
        self.assertIsInstance(ifaces,list)
        for i in ifaces:
            self.assertIn('ip',i)
            self.assertIn('type',i)
            self.assertNotEqual(i['ip'],'127.0.0.1')
            self.assertNotEqual(i['ip'],'0.0.0.0')


class SettingsConfigurationTests(Fixture,TestCase):
    def setUp(self):self.setup_data()
    def test_allowed_hosts_includes_detected_ip(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ,{'EXAM_PUBLIC_ORIGIN':'http://192.168.100.125:8000'}):
            import importlib
            import secure_exam_system.settings as s
            importlib.reload(s)
            self.assertIn('192.168.100.125',s.ALLOWED_HOSTS)
            importlib.reload(s)
    def test_csrf_trusted_origins_includes_public_origin(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ,{'EXAM_PUBLIC_ORIGIN':'http://192.168.100.125:8000'}):
            import importlib
            import secure_exam_system.settings as s
            importlib.reload(s)
            self.assertIn('http://192.168.100.125:8000',s.CSRF_TRUSTED_ORIGINS)
            importlib.reload(s)

    def test_production_listener_and_database_ports_are_not_student_network_listeners(self):
        from pathlib import Path
        from secure_exam_system import settings
        source=(Path(settings.BASE_DIR)/'serve_production.py').read_text(encoding='utf-8')
        self.assertIn("host='127.0.0.1'",source)
        self.assertIn('port=8001',source)
        self.assertNotIn("host='0.0.0.0'",source)
        engine=settings.DATABASES['default']['ENGINE']
        self.assertIn(engine,{'django.db.backends.sqlite3','django.db.backends.postgresql','django.db.backends.mysql'})
        if engine=='django.db.backends.sqlite3':
            self.assertIn(settings.DATABASES['default'].get('HOST',''),{'','127.0.0.1','::1','localhost'})
        else:
            self.assertIn(settings.DATABASES['default']['HOST'],{'127.0.0.1','::1','localhost'})


class DirectExamJoinTests(Fixture,TestCase):
    def setUp(self):self.setup_data()
    def test_scan_then_login_redirects_to_exam(self):
        c=Client()
        # Student scans QR with join code - posts token
        code=invitation(self.s)['token']
        result=c.post('/exams/scan/',{'code':code})
        self.assertEqual(result.status_code,302)
        self.assertEqual(result.url,'/login/')
        # Student logs in
        result=c.post('/login/',{'username':'student', 'password':PASSWORD})
        self.assertEqual(result.status_code,302)
        self.assertEqual(result.url,'/exams/enter/')
    def test_manual_join_code_works(self):
        c=Client()
        code=invitation(self.s)['code']
        result=c.post('/exams/scan/',{'code':code})
        self.assertEqual(result.status_code,302)
        self.assertEqual(result.url,'/login/')
    def test_invalid_token_rejected(self):
        c=Client()
        result=c.post('/exams/scan/',{'code':'INVALID_TOKEN_12345'})
        self.assertEqual(result.status_code,403)
    def test_expired_session_rejected(self):
        from django.utils import timezone
        self.s.ends_at=timezone.now()-timezone.timedelta(hours=1)
        self.s.status='closed'
        self.s.save()
        c=Client()
        code=invitation(self.s)['token']
        result=c.post('/exams/scan/',{'code':code})
        self.assertEqual(result.status_code,403)
    def test_unenrolled_student_cannot_join(self):
        c=self.auth_client(self.other)
        code=invitation(self.s)['token']
        result=c.post('/exams/scan/',{'code':code})
        self.assertEqual(result.status_code,302)
        result=c.post('/exams/enter/')
        self.assertEqual(result.status_code,403)


class QRRegenerationPreservesSessionTests(Fixture,TestCase):
    def setUp(self):self.setup_data()
    def test_regenerate_qr_keeps_session_open(self):
        c=self.auth_client(self.teacher)
        token_before=invitation(self.s)['token']
        result=c.post('/exams/session/%d/rotate/'%self.s.pk)
        self.assertEqual(result.status_code,302)
        self.s.refresh_from_db()
        token_after=invitation(self.s)['token']
        self.assertNotEqual(token_before,token_after)
        self.assertEqual(self.s.status,'open')
    def test_qr_url_changes_after_rotation(self):
        from exams.qr import qr_url
        from django.test import RequestFactory
        rf=RequestFactory()
        request=rf.get('/')
        url_before=qr_url(request,self.s)
        c=self.auth_client(self.teacher)
        c.post('/exams/session/%d/rotate/'%self.s.pk)
        self.s.refresh_from_db()
        url_after=qr_url(request,self.s)
        self.assertNotEqual(url_before,url_after)


class NetworkDiagnosticsViewTests(Fixture,TestCase):
    def setUp(self):self.setup_data()
    def test_diagnostics_teacher_access(self):
        c=self.auth_client(self.teacher)
        result=c.get('/exams/session/%d/diagnostics/'%self.s.pk)
        self.assertEqual(result.status_code,200)
    def test_diagnostics_student_denied(self):
        c=self.auth_client(self.student)
        result=c.get('/exams/session/%d/diagnostics/'%self.s.pk)
        self.assertEqual(result.status_code,403)
    def test_diagnostics_admin_access(self):
        c=self.auth_client(self.admin)
        result=c.get('/exams/session/%d/diagnostics/'%self.s.pk)
        self.assertEqual(result.status_code,200)


class QRScanWorkflowTests(Fixture,TestCase):
    def setUp(self):self.setup_data()
    def test_valid_qr_token_redirects_to_exam_enter(self):
        c=Client()
        code=invitation(self.s)['token']
        result=c.get('/exams/scan/?token='+code)
        self.assertEqual(result.status_code,302)
        self.assertEqual(result.url,'/login/')
    def test_valid_qr_token_authenticated_goes_to_exam_enter(self):
        c=self.auth_client(self.student)
        code=invitation(self.s)['token']
        result=c.get('/exams/scan/?token='+code)
        self.assertEqual(result.status_code,302)
        self.assertEqual(result.url,'/exams/enter/')
    def test_invalid_token_shows_error(self):
        c=Client()
        result=c.get('/exams/scan/?token=INVALID_TOKEN')
        self.assertEqual(result.status_code,200)
        self.assertContains(result,'Invalid, expired')
    def test_expired_session_shows_error(self):
        from django.utils import timezone
        self.s.ends_at=timezone.now()-timezone.timedelta(hours=1)
        self.s.status='closed'
        self.s.save()
        c=Client()
        code=invitation(self.s)['token']
        result=c.get('/exams/scan/?token='+code)
        self.assertEqual(result.status_code,200)
        self.assertContains(result,'Invalid, expired')
    def test_scan_without_token_shows_manual_form(self):
        c=Client()
        result=c.get('/exams/scan/')
        self.assertEqual(result.status_code,200)
        self.assertContains(result,'Exam invitation code')
    def test_login_returns_to_same_exam(self):
        c=Client()
        code=invitation(self.s)['token']
        # Scan QR token -> stores session, redirects to login
        result=c.get('/exams/scan/?token='+code)
        self.assertEqual(result.status_code,302)
        self.assertEqual(result.url,'/login/')
        # Check session has join_session
        self.assertEqual(c.session['join_session'],self.s.pk)
        # Login
        result=c.post('/login/',{'username':'student','password':PASSWORD})
        self.assertEqual(result.status_code,302)
        self.assertEqual(result.url,'/exams/enter/')
