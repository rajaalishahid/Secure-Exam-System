import json,csv,secrets,ipaddress,socket
from io import BytesIO
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.http import HttpResponse,JsonResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.utils import timezone
from django.urls import reverse
from django.views.decorators.http import require_GET,require_POST,require_http_methods
from accounts.security import audit,digest,limited
from accounts.views import page
from classes.models import ClassRoom
from .models import Quiz,Question,ExamSession,Attempt,QuizEnrollment,NetworkProfile,ScoreRevision
from .forms import CreateQuizForm
from .network import normalize_networks,network_allowed,parse_networks
from .qr import rotate,invitation,qr_url
from . import services
from .deletion import can_delete_quiz, delete_quiz
from detect_network import detect_interfaces

EXAM_DURATION_SECONDS = 900

@login_required
@require_http_methods(['GET','POST'])
def create_quiz_view(request,quiz_id=None):
    if request.user.role!='teacher': raise PermissionDenied()
    existing=get_object_or_404(Quiz,pk=quiz_id,teacher=request.user,class_room__teacher=request.user,is_published=False) if quiz_id else None
    initial={}
    if existing:
        initial={'title':existing.title,'description':existing.description,'class_id':existing.class_room_id,'question_count':existing.questions.count()}
        for i,q in enumerate(existing.questions.order_by('pk'),1):
            for field,value in {'prompt':q.prompt,'a':q.option_a,'b':q.option_b,'c':q.option_c,'d':q.option_d,'correct':q.correct_option,'marks':q.marks}.items():initial[f'question_{i}_{field}']=value
    form=CreateQuizForm(request.POST or None,user=request.user,initial=initial)
    # Only admin-assigned classes may be used.
    form.fields.pop('new_class_name',None)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            d=form.cleaned_data
            if existing:
                quiz=Quiz.objects.select_for_update().get(pk=existing.pk)
                if quiz.is_published: return HttpResponse('Published quiz is immutable.',status=409)
                quiz.title=d['title'];quiz.description=d['description'];quiz.class_room=d['class_id'];quiz.save();quiz.questions.all().delete()
            else: quiz=Quiz.objects.create(teacher=request.user,class_room=d['class_id'],title=d['title'],description=d['description'])
            for i in range(1,d['question_count']+1):
                p=f'question_{i}_'
                Question.objects.create(quiz=quiz,prompt=d[p+'prompt'],option_a=d[p+'a'],option_b=d[p+'b'],option_c=d[p+'c'],option_d=d[p+'d'],correct_option=d[p+'correct'],marks=d[p+'marks'])
            audit(request.user,'quiz.created',quiz_id=quiz.pk)
        return redirect('dashboard')
    return render(request,'create_quiz.html',{'form':form,'question_fields':form.question_rows(),'quiz':existing})


def _quiz_deletion_context(quiz, error=''):
    return {
        'quiz': quiz,
        'total_questions': quiz.questions.count(),
        'total_enrolled_students': quiz.enrollments.count(),
        'total_attempts': quiz.attempts.count(),
        'total_submitted_results': quiz.attempts.filter(status__in=('submitted', 'expired')).count(),
        'has_open_session': quiz.sessions.filter(status='open').exists(),
        'error': error,
    }


@login_required
@require_GET
def confirm_delete_quiz_view(request, quiz_id):
    if request.user.role not in ('teacher', 'admin'):
        raise PermissionDenied()
    quiz = get_object_or_404(Quiz.objects.select_related('class_room'), pk=quiz_id)
    if not can_delete_quiz(request.user, quiz):
        raise PermissionDenied()
    return render(request, 'delete_quiz_confirm.html', _quiz_deletion_context(quiz))


@login_required
@require_POST
def delete_quiz_view(request, quiz_id):
    if request.user.role not in ('teacher', 'admin'):
        raise PermissionDenied()
    if request.POST.get('confirmation') != 'permanently-delete':
        return HttpResponse('Explicit deletion confirmation is required.', status=400)
    try:
        delete_quiz(actor=request.user, quiz_id=quiz_id, reason=request.POST.get('reason', ''))
    except Quiz.DoesNotExist:
        return HttpResponse('Quiz not found.', status=404)
    except PermissionDenied:
        raise
    except ValidationError as exc:
        quiz = get_object_or_404(Quiz.objects.select_related('class_room'), pk=quiz_id)
        if not can_delete_quiz(request.user, quiz):
            raise PermissionDenied()
        return render(
            request,
            'delete_quiz_confirm.html',
            _quiz_deletion_context(quiz, '; '.join(exc.messages)),
            status=409 if quiz.sessions.filter(status='open').exists() else 400,
        )
    messages.success(request, 'Quiz and all associated records deleted successfully.')
    return redirect('dashboard')

@login_required
@require_POST
@transaction.atomic
def publish_quiz_view(request,quiz_id):
    q=get_object_or_404(Quiz.objects.select_for_update(),pk=quiz_id,teacher=request.user,class_room__teacher=request.user)
    if request.user.role!='teacher': raise PermissionDenied()
    if q.is_published: return redirect('dashboard')
    students=q.class_room.memberships.filter(student__is_active=True).values_list('student_id',flat=True)
    if not q.questions.exists() or not students: return HttpResponse('Add questions and a class roster before publication.',status=400)
    if any(x.correct_option not in 'ABCD' or x.marks<=0 for x in q.questions.all()): return HttpResponse(status=400)
    QuizEnrollment.objects.bulk_create([QuizEnrollment(quiz=q,student_id=uid) for uid in students])
    q.status='published';q.is_published=True;q.published_at=timezone.now();q.save()
    audit(request.user,'quiz.published',quiz_id=q.pk,version=q.version)
    return redirect('dashboard')

class SessionForm(forms.Form):
    network_profile=forms.ModelChoiceField(queryset=NetworkProfile.objects.filter(enabled=True))
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if settings.EXAM_HOTSPOT_CIDR:
            try:
                allowed_networks=parse_networks(settings.EXAM_HOTSPOT_CIDR)
            except ValidationError:
                allowed_networks=[]
            compatible_ids=[]
            for profile in NetworkProfile.objects.filter(enabled=True):
                try:
                    profile_networks=parse_networks(profile.cidrs)
                except ValidationError:
                    continue
                if any(profile_network.version==allowed.version and profile_network.overlaps(allowed)
                       for profile_network in profile_networks for allowed in allowed_networks):
                    compatible_ids.append(profile.pk)
            queryset=NetworkProfile.objects.filter(pk__in=compatible_ids,enabled=True)
            self.fields['network_profile'].queryset=queryset
            self.fields['network_profile'].initial=queryset.first()

@login_required
@require_http_methods(['GET','POST'])
def open_session_view(request,quiz_id):
    q=get_object_or_404(Quiz,pk=quiz_id,teacher=request.user,class_room__teacher=request.user,is_published=True)
    if request.user.role!='teacher': raise PermissionDenied()
    form=SessionForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            Quiz.objects.select_for_update().get(pk=q.pk)
            now=timezone.now()
            if q.sessions.filter(status='open',ends_at__gt=now).exists(): return HttpResponse('An exam session is already open.',status=409)
            n=form.cleaned_data['network_profile']
            s=ExamSession.objects.create(quiz=q,starts_at=now,ends_at=now+timezone.timedelta(seconds=EXAM_DURATION_SECONDS),status='open',network_profile=n,network_name=n.name,authorized_networks=n.cidrs)
            rotate(s);audit(request.user,'exam.started',session_id=s.pk)
        return redirect('session_detail',session_id=s.pk)
    return page(request,'Start exam',form,notice='The exam always runs for 15 minutes from the time you start it. Late arrivals receive only the remaining shared time.')

@login_required
def session_detail_view(request,session_id):
    s=get_object_or_404(ExamSession,pk=session_id,quiz__teacher=request.user,quiz__class_room__teacher=request.user)
    if request.user.role!='teacher': raise PermissionDenied()
    for aid in s.attempts.filter(status='in_progress').values_list('pk',flat=True): services.reconcile(aid)
    attempts={a.student_id:a for a in s.quiz.attempts.all()}
    return render(request,'session_detail.html',{'session':s,'join_code':invitation(s)['code'],'qr_join_url':qr_url(request,s),'rows':[{'student':e.student,'attempt':attempts.get(e.student_id)} for e in s.quiz.enrollments.select_related('student')]})

@login_required
def session_qr_view(request,session_id):
    s=get_object_or_404(ExamSession,pk=session_id,quiz__teacher=request.user,quiz__class_room__teacher=request.user)
    if request.user.role!='teacher': raise PermissionDenied()
    import qrcode
    from qrcode.image.svg import SvgPathImage
    out=BytesIO();qrcode.make(qr_url(request,s),image_factory=SvgPathImage).save(out)
    return HttpResponse(out.getvalue(),content_type='image/svg+xml')

@login_required
@require_POST
@transaction.atomic
def regenerate_qr_view(request,session_id):
    s=get_object_or_404(ExamSession.objects.select_for_update(),pk=session_id,quiz__teacher=request.user,quiz__class_room__teacher=request.user)
    if request.user.role!='teacher' or s.status!='open' or timezone.now()>=s.ends_at: raise PermissionDenied()
    rotate(s);audit(request.user,'exam.invitation_rotated',session_id=s.pk)
    return redirect('session_detail',session_id=s.pk)

@login_required
@require_POST
def close_session_view(request,session_id):
    try: services.close(session_id,request.user,request.POST.get('reason',''))
    except ValidationError as e: return HttpResponse('; '.join(e.messages),status=400)
    return redirect('session_detail',session_id=session_id)

@require_http_methods(['GET','POST'])
def scan_exam_view(request):
    # QR code opens this URL with ?token=<TOKEN> — validate server-side and go directly to the exam.
    if request.method=='GET':
        token=request.GET.get('token','').strip()
        if token:
            s=ExamSession.objects.filter(token_digest=digest(token),status='open',ends_at__gt=timezone.now()).first()
            if s and network_allowed(request,s):
                request.session['join_session']=s.pk;request.session['join_digest']=s.token_digest
                return redirect('exam_enter' if request.user.is_authenticated else 'login')
            return render(request,'scan_exam.html',{'error':'Invalid, expired, or unauthorized invitation. Please contact your teacher.'})
    if request.method=='POST':
        if limited(request,'join',str(request.user.pk)): return HttpResponse('Too many guesses. Wait five minutes.',status=429)
        value=request.POST.get('code','').strip()[:200]
        query={'join_code':digest(value.upper())} if len(value)==12 else {'token_digest':digest(value)}
        s=ExamSession.objects.filter(**query,status='open',ends_at__gt=timezone.now()).first()
        if not s or not network_allowed(request,s): return HttpResponse('Invalid invitation, closed exam, or wrong network.',status=403)
        request.session['join_session']=s.pk;request.session['join_digest']=s.token_digest
        return redirect('exam_enter' if request.user.is_authenticated else 'login')
    return render(request,'scan_exam.html')

@login_required
@require_GET
def student_session_instructions_view(request,session_id):
    if request.user.role!='student': raise PermissionDenied()
    session=get_object_or_404(
        ExamSession.objects.select_related('quiz','network_profile'),
        pk=session_id,
        status='open',
        starts_at__lte=timezone.now(),
        ends_at__gt=timezone.now(),
        quiz__is_published=True,
    )
    services.allowed(request,session)
    request.session['join_session']=session.pk
    request.session['join_digest']=session.token_digest
    return redirect('exam_instructions')

@login_required
@require_http_methods(['GET','POST'])
def instructions_view(request):
    s=get_object_or_404(ExamSession,pk=request.session.get('join_session'))
    services.allowed(request,s)
    if request.session.get('join_digest')!=s.token_digest: raise PermissionDenied('Invitation was rotated. Enter the new code.')
    if request.method=='POST':
        try: a=services.start(request,s.pk)
        except ValidationError as e: return HttpResponse('; '.join(e.messages),status=409)
        return redirect('exam_view',attempt_id=a.pk)
    return render(request,'instructions.html',{'session':s})

@login_required
def exam_view(request,attempt_id):
    a=get_object_or_404(Attempt,pk=attempt_id,student=request.user)
    services.allowed(request,a.session)
    a=services.reconcile(a.pk)
    if a.status!='in_progress': return redirect('result_view',attempt_id=a.pk)
    answers={x.question_id:x for x in a.answers.all()}
    questions={q.pk:q for q in a.quiz.questions.all()}
    rows=[{'question':questions[qid],'selected':answers[qid].selected_option if qid in answers else '', 'version':answers[qid].version if qid in answers else 0,'key':secrets.token_hex(16)} for qid in a.question_order]
    return render(request,'exam.html',{'attempt':a,'question_rows':rows,'remaining':max(0,int((a.deadline_at-timezone.now()).total_seconds()))})

@login_required
def exam_access_view(request,attempt_id):
    a=get_object_or_404(Attempt,pk=attempt_id,student=request.user);services.allowed(request,a.session)
    a=services.reconcile(a.pk)
    return JsonResponse({'authorized':True,'remaining':max(0,int((a.deadline_at-timezone.now()).total_seconds())),'closed':a.status!='in_progress','result_url':reverse('result_view',args=[a.pk])})

@login_required
@require_POST
def save_answer_view(request,attempt_id):
    get_object_or_404(Attempt,pk=attempt_id,student=request.user)
    try:
        d=request.POST
        data,status=services.save(request,attempt_id,int(d.get('question_id','')),d.get('selected_option',''),int(d.get('version','')),d.get('key',''))
        if status==200 and request.headers.get('X-Requested-With')!='XMLHttpRequest': return redirect('exam_view',attempt_id=attempt_id)
        return JsonResponse(data,status=status)
    except (ValueError,ValidationError): return JsonResponse({'error':'invalid_request'},status=400)

@login_required
@require_POST
def submit_attempt_view(request,attempt_id):
    get_object_or_404(Attempt,pk=attempt_id,student=request.user)
    a=services.submit(request,attempt_id)
    return redirect('result_view',attempt_id=a.pk)

@login_required
def result_view(request,attempt_id):
    a=get_object_or_404(Attempt,pk=attempt_id)
    u=request.user
    if u.role=='student':
        if a.student_id!=u.pk: raise PermissionDenied()
        services.allowed(request,a.session)
    elif u.role=='teacher':
        if a.quiz.teacher_id!=u.pk or a.quiz.class_room.teacher_id!=u.pk: raise PermissionDenied()
    elif u.role=='admin': audit(u,'result.admin_view',attempt_id=a.pk)
    else: raise PermissionDenied()
    a=services.reconcile(a.pk)
    revision=a.revisions.order_by('-pk').first();score=revision.corrected if revision else a.score
    review=a.session.review_released and (a.session.status=='closed' or timezone.now()>=a.session.ends_at)
    return render(request,'result.html',{'attempt':a,'display_score':score,'total_marks':a.total_marks,'percentage':score/a.total_marks*100 if a.total_marks else 0,'review':a.quiz.questions.all() if review and a.status!='in_progress' else None,'revisions':a.revisions.all()})

@login_required
@require_POST
@transaction.atomic
def release_review_view(request,session_id):
    s=get_object_or_404(ExamSession.objects.select_for_update(),pk=session_id,quiz__teacher=request.user,quiz__class_room__teacher=request.user)
    if request.user.role!='teacher' or (s.status!='closed' and timezone.now()<s.ends_at): raise PermissionDenied()
    s.review_released=True;s.save(update_fields=['review_released']);audit(request.user,'exam.review_released',session_id=s.pk)
    return redirect('session_detail',session_id=s.pk)

def csv_cell(value):
    value=str(value)
    return "'"+value if value.lstrip().startswith(('=','+','-','@','\t','\r')) else value

@login_required
def export_results_view(request,session_id):
    s=get_object_or_404(ExamSession,pk=session_id,quiz__teacher=request.user,quiz__class_room__teacher=request.user)
    if request.user.role!='teacher': raise PermissionDenied()
    response=HttpResponse(content_type='text/csv');response['Content-Disposition']='attachment; filename="results.csv"'
    w=csv.writer(response);w.writerow(['Student ID','Username','Status','Score','Total'])
    for e in s.quiz.enrollments.select_related('student'):
        a=Attempt.objects.filter(quiz=s.quiz,student=e.student).first()
        if a: a=services.reconcile(a.pk)
        rev=a.revisions.order_by('-pk').first() if a else None
        w.writerow([csv_cell(e.student.institution_id),csv_cell(e.student.username),a.status if a else 'Not attempted',(rev.corrected if rev else a.score) if a else '',a.total_marks if a else ''])
    audit(request.user,'result.exported',session_id=s.pk)
    return response

class NetworkForm(forms.ModelForm):
    class Meta: model=NetworkProfile;fields=['name','cidrs','interface','enabled']
    def clean_cidrs(self): return normalize_networks(self.cleaned_data['cidrs'])
@login_required
@require_http_methods(['GET','POST'])
def network_profiles_view(request):
    if request.user.role!='admin': raise PermissionDenied()
    existing=get_object_or_404(NetworkProfile,pk=request.GET['edit']) if request.GET.get('edit') else None
    form=NetworkForm(request.POST or None,instance=existing)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            n=form.save();audit(request.user,'network.updated' if existing else 'network.created',profile_id=n.pk)
        return redirect('network_profiles')
    return render(request,'network_profiles.html',{'form':form,'profiles':NetworkProfile.objects.all()})

@login_required
@require_POST
@transaction.atomic
def revise_score_view(request,attempt_id):
    if request.user.role!='admin': raise PermissionDenied()
    s,a=services.locked_attempt(attempt_id)
    try: score=float(request.POST.get('score',''))
    except ValueError: return HttpResponse(status=400)
    reason=request.POST.get('reason','').strip()
    if a.status=='in_progress' or not reason or not 0<=score<=a.total_marks: return HttpResponse(status=400)
    prev=a.revisions.order_by('-pk').first()
    ScoreRevision.objects.create(attempt=a,previous=prev.corrected if prev else a.score,corrected=score,reason=reason,approver=request.user)
    audit(request.user,'score.revised',attempt_id=a.pk,reason=reason)
    return redirect('result_view',attempt_id=a.pk)

@login_required
@require_POST
@transaction.atomic
def replacement_quiz_view(request,quiz_id):
    if request.user.role!='admin': raise PermissionDenied()
    original=get_object_or_404(Quiz,pk=quiz_id,is_published=True)
    reason=request.POST.get('reason','').strip()
    if not reason:return HttpResponse('A replacement reason is required.',status=400)
    q=Quiz.objects.create(teacher=original.class_room.teacher,class_room=original.class_room,title=original.title+' — authorized replacement',description=original.description,replaces=original,version=original.version+1)
    for old in original.questions.all():
        old.pk=None;old.quiz=q;old.save()
    audit(request.user,'quiz.replacement_authorized',original_id=original.pk,replacement_id=q.pk,reason=reason)
    return page(request,'Replacement quiz created',notice='A new draft is available to the assigned teacher. Original attempts and scores are preserved.')

def _is_valid_exam_ip(ip_str):
    if not ip_str:
        return False
    try:
        addr = ipaddress.ip_address(ip_str)
        return addr.version == 4 and not addr.is_loopback and not addr.is_link_local and not addr.is_multicast and not addr.is_unspecified
    except ValueError:
        return False


@login_required
@require_http_methods(['GET','POST'])
def network_diagnostics_view(request, session_id=None):
    """Diagnose network connectivity for exam joining.

    Checks: server running, IP reachable, port listening, firewall, isolation.
    Teacher/admin only.
    """
    if request.user.role not in ('teacher', 'admin'):
        return HttpResponse(status=403)

    session = None
    if session_id:
        sessions = ExamSession.objects.all()
        if request.user.role == 'teacher':
            sessions = sessions.filter(quiz__teacher=request.user, quiz__class_room__teacher=request.user)
        session = get_object_or_404(sessions, pk=session_id)

    from django.conf import settings
    origin = getattr(settings, 'EXAM_PUBLIC_ORIGIN', '')
    mode = getattr(settings, 'EXAM_NETWORK_MODE', 'auto')
    server_ip = getattr(settings, 'EXAM_SERVER_IP', '')

    checks = []
    all_pass = True

    # 1. Origin configured
    if origin and '127.0.0.1' not in origin and 'localhost' not in origin and '0.0.0.0' not in origin:
        checks.append(('QR origin configured', True, origin))
    else:
        checks.append(('QR origin configured', False, origin or '(not set)'))
        all_pass = False

    # 2. Network mode
    checks.append(('Network mode', True, mode))

    # 3. Detect interfaces
    try:
        interfaces = detect_interfaces(mode)
        if interfaces:
            checks.append(('Interfaces detected', True, ', '.join(f"{i['ip']} ({i['type']})" for i in interfaces)))
        else:
            checks.append(('Interfaces detected', False, 'No usable interfaces'))
            all_pass = False
    except Exception as e:
        checks.append(('Interfaces detected', False, str(e)))
        all_pass = False

    # 4. Server IP set
    if server_ip and _is_valid_exam_ip(server_ip):
        checks.append(('Server IP configured', True, server_ip))
    else:
        checks.append(('Server IP configured', False, server_ip or '(not set)'))
        all_pass = False

    # Check the local application listener selected by the active deployment mode.
    backend_port = 8001 if getattr(settings, 'SECURE_PROXY_SSL_HEADER', None) else 8000
    port_open = False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(('127.0.0.1', backend_port))
            port_open = result == 0
    except Exception:
        pass
    checks.append((f'Application listener {backend_port} (localhost)', port_open, 'open' if port_open else 'closed'))
    if not port_open:
        all_pass = False

    # 6. Session-specific check
    if session:
        session_active = session.status == 'open' and timezone.now() < session.ends_at
        checks.append(('Exam session active', session_active, f'Session {session.pk} ({session.status}, {session.network_name})'))
        qr = qr_url(request, session)
        checks.append(('QR URL valid', '127.0.0.1' not in qr and 'localhost' not in qr and '0.0.0.0' not in qr, qr))
        if '127.0.0.1' in qr or 'localhost' in qr or '0.0.0.0' in qr:
            all_pass = False

    return render(request, 'network_diagnostics.html', {
        'origin': origin,
        'mode': mode,
        'server_ip': server_ip,
        'session': session,
        'checks': checks,
        'all_pass': all_pass,
    })


@login_required
def enter_view(request):
    # GET renders a CSRF form; the browser POST starts/resumes without visiting the dashboard.
    s=get_object_or_404(ExamSession,pk=request.session.get('join_session'))
    services.allowed(request,s)
    if request.session.get('join_digest')!=s.token_digest: raise PermissionDenied('Invitation changed. Scan the current QR.')
    if request.user.role!='student' or not QuizEnrollment.objects.filter(quiz=s.quiz,student=request.user).exists():
        raise PermissionDenied('This student is not enrolled in the exam.')
    if request.method=='POST':
        try: a=services.start(request,s.pk)
        except ValidationError as e: return HttpResponse('; '.join(e.messages),status=409)
        return redirect('exam_view',attempt_id=a.pk)
    return render(request,'exam_enter.html',{'session':s})
