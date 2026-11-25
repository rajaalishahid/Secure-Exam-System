"""Create a local-only development environment without overwriting existing keys."""
from pathlib import Path
import secrets
from cryptography.fernet import Fernet
p=Path(__file__).parent/'.env'
if p.exists():
    print('.env already exists; kept existing configuration and keys.')
else:
    p.write_text('DJANGO_DEBUG=1\nDJANGO_SECRET_KEY='+secrets.token_urlsafe(48)+'\nEXAM_ENCRYPTION_KEY='+Fernet.generate_key().decode()+'\nBASE_URL=http://127.0.0.1:8000\nDJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,[::1]\n',encoding='utf-8')
    try: p.chmod(0o600)
    except OSError: pass
    print('Local development configuration created. See README before LAN deployment.')
