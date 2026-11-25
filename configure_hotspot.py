"""Called by the Windows hotspot launcher after validating the selected adapter."""
import argparse,ipaddress,os
from pathlib import Path
from dotenv import set_key
ROOT=Path(__file__).resolve().parent

def write_config(ip,prefix):
    interface=ipaddress.ip_interface(f'{ip}/{prefix}')
    if interface.version!=4 or not interface.ip.is_private or interface.ip.is_loopback or interface.ip.is_link_local or interface.network.prefixlen<16:
        raise ValueError('Select a private IPv4 hotspot interface with prefix /16 or narrower.')
    cidr=str(interface.network);origin=f'https://{interface.ip}:8443'
    values={'DJANGO_DEBUG':'0','BASE_URL':origin,'EXAM_PUBLIC_ORIGIN':origin,'EXAM_SERVER_IP':str(interface.ip),'DJANGO_ALLOWED_HOSTS':str(interface.ip),'TRUST_PROXY_HTTPS':'1','EXAM_TRUSTED_PROXY_CIDRS':'127.0.0.1/32','EXAM_HOTSPOT_CIDR':cidr,'EXAM_HOTSPOT_IP':str(interface.ip)}
    for k,v in values.items():set_key(str(ROOT/'.env'),k,v);os.environ[k]=v
    static_path=(ROOT/'staticfiles').as_posix()
    config='''{
    admin off
    auto_https disable_redirects
    servers {
        protocols h1 h2
    }
}
https://__HOTSPOT_IP__:8443 {
    bind __HOTSPOT_IP__
    tls internal
    @outside not remote_ip __HOTSPOT_CIDR__
    respond @outside "Connect to the teacher laptop hotspot." 403
    handle_path /static/* {
        root * "__STATIC_PATH__"
        file_server
    }
    handle {
        reverse_proxy 127.0.0.1:8001 {
            header_up -Forwarded
            header_up -X-Forwarded-Host
            header_up -X-Forwarded-Port
            header_up -X-Forwarded-By
            header_up -X-Real-IP
            header_up X-Forwarded-For {http.request.remote.host}
            header_up X-Forwarded-Proto https
        }
    }
}
'''.replace('__HOTSPOT_CIDR__',cidr).replace('__HOTSPOT_IP__',str(interface.ip)).replace('__STATIC_PATH__',static_path)
    (ROOT/'Caddyfile.hotspot').write_text(config,encoding='utf-8')
    return cidr,origin

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('ip');p.add_argument('prefix',type=int);p.add_argument('--profile-only',action='store_true');a=p.parse_args()
    cidr,origin=write_config(a.ip,a.prefix)
    if a.profile_only:
        os.environ.setdefault('DJANGO_SETTINGS_MODULE','secure_exam_system.settings')
        import django;django.setup()
        from django.db import transaction
        from exams.models import NetworkProfile,ExamSession
        from accounts.security import audit
        with transaction.atomic():
            n,created=NetworkProfile.objects.get_or_create(name='Teacher laptop hotspot',defaults={'cidrs':cidr,'interface':a.ip})
            if n.cidrs!=cidr and ExamSession.objects.filter(network_profile=n,status='open').exists():
                raise SystemExit('Close previous hotspot exam sessions before changing the subnet.')
            n.cidrs=cidr;n.interface=a.ip;n.enabled=True;n.save()
            audit(None,'network.hotspot_configured',profile_id=n.pk,cidr=cidr)
    print('Hotspot URL:',origin)
