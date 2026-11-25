from django.core.management.base import BaseCommand
from django.db import transaction
from accounts.lifecycle import invitation_url
from accounts.security import issue_token
from accounts.models import User

class Command(BaseCommand):
    help='Create a local teacher account after prompting for a password.'

    @transaction.atomic
    def handle(self, *a, **o):
        if User.objects.filter(username='teacher1').exists():
            self.stdout.write('teacher1 already exists.')
            return
        u=User(username='teacher1',role='teacher',status='pending_activation',
               verified_email='teacher1@example.test',email='teacher1@example.test')
        u.set_unusable_password()
        u.save()
        raw=issue_token(u,'activate',delivery_method='private')
        self.stdout.write('Created pending teacher1. Private activation link (shown once):')
        self.stdout.write(invitation_url('activate',raw))
