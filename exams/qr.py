import secrets, json, socket
from django.conf import settings
from accounts.security import encrypt,decrypt,digest

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

def _public_origin():
    """Advertised origin for QR codes: env override > detected LAN IP > BASE_URL."""
    origin = getattr(settings, 'EXAM_PUBLIC_ORIGIN', '') or ''
    if origin:
        return origin.rstrip('/')
    lan = _detect_lan_ip()
    if lan:
        return 'http://' + lan + ':8000'
    return settings.BASE_URL.rstrip('/')

def rotate(session):
    token=secrets.token_urlsafe(32)
    code=''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ234567') for _ in range(12))
    session.token_digest=digest(token);session.join_code=digest(code)
    session.invitation_cipher=encrypt(json.dumps({'token':token,'code':code}))
    session.save()

def invitation(session): return json.loads(decrypt(session.invitation_cipher))
def qr_url(request,session):
    # Query parameter is sent to the server; fragments are not.
    # Never use request.build_absolute_uri() (may be localhost); use the advertised origin.
    return _public_origin() + '/exams/scan/?token=' + invitation(session)['token']
