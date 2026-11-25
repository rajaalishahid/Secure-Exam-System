import re
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from waitress.proxy_headers import proxy_headers_middleware

from accounts.models import User
from exams.network import client_address


HOTSPOT_IP = '192.168.137.1'
HOTSPOT_ORIGIN = f'https://{HOTSPOT_IP}:8443'
STUDENT_IP = '192.168.137.50'
PASSWORD = 'Synthetic-Test-Passphrase-579!'


def translated_environ(remote_addr, **headers):
    captured = {}

    def application(environ, start_response):
        captured.update(environ)
        start_response('200 OK', [('Content-Type', 'text/plain')])
        return [b'ok']

    environ = {
        'REQUEST_METHOD': 'GET',
        'SCRIPT_NAME': '',
        'PATH_INFO': '/',
        'QUERY_STRING': '',
        'SERVER_NAME': '127.0.0.1',
        'SERVER_PORT': '8001',
        'SERVER_PROTOCOL': 'HTTP/1.1',
        'REMOTE_ADDR': remote_addr,
        'wsgi.version': (1, 0),
        'wsgi.url_scheme': 'http',
        'wsgi.input': None,
        'wsgi.errors': None,
        'wsgi.multithread': True,
        'wsgi.multiprocess': False,
        'wsgi.run_once': False,
        **headers,
    }
    middleware = proxy_headers_middleware(
        application,
        trusted_proxy='127.0.0.1',
        trusted_proxy_count=1,
        trusted_proxy_headers={'x-forwarded-for', 'x-forwarded-proto'},
        clear_untrusted=True,
    )
    list(middleware(environ, lambda status, response_headers: None))
    return captured


class WaitressProxyTests(SimpleTestCase):
    def test_verified_loopback_proxy_sets_scheme_and_student_address(self):
        environ = translated_environ(
            '127.0.0.1',
            HTTP_X_FORWARDED_FOR=STUDENT_IP,
            HTTP_X_FORWARDED_PROTO='https',
        )
        self.assertEqual(environ['wsgi.url_scheme'], 'https')
        self.assertEqual(environ['REMOTE_ADDR'], STUDENT_IP)
        request = RequestFactory().get('/', REMOTE_ADDR=environ['REMOTE_ADDR'])
        self.assertEqual(str(client_address(request)), STUDENT_IP)

    def test_untrusted_peer_cannot_forge_scheme_or_student_address(self):
        environ = translated_environ(
            '203.0.113.9',
            HTTP_X_FORWARDED_FOR=STUDENT_IP,
            HTTP_X_FORWARDED_PROTO='https',
        )
        self.assertEqual(environ['wsgi.url_scheme'], 'http')
        self.assertEqual(environ['REMOTE_ADDR'], '203.0.113.9')
        self.assertNotIn('HTTP_X_FORWARDED_FOR', environ)
        self.assertNotIn('HTTP_X_FORWARDED_PROTO', environ)

    def test_unneeded_forwarding_headers_are_cleared(self):
        environ = translated_environ(
            '127.0.0.1',
            HTTP_X_FORWARDED_FOR=STUDENT_IP,
            HTTP_X_FORWARDED_PROTO='https',
            HTTP_FORWARDED='for=203.0.113.9;proto=http',
            HTTP_X_FORWARDED_HOST='attacker.example',
            HTTP_X_FORWARDED_PORT='80',
        )
        self.assertNotIn('HTTP_FORWARDED', environ)
        self.assertNotIn('HTTP_X_FORWARDED_HOST', environ)
        self.assertNotIn('HTTP_X_FORWARDED_PORT', environ)

    def test_caddy_overwrites_the_only_trusted_forwarding_headers(self):
        source = (Path(settings.BASE_DIR) / 'Caddyfile.hotspot').read_text(encoding='utf-8')
        self.assertIn('header_up X-Forwarded-For {http.request.remote.host}', source)
        self.assertIn('header_up X-Forwarded-Proto https', source)
        for header in ('Forwarded', 'X-Forwarded-Host', 'X-Forwarded-Port', 'X-Forwarded-By', 'X-Real-IP'):
            self.assertIn(f'header_up -{header}', source)

    def test_hotspot_generator_preserves_forwarding_header_names(self):
        import configure_hotspot

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(configure_hotspot, 'ROOT', Path(directory)):
                with patch.object(configure_hotspot, 'set_key'):
                    configure_hotspot.write_config(HOTSPOT_IP, 24)
            source = (Path(directory) / 'Caddyfile.hotspot').read_text(encoding='utf-8')
        self.assertIn('header_up -X-Real-IP', source)
        self.assertNotIn('X-Real-192.168.137.1', source)

    def test_waitress_listener_and_trust_are_loopback_only(self):
        source = (Path(settings.BASE_DIR) / 'serve_production.py').read_text(encoding='utf-8')
        self.assertIn("host='127.0.0.1'", source)
        self.assertIn("trusted_proxy='127.0.0.1'", source)
        self.assertIn("{'x-forwarded-for', 'x-forwarded-proto'}", source)
        self.assertIn('clear_untrusted_proxy_headers=True', source)


@override_settings(
    DEBUG=False,
    SECURE_SSL_REDIRECT=True,
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    CSRF_TRUSTED_ORIGINS=[HOTSPOT_ORIGIN],
    EXAM_PUBLIC_ORIGIN=HOTSPOT_ORIGIN,
    EXAM_HOTSPOT_CIDR='192.168.137.0/24',
    EXAM_TRUSTED_PROXY_CIDRS=['127.0.0.1/32'],
)
class DjangoProxyIntegrationTests(TestCase):
    proxy_headers = {
        'REMOTE_ADDR': '127.0.0.1',
        'HTTP_X_FORWARDED_FOR': STUDENT_IP,
        'HTTP_X_FORWARDED_PROTO': 'https',
        'HTTP_HOST': f'{HOTSPOT_IP}:8443',
    }

    def setUp(self):
        self.user = User.objects.create_user(
            username='proxy-student',
            password=PASSWORD,
            role='student',
            status='active',
            institution_id='PROXY001',
        )

    def test_https_login_loads_without_redirect_loop(self):
        response = Client().get('/login/', **self.proxy_headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.wsgi_request.is_secure())

    def test_secure_login_post_accepts_valid_csrf(self):
        client = Client(enforce_csrf_checks=True)
        form = client.get('/login/', **self.proxy_headers)
        token = re.search(
            r'name="csrfmiddlewaretoken" value="([^"]+)"',
            form.content.decode(),
        ).group(1)
        response = client.post(
            '/login/',
            {
                'username': self.user.username,
                'password': PASSWORD,
                'csrfmiddlewaretoken': token,
            },
            HTTP_ORIGIN=HOTSPOT_ORIGIN,
            **self.proxy_headers,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/dashboard/')
        self.assertTrue(client.cookies[settings.SESSION_COOKIE_NAME]['secure'])
        self.assertTrue(client.cookies[settings.CSRF_COOKIE_NAME]['secure'])

    def test_login_post_without_csrf_is_rejected(self):
        response = Client(enforce_csrf_checks=True).post(
            '/login/',
            {'username': self.user.username, 'password': PASSWORD},
            HTTP_ORIGIN=HOTSPOT_ORIGIN,
            **self.proxy_headers,
        )
        self.assertEqual(response.status_code, 403)

    def test_unauthorized_network_and_forged_proxy_peer_are_denied(self):
        unauthorized = dict(self.proxy_headers, HTTP_X_FORWARDED_FOR='192.168.1.50')
        self.assertEqual(Client().get('/login/', **unauthorized).status_code, 403)
        forged = dict(
            self.proxy_headers,
            REMOTE_ADDR='203.0.113.9',
            HTTP_X_FORWARDED_FOR=STUDENT_IP,
        )
        self.assertEqual(Client().get('/login/', **forged).status_code, 403)

    def test_hotspot_qr_origin_is_exact(self):
        from exams.qr import _public_origin

        self.assertEqual(_public_origin(), HOTSPOT_ORIGIN)
