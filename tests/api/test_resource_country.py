import io
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
from tests.api.support import api


class InlineExecutor:
    def submit(self, function, *args):
        function(*args)


class ResourceCountryTests(unittest.TestCase):
    def lookup(self, codes):
        def respond(request, **kwargs):
            index, template = next((i, url) for i, url in enumerate(api.resource_country._PROVIDER_URLS)
                                   if request.full_url.startswith(url.split("{ip}")[0]))
            prefix, suffix = template.split("{ip}")
            ip = request.full_url.removeprefix(prefix).removesuffix(suffix)
            value = codes[index]
            if isinstance(value, Exception):
                raise value
            row = value if isinstance(value, dict) else {"ip": ip, "country_code": value}
            return io.BytesIO(json.dumps(row).encode())
        opener = Mock(side_effect=respond)
        clock = [10.0]
        service = api.resource_country.CountryLookup(opener=opener, clock=lambda: clock[0], executor=InlineExecutor())
        return service, opener, clock

    def test_majority_corrects_outlier_without_address_overrides(self):
        service, opener, _ = self.lookup(['FI', 'FI', 'US', 'FI', 'US'])
        self.assertEqual(service.lookup(['85.209.155.229'])['items'][0]['country_code'], 'FI')
        self.assertEqual(opener.call_count, 5)

    def test_no_quorum_does_not_invent_a_country(self):
        for codes in (['FI', 'US', None, None, None], ['FI', None, None, None, None],
                      ['FI', 'FI', 'US', 'US', None], [None] * 5):
            with self.subTest(codes=codes):
                service, _, _ = self.lookup(codes)
                self.assertIsNone(service.lookup(['85.209.155.229'])['items'][0]['country_code'])

    def test_failures_do_not_prevent_remaining_sources_reaching_quorum(self):
        service, _, _ = self.lookup([OSError('offline'), 'FI', 'FI', None, None])
        self.assertEqual(service.lookup(['85.209.155.229'])['items'][0]['country_code'], 'FI')

    def test_cdn_and_ipv6_use_same_consensus(self):
        service, _, _ = self.lookup(['US'] * 5)
        ips = ['104.21.60.146', '172.67.197.199', '2606:4700:3033::6815:3c92']
        self.assertEqual([row['country_code'] for row in service.lookup(ips)['items']], ['US'] * 3)

    def test_deduplication_cache_expiry_and_negative_cache(self):
        for codes, ttl in ((['FI'] * 5, 86400), ([None] * 5, 300)):
            service, opener, clock = self.lookup(codes)
            service.lookup(['85.209.155.229'] * 2)
            self.assertEqual(opener.call_count, 5)
            clock[0] += ttl - 1
            service.lookup(['85.209.155.229'])
            self.assertEqual(opener.call_count, 5)
            clock[0] += 2
            service.lookup(['85.209.155.229'])
            self.assertEqual(opener.call_count, 10)

    def test_private_reserved_and_multicast_are_never_disclosed(self):
        service, opener, _ = self.lookup([])
        ips = ['127.0.0.1', '10.0.0.1', '192.168.1.1', '169.254.169.254', '203.0.113.1', '224.0.0.1', '::1', 'fd00::1', 'ff02::1']
        result = service.lookup(ips)
        self.assertTrue(all(item['country_code'] is None for item in result['items']))
        self.assertFalse(result['pending'])
        opener.assert_not_called()

    def test_provider_shapes_and_address_binding(self):
        normalize = api.resource_country.country_record
        ip = '85.209.155.229'
        for row in ({'ip': ip, 'country_code': 'FI'}, {'ipAddress': ip, 'countryCode': 'FI'},
                    {'ip': ip, 'location': {'country_code': 'FI'}},
                    {'ip': ip, 'network': {'autonomous_system': {'country': 'FI'}}},
                    {'ip': ip, 'country': 'fi'}):
            self.assertEqual(normalize(row, ip), 'FI')
        for row in ({'ip': '8.8.8.8', 'country_code': 'US'}, {'country_code': 'US'},
                    {'ip': ip, 'success': False, 'country_code': 'FI'},
                    {'ip': ip, 'country_code': '../US'}, [], {'ip': ip, 'country_code': 42}):
            self.assertIsNone(normalize(row, ip))

    def test_oversized_and_malformed_responses_are_ignored(self):
        for raw in (b'x' * 65537, b'not json', b'[]'):
            service = api.resource_country.CountryLookup(opener=lambda *a, **k: io.BytesIO(raw), executor=InlineExecutor())
            self.assertIsNone(service.lookup(['85.209.155.229'])['items'][0]['country_code'])

    def test_background_lookup_is_nonblocking_and_deduplicated(self):
        entered, release = threading.Event(), threading.Event()
        def respond(*args, **kwargs):
            entered.set()
            release.wait(3)
            return io.BytesIO(b'{"ip":"85.209.155.229","country_code":"FI"}')
        opener = Mock(side_effect=respond)
        with ThreadPoolExecutor(max_workers=1) as executor:
            service = api.resource_country.CountryLookup(opener=opener, executor=executor)
            try:
                self.assertTrue(service.lookup(['85.209.155.229'])['pending'])
                self.assertTrue(entered.wait(2))
                self.assertTrue(service.lookup(['85.209.155.229'])['pending'])
                self.assertEqual(opener.call_count, 1)
            finally:
                release.set()
        self.assertFalse(service.lookup(['85.209.155.229'])['pending'])
        self.assertEqual(service.lookup(['85.209.155.229'])['items'][0]['country_code'], 'FI')

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
