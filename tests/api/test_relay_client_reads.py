import concurrent.futures
import threading
import unittest
from unittest.mock import patch

from tests.api.support import api
from relay_client import RelayClient, AgentAPIError
from read_cache import InFlightReads


class RelayReadTests(unittest.TestCase):
    def client(self, token='s'*48, pin='a'*64):
        return RelayClient('https://85.209.155.229:9443', token, pin)

    def test_overlapping_reads_share_io_but_not_mutable_results_or_completed_observations(self):
        entered, joined, release = threading.Event(), threading.Event(), threading.Event()
        class WatchedFuture(concurrent.futures.Future):
            def result(self, *args, **kwargs):
                joined.set()
                return super().result(*args, **kwargs)
        def read(*args):
            entered.set()
            if not release.wait(3): raise TimeoutError('Fixture was not released')
            return {'items': []}
        with patch('relay_client._reads', InFlightReads()), patch('read_cache.Future', WatchedFuture), \
                patch.object(RelayClient, '_request', side_effect=read) as io, concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.client().request, 'GET', '/v1/status')
            try:
                self.assertTrue(entered.wait(2))
                second = pool.submit(self.client().request, 'GET', '/v1/status')
                self.assertTrue(joined.wait(2))
            finally:
                release.set()
            a, b = first.result(3), second.result(3)
            self.assertEqual(io.call_count, 1)
            a['items'].append('local')
            self.assertEqual(b, {'items': []})
            self.client().request('GET', '/v1/status')
            self.assertEqual(io.call_count, 2)
            for _ in range(2): self.client().request('PUT', '/v1/routes/test', {}, 1)
            self.assertEqual(io.call_count, 4)

    def test_other_credentials_or_pins_do_not_wait_on_a_slow_read_and_failures_are_not_retained(self):
        entered, release = threading.Event(), threading.Event()
        def read(client, *args):
            if client.token=='s'*48 and client.fingerprint=='a'*64:
                entered.set()
                if not release.wait(3): raise TimeoutError('Fixture was not released')
                raise AgentAPIError(495, 'fixture pin mismatch')
            return {'version':'fresh'}
        with patch('relay_client._reads', InFlightReads()), patch.object(RelayClient, '_request', read), \
                concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            slow=pool.submit(self.client().request, 'GET', '/v1/status')
            try:
                self.assertTrue(entered.wait(2))
                for client in (self.client(token='t'*48), self.client(pin='b'*64)):
                    self.assertEqual(pool.submit(client.request,'GET','/v1/status').result(1), {'version':'fresh'})
            finally: release.set()
            with self.assertRaises(AgentAPIError): slow.result(3)
        with patch.object(RelayClient, '_request', return_value={'version':'recovered'}) as io:
            self.assertEqual(self.client().request('GET','/v1/status'), {'version':'recovered'})
            io.assert_called_once()
