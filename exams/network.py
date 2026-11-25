"""Fail-closed exam network checks; never trust client-supplied IP headers by default."""
import ipaddress
import re
import socket

from django.conf import settings
from django.core.exceptions import ValidationError


def parse_networks(value):
    """Accept explicit IPs/CIDRs, normalize and reject unrestricted/invalid policies."""
    tokens = re.split(r'[\s,]+', value.strip()) if isinstance(value, str) else []
    if not tokens or len(tokens) > 32:
        raise ValidationError('Enter between 1 and 32 authorized IP addresses or CIDR ranges.')
    networks = []
    for token in tokens:
        try:
            if '%' in token:
                raise ValueError('Scoped addresses are not supported.')
            network = ipaddress.ip_network(token, strict=True)
        except ValueError as error:
            raise ValidationError(f'Invalid IP address or network: {token}. Use a network boundary for CIDR ranges.') from error
        if network.prefixlen == 0 or network.network_address.is_multicast or network.network_address.is_unspecified:
            raise ValidationError('Unrestricted, unspecified, and multicast networks cannot be authorized.')
        if network not in networks:
            networks.append(network)
    return networks


def normalize_networks(value):
    return '\n'.join(str(network) for network in parse_networks(value))


def parse_address(value):
    try:
        if not value or '%' in value:
            return None
        address = ipaddress.ip_address(value.strip())
        if address.is_unspecified or address.is_multicast:
            return None
        return getattr(address, 'ipv4_mapped', None) or address
    except (ValueError, AttributeError, TypeError):
        return None


def in_networks(address, networks):
    return address is not None and any(address.version == network.version and address in network for network in networks)


def local_network_addresses():
    """Resolve this server's own interface addresses without trusting HTTP input."""
    try:
        records = socket.getaddrinfo(socket.gethostname(), None, type=socket.SOCK_STREAM)
    except OSError:
        return []
    addresses = [parse_address(record[4][0]) for record in records]
    return [address for address in addresses if address and not address.is_loopback]


def trusted_proxy_peer(request):
    configured = getattr(settings, 'EXAM_TRUSTED_PROXY_CIDRS', [])
    if not configured:
        return False
    try:
        networks = parse_networks('\n'.join(configured))
    except (ValidationError, TypeError):
        return False
    return in_networks(parse_address(request.META.get('REMOTE_ADDR')), networks)


def client_address(request):
    """Walk XFF from the trusted server side, stopping at the first untrusted hop.

    The edge proxy MUST overwrite or append the actual connecting address. Do not
    configure arbitrary client networks as trusted proxies or expose the origin.
    """
    peer = parse_address(request.META.get('REMOTE_ADDR'))
    if peer is None:
        return None
    configured = getattr(settings, 'EXAM_TRUSTED_PROXY_CIDRS', [])
    try:
        proxies = parse_networks('\n'.join(configured)) if configured else []
    except (ValidationError, TypeError):
        return None
    if not in_networks(peer, proxies):
        return peer  # Ignore XFF, X-Real-IP, Forwarded, and similar client headers.
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    parts = forwarded.split(',') if forwarded else []
    if not parts or len(parts) > 32:
        return None
    chain = [parse_address(part) for part in parts]
    if any(address is None for address in chain):
        return None
    for address in reversed(chain):
        if not in_networks(address, proxies):
            return address
    return None  # A chain containing only proxies does not identify a student.


def network_allowed(request, session):
    try:
        networks = parse_networks(session.authorized_networks)
    except ValidationError:
        return False  # Legacy/unconfigured sessions never become unrestricted.
    return in_networks(client_address(request), networks)
