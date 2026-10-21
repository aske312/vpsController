import io
import json
import threading
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
from tests.api.support import api


class ResourceCountryTests(unittest.TestCase):
    def lookup(self, rows):
        opener = Mock(side_effect=lambda *args, **kwargs: io.BytesIO(json.dumps(rows).encode()))
        clock = [10.0]
        service = api.resource_country.CountryLookup(opener=opener, clock=lambda: clock[0])
        return service, opener, clock

    def test_public_ips_are_batched_deduplicated_and_cached(self):
        service, opener, clock = self.lookup([{'ip': '8.8.8.8', 'country': 'US'}, {'ip': '2606:4700::1111', 'country': 'AU'}])
        result = service.lookup(['8.8.8.8', '8.8.8.8', '2606:4700::1111'])
        self.assertEqual([item['country_code'] for item in result['items']], ['US', 'AU'])
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.country.is/')
        self.assertEqual(json.loads(request.data), ['8.8.8.8', '2606:4700::1111'])
        self.assertEqual(opener.call_args.kwargs['timeout'], 4)
        clock[0] += 86399
        service.lookup(['8.8.8.8'])
        self.assertEqual(opener.call_count, 1)
        clock[0] += 2
        service.lookup(['8.8.8.8', '2606:4700::1111'])
        self.assertEqual(opener.call_count, 2)

    def test_private_reserved_and_multicast_are_never_disclosed(self):
        service, opener, _ = self.lookup([])
        ips = ['127.0.0.1', '10.0.0.1', '192.168.1.1', '169.254.169.254', '203.0.113.1', '224.0.0.1', '::1', 'fd00::1', 'ff02::1']
        result = service.lookup(ips)
        self.assertTrue(all(item['country_code'] is None for item in result['items']))
        self.assertFalse(result['pending'])
        opener.assert_not_called()

    def test_failure_is_negative_cached_and_does_not_invent_countries(self):
        service, opener, clock = self.lookup([])
        opener.side_effect = OSError('unavailable')
        self.assertIsNone(service.lookup(['8.8.8.8'])['items'][0]['country_code'])
        clock[0] += 299
        service.lookup(['8.8.8.8'])
        self.assertEqual(opener.call_count, 1)
        clock[0] += 2
        service.lookup(['8.8.8.8'])
        self.assertEqual(opener.call_count, 2)

    def test_unrelated_duplicate_or_malformed_evidence_is_rejected(self):
        for rows in ([{'ip': '1.1.1.1', 'country': 'US'}], [{'ip': '8.8.8.8', 'country': '../US'}],
                     [{'ip': '8.8.8.8', 'country': 'US'}] * 2, {'country': 'US'}):
            with self.subTest(rows=rows):
                service, _, _ = self.lookup(rows)
                self.assertIsNone(service.lookup(['8.8.8.8'])['items'][0]['country_code'])

    def test_concurrent_read_does_not_repeat_the_provider_request(self):
        entered, release = threading.Event(), threading.Event()
        def open_request(*args, **kwargs):
            entered.set()
            release.wait(3)
            return io.BytesIO(b'[{"ip":"8.8.8.8","country":"US"}]')
        opener = Mock(side_effect=open_request)
        service = api.resource_country.CountryLookup(opener=opener)
        thread = threading.Thread(target=lambda: service.lookup(['8.8.8.8']))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(service.lookup(['8.8.8.8'])['pending'])
            self.assertEqual(opener.call_count, 1)
        finally:
            release.set(); thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(service.lookup(['8.8.8.8'])['items'][0]['country_code'], 'US')

    def test_endpoint_requires_auth_and_accepts_only_bounded_ip_lists(self):
        client = TestClient(api.app)
        with patch.object(api.resource_countries, 'lookup', return_value={'items': [], 'pending': False}) as lookup:
            self.assertEqual(client.post('/api/network/locations', json={'addresses': ['8.8.8.8']}).status_code, 401)
            lookup.assert_not_called()
            with patch.object(api, 'ADMIN_USER', 'fixture'), patch.object(api, 'ADMIN_PASSWORD', 'fixture-only'):
                response = client.post('/api/network/locations', json={'addresses': ['8.8.8.8']}, auth=('fixture', 'fixture-only'))
                self.assertEqual(response.status_code, 200)
                lookup.assert_called_once_with(['8.8.8.8'])
        for addresses in ([], ['example.com'], ['https://8.8.8.8/path'], ['8.8.8.8'] * 101):
            with self.assertRaises(ValidationError):
                api.ResourceCountryRequest(addresses=addresses)
