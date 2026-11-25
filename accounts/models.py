from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
import uuid


class User(AbstractUser):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('teacher', 'Teacher'),
        ('student', 'Student'),
    ]
    STATUS_CHOICES = [
        ('pending_activation', 'Pending Activation'),
        ('pending_mfa', 'Pending MFA'),
        ('active', 'Active'),
        ('suspended', 'Suspended'),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    setup_expires_at = models.DateTimeField(null=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='student')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='pending_activation')
    institution_id = models.CharField(max_length=50, unique=True, null=True, blank=True)
    verified_email = models.EmailField(blank=True, default='')
    security_version = models.IntegerField(default=1)

    def __str__(self):
        return self.get_full_name() or self.username

    active_session = models.CharField(max_length=40, blank=True)

class AccountToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    digest = models.CharField(max_length=64, unique=True)
    purpose = models.CharField(max_length=20)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    delivery_method = models.CharField(max_length=20, default='system')
    issuer = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='issued_tokens')


class RecoveryCode(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='recovery_codes')
    digest = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

class RateBucket(models.Model):
    key = models.CharField(max_length=64, unique=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()


class AccountMFA(models.Model):
    """Account-bound TOTP state; the secret is always Fernet ciphertext."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='mfa')
    encrypted_secret = models.CharField(max_length=512, blank=True, default='')
    is_confirmed = models.BooleanField(default=False)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_accepted_step = models.BigIntegerField(default=-1)
    setup_created_at = models.DateTimeField(null=True, blank=True)
    setup_expires_at = models.DateTimeField(null=True, blank=True)
    setup_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(is_confirmed=True, confirmed_at__isnull=False)
                    | models.Q(is_confirmed=False, confirmed_at__isnull=True)
                ),
                name='account_mfa_confirmation_consistent',
            ),
            models.CheckConstraint(
                condition=models.Q(last_accepted_step__gte=-1),
                name='account_mfa_step_nonnegative_or_unset',
            ),
        ]
