"""Every controller API route must reject unauthenticated reads and mutations."""
import re
import unittest
from unittest.mock import patch

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tests.api.support import api


class AuthenticationBoundaryTests(unittest.TestCase):
    def test_all_private_api_methods_require_auth_before_execution(self):
        client = TestClient(api.app)
        self.assertFalse(api.app.dependency_overrides)
        checked = 0
        with patch.object(api.subprocess, 'run', side_effect=AssertionError('Unauthenticated command')):
            for route in api.app.routes:
                if not isinstance(route, APIRoute) or not route.path.startswith('/api/') or route.path in {'/api/health', '/api/auth/status'}:
                    continue
                path = re.sub(r'\{[^}]+\}', 'audit-fixture', route.path)
                for method in route.methods & {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}:
                    with self.subTest(method=method, path=route.path):
                        response = client.request(method, path, json={})
                        self.assertEqual(response.status_code, 401, response.text)
                        self.assertEqual(response.headers.get('www-authenticate'), 'Basic')
                        checked += 1
        self.assertGreater(checked, 0)
