"""Private WSGI listener. Terminate HTTPS with the configured reverse proxy."""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','secure_exam_system.settings')
from django.core.wsgi import get_wsgi_application
from waitress import serve
from django.conf import settings
if settings.DEBUG: raise SystemExit('Production requires DJANGO_DEBUG=0.')
if getattr(settings, 'SECURE_PROXY_SSL_HEADER', None) != ('HTTP_X_FORWARDED_PROTO', 'https'):
    raise SystemExit('Production HTTPS requires the verified loopback proxy configuration.')
serve(
    get_wsgi_application(),
    host='127.0.0.1',
    port=8001,
    threads=16,
    max_request_body_size=1048576,
    channel_timeout=30,
    trusted_proxy='127.0.0.1',
    trusted_proxy_count=1,
    trusted_proxy_headers={'x-forwarded-for', 'x-forwarded-proto'},
    clear_untrusted_proxy_headers=True,
    log_untrusted_proxy_headers=True,
)
