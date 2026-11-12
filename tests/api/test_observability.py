import json
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from tests.api.support import api, manager
import assistant_control
import assistant_commands
import assistant_diagnostics
import assistant_mihomo
import observability as obs
import observability_control as control


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(api, 'DATA_DIR', self.root))
        self.store = obs.Store(self.root)

    def event(self, **values):
        return {'at': time.time(), 'request_id': 'a' * 32, 'service': 'mihomo',
                'route': '/api/mihomo/subscriptions/{token}', 'method': 'GET', 'status': 200,
                'duration_ms': 1, 'reason': 'completed', 'profile_id': 'profile-one', 'kind': 'subscription', **values}

    def test_retained_window_counts_success_failure_and_distinct_known_profiles(self):
        for event in [self.event(), self.event(status=403), self.event(status=500),
                      self.event(status=404, profile_id=None), self.event(at=time.time() - 7200)]:
            self.store.append(event)
        result = self.store.read(errors=True)
        self.assertEqual(result['summary']['requests'], 4)
        self.assertEqual(result['summary']['errors'], 3)
        self.assertEqual(result['summary']['affected_profiles'], 1)
        self.assertEqual(result['subscriptions'], {'attempts': 4, 'succeeded': 1, 'failed': 3})
        self.assertEqual(len(result['events']), 3)
        self.assertIsNotNone(result['summary']['first_error_at'])
        self.assertEqual(self.store.read(identity='b' * 32)['summary']['requests'], 0)

    def test_row_limit_retention_disabled_and_errors_only(self):
        policy = obs.Settings(**{**obs.DEFAULTS, 'max_events': 100})
        self.store.configure(policy)
        for number in range(103): self.store.append(self.event(request_id=f'{number:032x}'))
        result = self.store.read(limit=120)
        self.assertEqual(len(result['events']), 100)
        self.assertEqual(result['events'][-1]['request_id'], f'{3:032x}')
        self.store.append(self.event(at=time.time() - 91 * 86400))
        self.assertEqual(self.store.read(minutes=129600)['summary']['requests'], 100)
        self.store.configure(obs.Settings(**{**result['settings'], 'errors_only': True}))
        self.store.append(self.event())
        self.store.append(self.event(status=500))
        self.assertEqual(self.store.read()['summary']['errors'], 1)
        self.store.configure(obs.Settings(**{**self.store.settings(), 'enabled': False}))
        self.store.append(self.event(status=500))
        self.assertEqual(self.store.read()['summary']['errors'], 1)

    def test_revision_prevents_concurrent_overwrite_and_parallel_writes_survive(self):
        first = obs.Settings(**obs.DEFAULTS)
        self.store.configure(first)
        with self.assertRaises(HTTPException) as error: self.store.configure(first)
        self.assertEqual(error.exception.status_code, 409)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.store.append(self.event()), range(30)))
        self.assertEqual(self.store.read()['summary']['requests'], 30)

    def test_clear_preserves_collection_policy(self):
        policy = self.store.configure(obs.Settings(**{**obs.DEFAULTS, 'errors_only': True}))
        self.store.append(self.event(status=500))
        self.store.clear()
        result = self.store.read()
        self.assertEqual(result['summary']['requests'], 0)
        self.assertEqual(result['settings'], policy)

    def test_middleware_never_stores_request_urls_secrets_or_exception_messages(self):
        app = FastAPI()
        app.add_middleware(obs.RequestEvidence, root=lambda: self.root, service='mihomo')
        @app.get('/api/mihomo/subscriptions/{token}')
        def subscription(token: str, request: Request):
            request.state.observed_profile = 'profile-one'
            if token == 'expired': raise HTTPException(403, 'secret exception message')
            return PlainTextResponse('secret yaml credentials', media_type='text/yaml')
        @app.get('/crash')
        def crash(): raise ValueError('private-key-in-exception')
        @app.get('/api/observability/events')
        def own_read(): return {}
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get('/api/mihomo/subscriptions/private-subscription-token?password=private-query',
                                  headers={'Authorization': 'Bearer private-auth', 'X-Request-ID': 'attacker-id'})
            identity = response.headers['x-request-id']
            self.assertRegex(identity, '^[0-9a-f]{32}$')
            client.get('/api/mihomo/subscriptions/expired')
            client.get('/crash')
            client.get('/private-unmatched-url')
            client.get('/api/observability/events')
        result = self.store.read()
        self.assertEqual(result['summary']['requests'], 4)
        self.assertEqual(result['summary']['affected_profiles'], 1)
        self.assertEqual(result['subscriptions']['succeeded'], 1)
        self.assertEqual(self.store.read(identity=identity)['events'][0]['route'], '/api/mihomo/subscriptions/{token}')
        text = json.dumps(result)
        for secret in ('private-subscription-token', 'private-query', 'private-auth', 'secret yaml', 'secret exception', 'private-key-in-exception', 'private-unmatched-url'):
            self.assertNotIn(secret, text)

    def test_storage_failure_does_not_break_the_request(self):
        app = FastAPI()
        app.add_middleware(obs.RequestEvidence, root=lambda: self.root, service='panel')
        @app.get('/ping')
        def ping(): return {'ok': True}
        with patch.object(obs.Store, 'append', side_effect=sqlite3.OperationalError('full')), TestClient(app) as client:
            self.assertEqual(client.get('/ping').json(), {'ok': True})

    def test_real_mihomo_subscription_expiry_records_profile_without_token(self):
        with patch.object(manager, 'DATA_ROOT', self.root / 'mihomo'), \
             patch.object(manager, 'subscription_mutation_access', return_value=False), \
             patch.object(manager.subscription_signing, 'server_signer', return_value=object()), \
             patch.object(manager, 'profiles', return_value=[{'id': 'known-profile', 'subscription_token': 'private-token'}]), \
             patch.object(manager.subscription_limits, 'status', return_value={'state': 'expired'}):
            manager.app.dependency_overrides[manager.subscription_mutation_access] = lambda: False
            try:
                with TestClient(manager.app) as client:
                    response = client.get('/api/mihomo/subscriptions/private-token')
            finally: manager.app.dependency_overrides.clear()
        self.assertEqual(response.status_code, 403)
        result = self.store.read()
        self.assertEqual(result['summary']['affected_profiles'], 1)
        self.assertEqual(result['events'][0]['reason'], 'subscription_expired')
        self.assertNotIn('private-token', json.dumps(result))

    def test_connections_do_not_invent_complete_counts(self):
        data = {'items': [{'connections': {'one': {'active': True, 'active_connections': 3}, 'two': {'active': True, 'handshake_age_s': 5}}}]}
        with patch.object(assistant_mihomo, 'request', return_value=data): result = control.connections(api)
        self.assertIsNone(result['established_connections'])
        self.assertEqual(result['known_established_connections'], 3)
        self.assertEqual(result['observed_active_channels'], 2)
        with patch.object(assistant_mihomo, 'request', side_effect=HTTPException(503, 'unavailable')):
            self.assertFalse(control.connections(api)['available'])

    def test_mcp_requires_permissions_and_confirmation_for_settings(self):
        settings = {'accounts': [], 'revision': 'a' * 32}
        with patch.object(assistant_control, 'read', return_value=settings), patch.object(assistant_control, '_read', return_value=settings):
            with self.assertRaises(HTTPException) as error: control.call(api, {}, '42')
            self.assertEqual(error.exception.status_code, 403)
            result = control.call(api, {'method': 'PATCH', 'path': '/settings', 'body': {**obs.DEFAULTS, 'enabled': False}}, 'panel')
            self.assertTrue(result['confirmation_required'])
            self.assertTrue(self.store.settings()['enabled'])
            record = assistant_control.operations.read(assistant_commands.pending_path(api, 'panel'))
            assistant_commands.execute(api, 'panel', '/confirm ' + record['code'])
            self.assertFalse(self.store.settings()['enabled'])
            self.assertIn('сохранены', assistant_commands.execute(api, 'panel', '/confirm ' + record['code']))

    def test_journal_is_bounded_allowlisted_and_masks_subscription_links(self):
        with patch.object(api, 'managed_services', return_value={}), \
             patch.object(api, 'run', return_value='GET /s/private-token https://host/s/private-token token=private-key') as run, \
             patch.object(assistant_control, 'read', return_value={'accounts': []}):
            result = control.journal(api, service='dns', minutes=15, priority='err')
            self.assertNotIn('private-token', json.dumps(result))
            self.assertNotIn('private-key', json.dumps(result))
            self.assertIn('systemd-resolved.service', run.call_args.args)
            self.assertTrue(run.call_args.kwargs['check'])
            for kwargs in ({'service': '--file=/etc/shadow'}, {'minutes': -1}, {'limit': 2000}, {'priority': 'invalid'}):
                with self.assertRaises(HTTPException): control.journal(api, **kwargs)


if __name__ == '__main__': unittest.main()
