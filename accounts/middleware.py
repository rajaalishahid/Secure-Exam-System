import time
import uuid
from .context import request_context
from django.contrib.auth import logout
from django.conf import settings
from django.http import HttpResponse
from exams.network import client_address, parse_networks, in_networks
from .authentication import auth_session_is_valid


class SecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Global hotspot gate: block all non-hotspot IPs when EXAM_HOTSPOT_CIDR is set
        if settings.EXAM_HOTSPOT_CIDR:
            try:
                approved = in_networks(client_address(request), parse_networks(settings.EXAM_HOTSPOT_CIDR))
            except Exception:
                approved = False
            if not approved:
                response = HttpResponse('Access blocked. Connect to the teacher laptop hotspot.', status=403)
                response['Cache-Control'] = 'no-store'
                return response

        u = request.user
        if u.is_authenticated:
            authenticated_at = request.session.get('authenticated_at', 0)
            valid = (u.is_active and u.status == 'active'
                     and request.session.get('authenticated_security_version') == u.security_version
                     and request.session.get('authenticated_role') == u.role
                     and isinstance(authenticated_at, (int, float))
                     and 0 <= time.time() - authenticated_at < 28800
                     and auth_session_is_valid(request, u)
                     and (u.role != 'student' or u.active_session == request.session.session_key))
            if not valid:
                logout(request)
        elif request.session.get('authenticated_user_id') is not None:
            # Django may resolve an inactive account to AnonymousUser before this
            # middleware runs; discard its remaining server-side auth state too.
            logout(request)

        address = client_address(request)
        token = request_context.set({'source_ip': str(address) if address else None, 'request_id': str(uuid.uuid4())})
        try:
            response = self.get_response(request)
            if response.status_code in (403, 429):
                from .security import audit
                try:
                    audit(request.user if request.user.is_authenticated else None, 'request.denied', path=request.path[:200], status=response.status_code)
                except Exception:
                    import logging
                    logging.getLogger('security').error('Denied request audit unavailable; status=%s', response.status_code)
        finally:
            request_context.reset(token)
        response['Cache-Control'] = 'no-store'
        response['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response
