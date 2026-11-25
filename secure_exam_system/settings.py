from pathlib import Path
import os
import socket
import ipaddress
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')
DEBUG = os.getenv('DJANGO_DEBUG', '0') == '1'
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', '')
EXAM_ENCRYPTION_KEY = os.getenv('EXAM_ENCRYPTION_KEY', '')
if not SECRET_KEY or not EXAM_ENCRYPTION_KEY:
    raise ImproperlyConfigured('Run python configure.py to create local keys, or configure environment keys.')
EXAM_NETWORK_MODE = os.getenv('EXAM_NETWORK_MODE', 'auto')
EXAM_SERVER_IP = os.getenv('EXAM_SERVER_IP', '')

def _detect_lan_ip():
    """Detect the active non-loopback IPv4 LAN address (works over Wi-Fi/hotspot)."""
    ip = ''
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
    except Exception:
        pass
    return ip

def _is_valid_exam_ip(ip_str):
    if not ip_str:
        return False
    try:
        addr = ipaddress.ip_address(ip_str)
        return addr.version == 4 and not addr.is_loopback and not addr.is_link_local and not addr.is_multicast and not addr.is_unspecified
    except ValueError:
        return False

# Determine the public origin for QR codes and student access
# Priority: EXAM_PUBLIC_ORIGIN env > EXAM_SERVER_IP env > detected LAN IP > BASE_URL fallback
_ENV_ORIGIN = os.getenv('EXAM_PUBLIC_ORIGIN', '').rstrip('/')
_ORIGIN_FROM_DETECTION = False
if _ENV_ORIGIN:
    EXAM_PUBLIC_ORIGIN = _ENV_ORIGIN
elif _is_valid_exam_ip(EXAM_SERVER_IP):
    EXAM_PUBLIC_ORIGIN = 'http://' + EXAM_SERVER_IP + ':8000'
else:
    _LAN_IP = _detect_lan_ip()
    if _LAN_IP:
        EXAM_PUBLIC_ORIGIN = 'http://' + _LAN_IP + ':8000'
        _ORIGIN_FROM_DETECTION = True
    else:
        EXAM_PUBLIC_ORIGIN = os.getenv('BASE_URL', 'http://127.0.0.1:8000').rstrip('/')

# BASE_URL should match EXAM_PUBLIC_ORIGIN for consistency in email/activation links.
# When origin came from detection (not env override), use it for BASE_URL too.
_BASE_URL = os.getenv('BASE_URL', '').rstrip('/')
if not _BASE_URL or _ORIGIN_FROM_DETECTION:
    _BASE_URL = EXAM_PUBLIC_ORIGIN

_ALLOWED_HOSTS = [x.strip() for x in os.getenv('DJANGO_ALLOWED_HOSTS','localhost,127.0.0.1,[::1]').split(',') if x.strip()]
if EXAM_PUBLIC_ORIGIN:
    # Extract host from origin for ALLOWED_HOSTS
    from urllib.parse import urlparse
    try:
        _host = urlparse(EXAM_PUBLIC_ORIGIN).hostname
        if _host and _host not in _ALLOWED_HOSTS:
            _ALLOWED_HOSTS.append(_host)
    except Exception:
        pass
ALLOWED_HOSTS = _ALLOWED_HOSTS
BASE_URL = _BASE_URL
CSRF_TRUSTED_ORIGINS = [EXAM_PUBLIC_ORIGIN] + ([BASE_URL] if BASE_URL and BASE_URL not in [EXAM_PUBLIC_ORIGIN] else [])
EXAM_TRUSTED_PROXY_CIDRS = [x.strip() for x in os.getenv('EXAM_TRUSTED_PROXY_CIDRS','').split(',') if x.strip()]
INSTALLED_APPS = ['django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','accounts','classes','exams','audit']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','accounts.middleware.SecurityMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF='secure_exam_system.urls'
TEMPLATES=[{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION='secure_exam_system.wsgi.application'
DB_ENGINE = os.getenv('DB_ENGINE', 'sqlite').lower()
DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':BASE_DIR/'db.sqlite3','OPTIONS':{'timeout':20}}}
if DB_ENGINE=='postgresql':
    DATABASES={'default':{'ENGINE':'django.db.backends.postgresql','NAME':os.getenv('DB_NAME','secure_exam'),'USER':os.getenv('DB_USER','exam_app'),'PASSWORD':os.getenv('DB_PASSWORD',''),'HOST':os.getenv('DB_HOST','127.0.0.1'),'PORT':os.getenv('DB_PORT','5432')}}
elif DB_ENGINE=='mysql':
    DATABASES={'default':{'ENGINE':'django.db.backends.mysql','NAME':os.getenv('DB_NAME','secure_exam'),'USER':os.getenv('DB_USER','exam_app'),'PASSWORD':os.getenv('DB_PASSWORD',''),'HOST':os.getenv('DB_HOST','127.0.0.1'),'PORT':os.getenv('DB_PORT','3306'),'OPTIONS':{'charset':'utf8mb4','init_command':"SET sql_mode='STRICT_TRANS_TABLES'"}}}
elif DB_ENGINE!='sqlite':
    raise ImproperlyConfigured('DB_ENGINE must be sqlite, mysql, or postgresql.')
AUTH_USER_MODEL='accounts.User'
AUTH_PASSWORD_VALIDATORS=[{'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator','OPTIONS':{'min_length':12}},{'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},{'NAME':'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'}]
PASSWORD_HASHERS=['django.contrib.auth.hashers.Argon2PasswordHasher','django.contrib.auth.hashers.PBKDF2PasswordHasher']
LANGUAGE_CODE='en-us'
TIME_ZONE='UTC'
USE_TZ=True
DEFAULT_AUTO_FIELD='django.db.models.BigAutoField'
STATIC_URL='/static/'
STATICFILES_DIRS=[BASE_DIR/'static']
STATIC_ROOT=BASE_DIR/'staticfiles'
LOGIN_URL='/login/'
SESSION_COOKIE_HTTPONLY=True
SESSION_COOKIE_SAMESITE='Strict'
SESSION_COOKIE_SECURE=not DEBUG
CSRF_COOKIE_SECURE=not DEBUG
CSRF_COOKIE_HTTPONLY=True
# Authentication state stays in Django's server-side session table.
SESSION_ENGINE='django.contrib.sessions.backends.db'
SESSION_COOKIE_AGE=8 * 60 * 60
SESSION_SAVE_EVERY_REQUEST=True
SECURE_SSL_REDIRECT=not DEBUG
SECURE_HSTS_SECONDS=0 if DEBUG else 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS=not DEBUG
SECURE_HSTS_PRELOAD=not DEBUG
SECURE_REFERRER_POLICY='same-origin'
X_FRAME_OPTIONS='DENY'
SECURE_CONTENT_TYPE_NOSNIFF=True
DATA_UPLOAD_MAX_MEMORY_SIZE=1048576
DATA_UPLOAD_MAX_NUMBER_FIELDS=1500
CSRF_FAILURE_VIEW='accounts.views.csrf_failure'
EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST=os.getenv('EMAIL_HOST','localhost')
EMAIL_PORT=int(os.getenv('EMAIL_PORT','587'))
EMAIL_USE_TLS=True
EMAIL_HOST_USER=os.getenv('EMAIL_HOST_USER','')
EMAIL_HOST_PASSWORD=os.getenv('EMAIL_HOST_PASSWORD','')
DEFAULT_FROM_EMAIL=os.getenv('DEFAULT_FROM_EMAIL','exams@example.edu')
# Hotspot HTTPS has one loopback proxy hop. The origin listener is loopback-only,
# and Waitress accepts the overwritten forwarding headers only from this peer.
if os.getenv('TRUST_PROXY_HTTPS') == '1':
    if EXAM_TRUSTED_PROXY_CIDRS != ['127.0.0.1/32']:
        raise ImproperlyConfigured(
            'HTTPS proxy mode requires EXAM_TRUSTED_PROXY_CIDRS=127.0.0.1/32.'
        )
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

EXAM_HOTSPOT_CIDR=os.getenv('EXAM_HOTSPOT_CIDR','')
EXAM_HOTSPOT_IP=os.getenv('EXAM_HOTSPOT_IP','')
