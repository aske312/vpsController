import threading
import time
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from tests.api.support import api
import assistant_broker


class BrokerTests(unittest.TestCase):
    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            if time.monotonic() > deadline: self.fail('Queue did not reach the expected state')
            threading.Event().wait(.005)

    def test_same_provider_serializes_and_alternates_users_without_reordering_their_questions(self):
        broker = assistant_broker.Broker(wait_seconds=3)
        release = threading.Event()
        order, errors, threads = [], [], []
        def request(actor, identity, hold=False):
            try:
                with broker.admit(actor, 'provider', identity):
                    order.append(identity)
                    if hold: release.wait(3)
            except Exception as exc: errors.append(exc)
        def launch(actor, identity, hold=False):
            thread = threading.Thread(target=request, args=(actor, identity, hold)); thread.start(); threads.append(thread)
        try:
            launch('A', 'A0', True)
            self.wait_for(lambda: order == ['A0'])
            launch('A', 'A1'); self.wait_for(lambda: broker.status('A', 'A1')['state'] == 'queued')
            launch('A', 'A2'); self.wait_for(lambda: broker.status('A', 'A2')['state'] == 'queued')
            launch('B', 'B1'); self.wait_for(lambda: broker.status('B', 'B1')['state'] == 'queued')
            self.assertEqual(broker.status('A', 'B1')['state'], 'unknown')
            release.set()
        finally:
            release.set()
            for thread in threads: thread.join(4)
        self.assertFalse(errors)
        self.assertEqual(order, ['A0', 'B1', 'A1', 'A2'])

    def test_independent_providers_can_work_in_parallel(self):
        broker = assistant_broker.Broker()
        release = threading.Event(); entered = threading.Event()
        def first():
            with broker.admit('A', 'one', 'first'):
                entered.set(); release.wait(3)
        thread = threading.Thread(target=first); thread.start(); self.assertTrue(entered.wait(2))
        try:
            with broker.admit('B', 'two', 'second'):
                self.assertEqual(broker.status('B', 'second')['running'], 2)
        finally: release.set(); thread.join(3)

    def test_spam_is_bounded_and_wait_timeout_never_starts_the_request(self):
        broker = assistant_broker.Broker(capacity=2, per_actor=1, wait_seconds=.02)
        with broker.admit('A', 'same', 'active'):
            with self.assertRaises(HTTPException) as duplicate_user:
                with broker.admit('A', 'same', 'spam'): self.fail('Spam was executed')
            self.assertEqual(duplicate_user.exception.status_code, 429)
            with self.assertRaises(HTTPException) as expired:
                with broker.admit('B', 'same', 'waiting'): self.fail('Expired request was executed')
            self.assertEqual(expired.exception.status_code, 503)
            self.assertEqual(broker.status('B', 'waiting')['state'], 'not_started')
            self.assertEqual(broker.status('A', 'active')['waiting'], 0)

    def test_failure_releases_capacity_but_same_request_id_is_never_replayed(self):
        broker = assistant_broker.Broker()
        with self.assertRaises(RuntimeError):
            with broker.admit('A', 'one', 'id'): raise RuntimeError('fixture')
        with self.assertRaises(HTTPException) as duplicate:
            with broker.admit('A', 'one', 'id'): self.fail('Duplicate executed')
        self.assertEqual(duplicate.exception.status_code, 409)
        with broker.admit('B', 'one', 'next'): self.assertEqual(broker.status('B', 'next')['running'], 1)

    def test_provider_cooldown_delays_its_queue_but_leaves_other_providers_available(self):
        broker = assistant_broker.Broker(wait_seconds=.02)
        broker.defer('limited', 1)
        with broker.admit('A', 'healthy', 'available'): pass
        with self.assertRaises(HTTPException):
            with broker.admit('B', 'limited', 'limited'): self.fail('Rate-limited provider called')

    def test_queue_endpoint_requires_panel_authentication(self):
        from fastapi.testclient import TestClient
        self.assertEqual(TestClient(api.app).get('/api/assistant/queue').status_code, 401)
        self.assertEqual(TestClient(api.app).get('/api/assistant/bot-queue').status_code, 401)

    def test_attachment_backlog_has_a_total_memory_budget(self):
        broker = assistant_broker.Broker()
        with broker.admit('A', 'one', 'first', weight=25 * 1024 * 1024):
            with self.assertRaises(HTTPException) as full:
                with broker.admit('B', 'two', 'second', weight=8 * 1024 * 1024): self.fail('Memory budget exceeded')
            self.assertEqual(full.exception.status_code, 429)

    def test_two_real_http_requests_queue_and_finish_with_separate_results(self):
        from fastapi.testclient import TestClient
        import assistant_control as control
        broker = assistant_broker.Broker(wait_seconds=3)
        client = TestClient(api.app)
        entered, release = threading.Event(), threading.Event()
        responses, threads = {}, []
        settings = {'revision': 'a' * 32, 'provider_url': 'https://fixture.example/v1',
                    'accounts': [{'telegram_id': actor, 'bot_chat_access': True} for actor in (11, 22)]}
        def work(env, payload, actor, channel, **kwargs):
            if actor == '11': entered.set(); release.wait(3)
            return {'ok': True, 'result': {'answer': actor, 'tools_used': []}}
        def post(actor):
            responses[actor] = client.post('/api/assistant/chat', json={'prompt': 'fixture', 'telegram_id': actor, 'request_id': str(actor) * 16})
        previous = api.app.dependency_overrides.get(api.require_token)
        api.app.dependency_overrides[api.require_token] = lambda: None
        try:
            with patch.object(assistant_broker, 'broker', broker), patch.object(control, 'read', return_value=settings), patch.object(control, '_chat_request', side_effect=work):
                first = threading.Thread(target=post, args=(11,)); first.start(); threads.append(first)
                self.assertTrue(entered.wait(2))
                second = threading.Thread(target=post, args=(22,)); second.start(); threads.append(second)
                self.wait_for(lambda: broker.status('22', '22' * 16)['state'] == 'queued')
                queue = client.get('/api/assistant/queue', params={'telegram_id': 22, 'request_id': '22' * 16})
                self.assertEqual(queue.json()['state'], 'queued')
                release.set()
                for thread in threads: thread.join(4)
            for actor in (11, 22):
                self.assertEqual(responses[actor].status_code, 200)
                self.assertEqual(responses[actor].json()['result']['answer'], str(actor))
        finally:
            release.set()
            for thread in threads: thread.join(4)
            if previous is None: api.app.dependency_overrides.pop(api.require_token, None)
            else: api.app.dependency_overrides[api.require_token] = previous
