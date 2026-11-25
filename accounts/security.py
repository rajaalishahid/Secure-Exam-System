import hashlib, secrets, time
from cryptography.fernet import Fernet
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from audit.models import AuditEvent
from .models import RateBucket, AccountToken

def encrypt(value): return Fernet(settings.EXAM_ENCRYPTION_KEY.encode()).encrypt(value.encode()).decode()
def decrypt(value): return Fernet(settings.EXAM_ENCRYPTION_KEY.encode()).decrypt(value.encode()).decode()
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def audit(actor, action, **details):
    import json
    from .context import request_context
    context=request_context.get()
    details.update({'request_id':context.get('request_id'),'actor_role':actor.role if actor else 'system'})
    return AuditEvent.objects.create(actor=actor, action=action, details=json.dumps(details),source_ip=context.get('source_ip'))

def limited(request, action, account=''):
    # Shared database counters work across WSGI processes; bounded five-minute buckets.
    peer=request.META.get('REMOTE_ADDR','unknown')
    now=timezone.now(); denied=False
    for scope,limit in [(peer,60),(peer+'|'+account,8),(account,30)]:
        key=digest(action+'|'+scope+'|'+str(int(time.time())//300))
        with transaction.atomic():
            row,_=RateBucket.objects.get_or_create(key=key,defaults={'expires_at':now+timezone.timedelta(minutes=6)})
            row=RateBucket.objects.select_for_update().get(pk=row.pk)
            row.count+=1;row.save(update_fields=['count'])
            denied |= row.count>limit
    return denied

@transaction.atomic
def issue_token(user,purpose,issuer=None,delivery_method=None):
    from .models import User
    user=User.objects.select_for_update().get(pk=user.pk)
    now=timezone.now()
    AccountToken.objects.filter(user=user,purpose=purpose,used_at__isnull=True,revoked_at__isnull=True).update(revoked_at=now)
    raw=secrets.token_urlsafe(32)
    delivery_method=delivery_method or ('private' if purpose == 'activate' else 'system')
    AccountToken.objects.create(
        user=user,digest=digest(raw),purpose=purpose,
        expires_at=now+timezone.timedelta(minutes=30),issuer=issuer,
        delivery_method=delivery_method,
    )
    audit(issuer,'account.invitation_issued' if purpose == 'activate' else 'account.reset_issued',
          user_id=user.pk,purpose=purpose,delivery_method=delivery_method)
    return raw
