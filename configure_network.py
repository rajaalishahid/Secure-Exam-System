"""Configure .env and settings for the selected network mode.

Legacy network configuration helper. The supported student-facing deployment uses
Start-Hotspot.cmd; this helper does not start a secure listener.
Updates EXAM_PUBLIC_ORIGIN, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, EXAM_HOTSPOT_CIDR/IP.
"""
import argparse
import ipaddress
import os
from pathlib import Path
from dotenv import set_key

ROOT = Path(__file__).resolve().parent


def configure(ip, mode, cidr, interface):
    if not ip:
        raise ValueError('IP address is required')
    addr = ipaddress.ip_address(ip)
    if addr.version != 4 or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        raise ValueError(f'Invalid exam origin IP: {ip}')
    if mode not in {'hotspot', 'hostel', 'auto'}:
        raise ValueError('Mode must be hotspot, hostel, or auto.')

    origin = f'http://{ip}:8000'

    # Accept the detected prefix (for example, /24) or an explicit network.
    try:
        network = ipaddress.ip_interface(f'{ip}{cidr}').network if cidr.startswith('/') else ipaddress.ip_network(cidr, strict=False)
    except ValueError as error:
        raise ValueError(f'Invalid network prefix or CIDR: {cidr}') from error
    if addr not in network:
        raise ValueError(f'Exam IP {ip} is outside the selected network {network}.')
    subnet = str(network)

    values = {
        'EXAM_PUBLIC_ORIGIN': origin,
        'BASE_URL': origin,
        'DJANGO_ALLOWED_HOSTS': ','.join(('localhost', '127.0.0.1', '[::1]', ip)),
        'CSRF_TRUSTED_ORIGINS': origin,
        'EXAM_NETWORK_MODE': mode,
        'EXAM_SERVER_IP': ip,
        'EXAM_SERVER_INTERFACE': interface or '',
    }
    if mode == 'hotspot':
        values['EXAM_HOTSPOT_CIDR'] = subnet
        values['EXAM_HOTSPOT_IP'] = ip

    env_path = ROOT / '.env'
    for k, v in values.items():
        set_key(str(env_path), k, v)
        os.environ[k] = v

    print(f'Configured: EXAM_PUBLIC_ORIGIN={origin}')
    print(f'Configured: ALLOWED_HOSTS includes {ip}')
    print(f'Configured: Network mode = {mode}')
    print(f'Student access URL: {origin}')
    return origin


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('ip')
    p.add_argument('mode')
    p.add_argument('cidr', nargs='?', default='/24')
    p.add_argument('interface', nargs='?', default='')
    a = p.parse_args()
    configure(a.ip, a.mode, a.cidr, a.interface)
