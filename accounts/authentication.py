"""Server-side authentication state layered on Django's session authentication."""
import math
import time

from django.conf import settings
from django.utils.crypto import constant_time_compare, salted_hmac

from .models import User

AUTH_SESSION_FIELDS = (
    'authenticated_user_id',
    'authenticated_at',
    'authenticated_session_expires_at',
    'authenticated_security_version',
    'authenticated_role',
    'authenticated_password_fingerprint',
    'authenticated_backend',
)
FULL_SESSION_TTL_SECONDS = 8 * 60 * 60


def _password_fingerprint(user):
    return salted_hmac('accounts.auth.password', user.password).hexdigest()


def clear_auth_session(session):
    for field in AUTH_SESSION_FIELDS:
        session.pop(field, None)


def begin_auth_session(request, user, backend):
    """Rotate the anonymous session and bind it to the current account state."""
    request.session.cycle_key()
    clear_auth_session(request.session)
    authenticated_at = time.time()
    request.session.update({
        'authenticated_user_id': user.pk,
        'authenticated_at': authenticated_at,
        'authenticated_session_expires_at': authenticated_at + FULL_SESSION_TTL_SECONDS,
        'authenticated_security_version': user.security_version,
        'authenticated_role': user.role,
        'authenticated_password_fingerprint': _password_fingerprint(user),
        'authenticated_backend': backend,
    })


def get_authenticated_user(request, *, lock=False, now=None):
    """Return (user, reason) after validating the server-side authentication state."""
    session = request.session
    timestamp = time.time() if now is None else now
    try:
        user_id = int(session['authenticated_user_id'])
        authenticated_at = float(session['authenticated_at'])
        expires_at = float(session['authenticated_session_expires_at'])
        version = int(session['authenticated_security_version'])
        role = session['authenticated_role']
        password_fingerprint = session['authenticated_password_fingerprint']
        backend = session['authenticated_backend']
    except (KeyError, TypeError, ValueError, OverflowError):
        clear_auth_session(session)
        return None, 'missing'

    configured_backends = getattr(settings, 'AUTHENTICATION_BACKENDS', [
        'django.contrib.auth.backends.ModelBackend',
    ])
    if (not math.isfinite(authenticated_at) or not math.isfinite(expires_at)
            or not isinstance(role, str)
            or not isinstance(password_fingerprint, str)
            or not isinstance(backend, str) or backend not in configured_backends
            or authenticated_at > timestamp or timestamp >= expires_at
            or expires_at - authenticated_at != FULL_SESSION_TTL_SECONDS):
        clear_auth_session(session)
        return None, 'expired'

    query = User.objects.select_for_update() if lock else User.objects
    user = query.filter(pk=user_id).first()
    valid = bool(
        user and user.is_active and user.status == 'active'
        and user.security_version == version and user.role == role
        and constant_time_compare(_password_fingerprint(user), password_fingerprint)
    )
    if not valid:
        clear_auth_session(session)
        return None, 'changed'
    return user, 'ok'


def auth_session_is_valid(request, user, *, now=None):
    """Check that Django's authenticated user still matches our server-side state."""
    authenticated, reason = get_authenticated_user(request, now=now)
    return bool(reason == 'ok' and authenticated.pk == user.pk)
