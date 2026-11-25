import time
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q
from exams.models import Attempt,ExamSession
from exams.services import reconcile
from accounts.models import RateBucket,User
from accounts.security import audit
from django.db import transaction
class Command(BaseCommand):
    help='Finalize due attempts every five seconds; --once for scheduled tasks.'
    def add_arguments(self,parser): parser.add_argument('--once',action='store_true')
    def handle(self,*args,**options):
        while True:
            now=timezone.now()
            ids=Attempt.objects.filter(status='in_progress').filter(Q(deadline_at__lte=now)|Q(session__status='closed')).values_list('pk',flat=True)
            for aid in ids.iterator(): reconcile(aid)
            for sid in ExamSession.objects.filter(status='open',ends_at__lte=now).values_list('pk',flat=True):
                with transaction.atomic():
                    s=ExamSession.objects.select_for_update().get(pk=sid)
                    if s.status=='open':
                        s.status='closed';s.closed_at=s.ends_at;s.closure_reason='Scheduled deadline';s.save();audit(None,'exam.deadline_closed',session_id=s.pk)
            RateBucket.objects.filter(expires_at__lt=now).delete()
            if options['once']: break
            time.sleep(5)
