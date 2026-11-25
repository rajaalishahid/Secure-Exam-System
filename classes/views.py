import csv,io,secrets
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction,IntegrityError
from django.shortcuts import get_object_or_404,render,redirect
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods
from accounts.models import User
from accounts.views import page
from accounts.security import audit
from .models import ClassRoom,ClassMembership
from .forms import CreateClassForm
class AssignForm(forms.ModelForm):
    class Meta: model=ClassRoom;fields=['name','teacher']
    def __init__(self,*a,**k):
        super().__init__(*a,**k);self.fields['teacher'].queryset=User.objects.filter(role='teacher',is_active=True)
@login_required
@require_http_methods(['GET','POST'])
def create_class_view(request):
    if request.user.role!='admin': raise PermissionDenied('Only admins assign classes.')
    form=AssignForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic(): c=form.save();audit(request.user,'class.assigned',class_id=c.pk,teacher_id=c.teacher_id)
        return redirect('dashboard')
    return page(request,'Create and assign class',form)
@login_required
@require_http_methods(['GET','POST'])
def create_teacher_class_view(request):
    if request.user.role!='teacher': raise PermissionDenied('Only teachers can create their own classes.')
    form=CreateClassForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic(): c=form.save(commit=False);c.teacher=request.user;c.save();audit(request.user,'class.created',class_id=c.pk)
        return redirect('class_detail',pk=c.pk)
    return page(request,'Create your class',form)

@login_required
def class_detail_view(request,pk):
    c=get_object_or_404(ClassRoom,pk=pk)
    if request.user.role=='teacher' and c.teacher_id!=request.user.pk: raise PermissionDenied()
    if request.user.role=='student': raise PermissionDenied()
    return render(request,'class_detail.html',{'class_room':c})
class ImportForm(forms.Form): file=forms.FileField(label='CSV (student_id,full_name)')
@login_required
@require_http_methods(['GET','POST'])
def import_students_view(request,pk):
    c=get_object_or_404(ClassRoom,pk=pk,teacher=request.user)
    if request.user.role!='teacher': raise PermissionDenied()
    form=ImportForm(request.POST or None,request.FILES or None)
    if request.method=='POST' and form.is_valid():
        upload=form.cleaned_data['file']
        if upload.size>262144: return HttpResponse('Maximum CSV size: 256 KB.',status=400)
        try:
            from accounts.forms import CreateStudentForm
            rows=list(csv.DictReader(io.StringIO(upload.read().decode('utf-8-sig'))))
            if not 1<=len(rows)<=500: raise ValueError('Use 1–500 rows.')
            credentials=[]
            with transaction.atomic():
                for row in rows:
                    row['institution_id']=row.get('student_id') or row.get('institution_id')
                    row['class_id']=c.pk;f=CreateStudentForm(row,user=request.user)
                    if not f.is_valid(): raise ValueError(str(f.errors.as_text()))
                    d=f.cleaned_data;password=secrets.token_urlsafe(18)
                    u=User(username=d['institution_id'],institution_id=d['institution_id'],first_name=d['full_name'],role='student',status='active')
                    u.set_password(password);u.save();ClassMembership.objects.create(class_room=c,student=u)
                    credentials.append({'username':u.username,'password':password})
                audit(request.user,'roster.imported',class_id=c.pk,count=len(rows))
            response=render(request,'import_links.html',{'credentials':credentials})
            response['Cache-Control']='no-store, no-cache, max-age=0, must-revalidate'
            response['Referrer-Policy']='no-referrer'
            return response
        except (ValueError,UnicodeDecodeError,IntegrityError) as e: form.add_error(None,'Import rejected; no rows committed. '+str(e)[:500])
    return page(request,'Import class roster',form,notice='Provide Student ID and full name. Every student is assigned only to this class; temporary passwords are shown once for private delivery.')
