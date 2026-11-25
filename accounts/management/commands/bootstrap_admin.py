from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.lifecycle import invitation_url
from accounts.models import User
from accounts.security import issue_token


class Command(BaseCommand):
    help = 'Create the initial pending administrator and print one private activation link.'

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('email')

    @transaction.atomic
    def handle(self, *args, **options):
        if User.objects.filter(role='admin').exists():
            raise CommandError('An administrator already exists; this command only creates the initial account.')
        if User.objects.filter(username=options['username']).exists():
            raise CommandError('Username already exists.')
        user = User(username=options['username'], email=options['email'],
                    verified_email=options['email'], role='admin', status='pending_activation')
        user.set_unusable_password()
        user.save()
        raw = issue_token(user, 'activate', delivery_method='private')
        self.stdout.write(self.style.WARNING('Initial administrator activation link (shown once):'))
        self.stdout.write(invitation_url('activate', raw))
        self.stdout.write('Deliver this link privately to the authorized administrator. It expires in 30 minutes.')
