"""Private activation link helpers retained for legacy pending accounts."""
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def trusted_base_url():
    """Validate the configured link origin; never derive it from request headers."""
    value = str(settings.BASE_URL).rstrip('/')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username or parsed.password):
        raise ImproperlyConfigured('BASE_URL must be a configured absolute HTTP(S) origin.')
    if not settings.DEBUG:
        if parsed.scheme != 'https':
            raise ImproperlyConfigured('Activation links require an HTTPS BASE_URL outside debug mode.')
        if parsed.hostname.lower() in ('localhost', '127.0.0.1', '::1'):
            raise ImproperlyConfigured(
                'Production activation links cannot use a loopback BASE_URL.'
            )
    return value


def invitation_url(purpose, token):
    if purpose != 'activate':
        raise ValueError('Unsupported invitation purpose.')
    return trusted_base_url() + '/accounts/activate/?' + urlencode({'token': token})
