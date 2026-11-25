"""Transactions lock Quiz, then ExamSession, then Attempt; time is read after locking."""
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import PermissionDenied, ValidationError
from accounts.security import audit
from .models import Attempt, ExamSession, Quiz, QuizEnrollment, Answer, SaveReceipt
from .network import network_allowed

def deadline(a,s): return min(a.deadline_at,s.closed_at) if s.closed_at else a.deadline_at

def finalize(a,s,now,submit=False):
    if a.status!='in_progress': return a
    if not submit and now<deadline(a,s) and s.status=='open': return a
    a.score=sum(x.question.marks for x in a.answers.select_related('question') if x.selected_option==x.question.correct_option)
    a.status='expired' if now>=deadline(a,s) or s.status=='closed' else 'submitted'
    a.submitted_at=now;a.save(update_fields=['score','status','submitted_at'])
    audit(a.student,'attempt.finalized',attempt_id=a.pk,status=a.status,score=a.score)
    return a

def allowed(request,s):
    if request.user.role!='student' or not network_allowed(request,s) or not s.network_profile or not s.network_profile.enabled:
        audit(request.user,'exam.access_denied',session_id=s.pk);raise PermissionDenied('Connect to the approved exam network.')
    if not QuizEnrollment.objects.filter(quiz=s.quiz,student=request.user).exists() or not s.quiz.class_room.memberships.filter(student=request.user).exists():
        raise PermissionDenied('You are not enrolled in this quiz and class.')

@transaction.atomic
def start(request,session_id):
    quiz_id=ExamSession.objects.values_list('quiz_id',flat=True).get(pk=session_id)
    Quiz.objects.select_for_update().get(pk=quiz_id)
    s=ExamSession.objects.select_for_update().get(pk=session_id)
    allowed(request,s)
    # Serialize same-student starts even across different sessions of one quiz.
    from accounts.models import User
    User.objects.select_for_update().get(pk=request.user.pk)
    a=Attempt.objects.select_for_update().filter(student=request.user,quiz=s.quiz).first()
    now=timezone.now()
    if a:
        if a.session_id!=s.pk: raise ValidationError('An attempt already exists in another session. Resume it from your dashboard.')
        return finalize(a,s,now)
    if s.status!='open' or not s.quiz.is_published or not s.starts_at<=now<s.ends_at: raise ValidationError('The exam window is closed.')
    if request.session.get('join_session')!=s.pk or request.session.get('join_digest')!=s.token_digest: raise PermissionDenied('Scan or enter the current invitation.')
    questions=list(s.quiz.questions.order_by('pk'))
    a=Attempt.objects.create(student=request.user,quiz=s.quiz,session=s,deadline_at=s.ends_at,quiz_version=s.quiz.version,question_order=[q.pk for q in questions],total_marks=sum(q.marks for q in questions))
    audit(request.user,'attempt.started',attempt_id=a.pk)
    return a

def locked_attempt(attempt_id):
    sid,quiz_id=Attempt.objects.values_list('session_id','quiz_id').get(pk=attempt_id)
    Quiz.objects.select_for_update().get(pk=quiz_id)
    s=ExamSession.objects.select_for_update().get(pk=sid)
    a=Attempt.objects.select_for_update().get(pk=attempt_id)
    return s,a

@transaction.atomic
def reconcile(attempt_id):
    s,a=locked_attempt(attempt_id)
    return finalize(a,s,timezone.now())

@transaction.atomic
def save(request,attempt_id,question_id,selected,expected,key):
    s,a=locked_attempt(attempt_id)
    if a.student_id!=request.user.pk: raise PermissionDenied()
    allowed(request,s)
    now=timezone.now();finalize(a,s,now)
    if a.status!='in_progress': return {'error':'closed'},409
    if question_id not in a.question_order or selected not in 'ABCD' or len(selected)!=1 or len(key)>64 or not key: raise ValidationError('Invalid answer request.')
    receipt=SaveReceipt.objects.filter(attempt=a,key=key).first()
    if receipt:
        if receipt.question_id_value!=question_id or receipt.selected!=selected: return {'error':'idempotency_mismatch'},409
        return {'saved':True,'version':receipt.version,'selected':receipt.selected},200
    answer=Answer.objects.filter(attempt=a,question_id=question_id).first()
    current=answer.version if answer else 0
    if current!=expected: return {'error':'stale','version':current,'selected':answer.selected_option if answer else ''},409
    if not answer: answer=Answer(attempt=a,question_id=question_id)
    answer.selected_option=selected;answer.version=current+1;answer.save()
    SaveReceipt.objects.create(attempt=a,key=key,question_id_value=question_id,selected=selected,version=answer.version)
    return {'saved':True,'version':answer.version,'selected':selected},200

@transaction.atomic
def submit(request,attempt_id):
    s,a=locked_attempt(attempt_id)
    if a.student_id!=request.user.pk: raise PermissionDenied()
    allowed(request,s)
    return finalize(a,s,timezone.now(),submit=True)

@transaction.atomic
def close(session_id,teacher,reason):
    quiz_id=ExamSession.objects.values_list('quiz_id',flat=True).get(pk=session_id)
    Quiz.objects.select_for_update().get(pk=quiz_id)
    s=ExamSession.objects.select_for_update().get(pk=session_id)
    if teacher.role!='teacher' or (s.quiz.teacher_id!=teacher.pk or s.quiz.class_room.teacher_id!=teacher.pk): raise PermissionDenied()
    if not reason.strip(): raise ValidationError('A closure reason is required.')
    if s.status!='closed':
        s.status='closed';s.closed_at=timezone.now();s.closure_reason=reason[:1000];s.save()
        audit(teacher,'exam.closed',session_id=s.pk,reason=s.closure_reason)
    for a in s.attempts.select_for_update().filter(status='in_progress'): finalize(a,s,timezone.now())
    return s
