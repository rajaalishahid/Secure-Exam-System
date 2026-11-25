"""Detect active network interfaces for Secure Exam System dual-network support.

Modes:
  - hotspot: Windows Mobile Hotspot adapter (Wi-Fi Direct)
  - hostel: active Wi-Fi/LAN adapter (hostel or shared LAN)
  - auto: hotspot preferred, fall back to Wi-Fi/LAN

Never returns localhost, 127.0.0.1, 0.0.0.0, link-local, multicast, or VPN adapters.
"""
import ipaddress
import socket
import subprocess
import sys


def _run(cmd):
    """Run a command and return stdout, or None on failure."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return result.stdout
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass
    return None


def _is_excluded(address):
    """Return True if an IPv4 address should never be used as exam origin."""
    if not address or address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
        return True
    if address.is_private:
        return False  # Private addresses are valid exam origins
    return True  # Public addresses are not used for local exam hotspot/LAN


def _detect_windows_adapters():
    """Use netsh to enumerate Wi-Fi and Wi-Fi Direct adapters with their IPv4 addresses."""
    adapters = []
    output = _run(['netsh', 'wlan', 'show', 'interfaces'])
    if output:
        current = {}
        for line in output.splitlines():
            line = line.strip()
            if line.startswith('Name') and ':' in line:
                if current.get('ip'):
                    adapters.append(current)
                current = {'name': line.split(':', 1)[1].strip(), 'type': 'wifi', 'ip': None}
            elif line.startswith('State') and ':' in line:
                current['state'] = line.split(':', 1)[1].strip()
            elif line.startswith('SSID') and ':' in line:
                current['ssid'] = line.split(':', 1)[1].strip()
        if current.get('ip'):
            adapters.append(current)

    # IPv4 config per interface name
    output = _run(['netsh', 'interface', 'ipv4', 'show', 'addresses'])
    if output:
        current_iface = None
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith('Interface ') and ':' in stripped:
                current_iface = stripped.split(':', 1)[1].strip()
            elif 'IP Address' in stripped and ':' in stripped and current_iface:
                ip_str = stripped.split(':', 1)[1].strip()
                try:
                    addr = ipaddress.ip_address(ip_str)
                    if addr.version == 4 and not _is_excluded(addr):
                        for a in adapters:
                            if a['name'] == current_iface and not a.get('ip'):
                                a['ip'] = str(addr)
                except ValueError:
                    pass
    return adapters


def _detect_windows_hotspot():
    """Detect Windows Mobile Hotspot (Wi-Fi Direct) adapter IPv4 address."""
    output = _run(['netsh', 'wlan', 'show', 'hostednetwork'])
    if not output:
        return None
    # Check if hosted network is started
    if 'Started' not in output and 'started' not in output.lower():
        return None
    # Try to find the hotspot adapter IP via Wi-Fi Direct
    adapters = _detect_windows_adapters()
    for a in adapters:
        if a.get('type') == 'wifi' and a.get('ip'):
            return a['ip']
    return None


def _detect_via_socket():
    """Fallback: detect active non-loopback IPv4 via UDP socket to public DNS."""
    ip = ''
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
    except OSError:
        pass
    return ip


def _is_wifi_direct_adapter(name):
    """Heuristic: Wi-Fi Direct / hosted network adapters often contain 'Microsoft Wi-Fi Direct Virtual Adapter'."""
    name_lower = (name or '').lower()
    return 'wi-fi direct' in name_lower or 'hosted network' in name_lower or 'virtual' in name_lower


def detect_interfaces(mode='auto'):
    """Detect usable IPv4 interfaces for the selected mode.

    Returns a list of dicts: {ip, type, name, ssid, state, recommended}
    type is 'hotspot', 'wifi', or 'lan'.
    """
    results = []
    seen = set()
    adapters = _detect_windows_adapters()

    for a in adapters:
        ip = a.get('ip')
        if not ip or ip in seen:
            continue
        seen.add(ip)
        addr = ipaddress.ip_address(ip)
        iface_type = 'hotspot' if _is_wifi_direct_adapter(a.get('name')) else ('wifi' if a.get('type') == 'wifi' else 'lan')
        results.append({
            'ip': ip,
            'type': iface_type,
            'name': a.get('name', ''),
            'ssid': a.get('ssid', ''),
            'state': a.get('state', ''),
            'recommended': False,
        })

    # Fallback via socket if netsh found nothing
    if not results:
        fb = _detect_via_socket()
        if fb:
            results.append({'ip': fb, 'type': 'lan', 'name': 'detected-via-socket', 'ssid': '', 'state': 'connected', 'recommended': True})

    # Filter by mode
    if mode == 'hotspot':
        results = [r for r in results if r['type'] == 'hotspot']
    elif mode == 'hostel':
        results = [r for r in results if r['type'] in ('wifi', 'lan')]

    # Mark recommended: hotspot first, then first wifi/lan
    if results:
        for r in results:
            if r['type'] == 'hotspot':
                r['recommended'] = True
                break
        if not any(r['recommended'] for r in results):
            results[0]['recommended'] = True

    return results


def select_ip(mode='auto', manual_ip=None):
    """Select the best IPv4 address for the given mode.

    If manual_ip is provided and valid, return it directly.
    Otherwise auto-detect.
    Returns (ip, mode_used, interfaces) or raises ValueError.
    """
    if manual_ip:
        try:
            addr = ipaddress.ip_address(manual_ip)
            if addr.version != 4 or _is_excluded(addr):
                raise ValueError(f'Invalid or unsuitable IP address: {manual_ip}')
            return str(addr), 'manual', [{'ip': str(addr), 'type': 'manual', 'name': 'user-specified', 'ssid': '', 'state': 'manual', 'recommended': True}]
        except ValueError:
            raise

    interfaces = detect_interfaces(mode)
    if not interfaces:
        hint = {
            'hotspot': 'No Windows Mobile Hotspot adapter found. Enable the hotspot in Settings > Network & Internet > Mobile hotspot.',
            'hostel': 'No active Wi-Fi or LAN adapter found. Connect to the hostel Wi-Fi network.',
            'auto': 'No usable network adapter detected. Connect to a Wi-Fi network or enable the laptop hotspot.',
        }.get(mode, 'No usable network adapter detected.')
        raise ValueError(hint)

    selected = interfaces[0]
    # Prefer hotspot in auto mode; otherwise the recommended one
    actual_mode = selected['type'] if mode == 'auto' else mode
    return selected['ip'], actual_mode, interfaces


if __name__ == '__main__':
    import json
    mode = sys.argv[1] if len(sys.argv) > 1 else 'auto'
    try:
        ip, used_mode, ifaces = select_ip(mode)
        print(json.dumps({'ip': ip, 'mode': used_mode, 'interfaces': ifaces}, indent=2))
    except ValueError as e:
        print(json.dumps({'error': str(e)}), file=sys.stderr)
        sys.exit(1)
