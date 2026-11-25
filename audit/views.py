import csv
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from accounts.security import audit
from .models import AuditEvent
@login_required
def audit_log_view(request):
    if request.user.role!='admin': raise PermissionDenied()
    events=AuditEvent.objects.select_related('actor').order_by('-pk')
    query=request.GET.get('q','')[:100]
    if query: events=events.filter(action__icontains=query)
    page=Paginator(events,100).get_page(request.GET.get('page',1))
    if request.GET.get('export')=='1':
        from exams.views import csv_cell
        r=HttpResponse(content_type='text/csv');r['Content-Disposition']='attachment; filename="audit-page.csv"'
        w=csv.writer(r);w.writerow(['UTC','Actor','Action','Details','Source IP'])
        for e in page: w.writerow([e.created_at.isoformat(),e.actor_id,csv_cell(e.action),csv_cell(e.details),e.source_ip])
        audit(request.user,'audit.exported',page=page.number);return r
    return render(request,'audit_log.html',{'events':page,'page':page,'query':query})
