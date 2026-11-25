from django.test import Client, TestCase
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone

from accounts.models import AccountMFA, RecoveryCode
from tests.test_system import Fixture, PASSWORD


class HistoricalMFACompatibilityTests(Fixture, TestCase):
    def setUp(self):
        self.setup_data()

    def test_existing_mfa_history_does_not_block_password_login_or_get_deleted(self):
        confirmed_at = timezone.now()
        mfa = AccountMFA.objects.create(
            user=self.student,
            encrypted_secret='historical-ciphertext',
            is_confirmed=True,
            confirmed_at=confirmed_at,
            last_accepted_step=123,
        )
        recovery = RecoveryCode.objects.create(
            user=self.student,
            digest='a' * 64,
        )

        client = Client()
        response = client.post(reverse('login'), {
            'username': self.student.username,
            'password': PASSWORD,
        })

        self.assertRedirects(response, reverse('dashboard'))
        mfa.refresh_from_db()
        recovery.refresh_from_db()
        self.assertEqual(mfa.encrypted_secret, 'historical-ciphertext')
        self.assertEqual(mfa.last_accepted_step, 123)
        self.assertEqual(recovery.digest, 'a' * 64)

    def test_legacy_pending_mfa_account_is_usable_with_its_password(self):
        self.student.status = 'pending_mfa'
        self.student.save(update_fields=['status'])
        client = Client()
        response = client.post(reverse('login'), {
            'username': self.student.username,
            'password': PASSWORD,
        })
        self.assertRedirects(response, reverse('dashboard'))
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, 'active')
        self.assertIn('_auth_user_id', client.session)

    def test_mfa_and_otp_screens_are_not_routable(self):
        for path in ('/accounts/otp/', '/accounts/mfa/setup/', '/accounts/mfa/enroll/'):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)
