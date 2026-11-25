"""Keep response work bounded without serving stale Relay permissions or state."""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch

import httpx

from tests.api.support import load_module, manager
from tests.api.test_profile_revision import profile

bot = load_module('latency_bot', 'protocol-images/nodera-bot/bot.py')


class RelayResponseTests(unittest.TestCase):
    def test_many_connections_share_one_registry_and_next_request_sees_removal(self):
        item = profile()
        item['connections'][0]['settings']['relay_host_id'] = 'a' * 32
        item['connections'] *= 20
        other = deepcopy(item)
        other['id'] = 'two'
        with (patch.object(manager, 'profiles', return_value=[item, other]),
              patch.object(manager, 'verified_profile_clients', return_value=[]),
              patch.object(manager, 'relay_connection_control') as factory):
            registry = factory.return_value.read
            factory.return_value.pending = None
            registry.return_value = {'hosts': [{'id': 'a' * 32}]}
            before = manager.list_profiles()
            registry.assert_called_once()
            registry.return_value = {'hosts': []}
            after = manager.list_profiles()
            self.assertEqual(registry.call_count, 2)
        self.assertEqual(len(before['items'][0]['connections']), 20)
        for entry in after['items']:
            for connection in entry['connections']:
                self.assertEqual(connection['route_state']['state'], 'stale')
        self.assertEqual(item['connections'][0]['settings']['relay_host_id'], 'a' * 32)

    def test_unreadable_registry_is_read_once_and_marks_all_relay_routes_unknown(self):
        item = profile()
        item['connections'][0]['settings']['relay_host_id'] = 'a' * 32
        item['connections'] *= 20
        with (patch.object(manager, 'verified_profile_clients', return_value=[]),
              patch.object(manager, 'relay_connection_control') as factory):
            factory.return_value.read.side_effect = ValueError('Invalid registry')
            result = manager.profile_response(item)
            factory.return_value.read.assert_called_once()
        self.assertTrue(all(c['route_state']['state'] == 'unknown' for c in result['connections']))


class TelegramTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_pool_allows_delivery_during_polling_and_closes_on_cancellation(self):
        polling = asyncio.Event()
        release = asyncio.Event()

        async def transport(request):
            if request.url.path.endswith('/getUpdates'):
                polling.set()
                await release.wait()
            return httpx.Response(200, json={'ok': True, 'result': []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))

        async def run(settings):
            poll = asyncio.create_task(bot.bot_api(settings, 'getUpdates'))
            try:
                await polling.wait()
                await asyncio.wait_for(bot.bot_api(settings, 'getMe'), timeout=1)
                self.assertFalse(client.is_closed)
                raise asyncio.CancelledError()
            finally:
                release.set()
                await poll

        with (patch.object(bot.httpx, 'AsyncClient', return_value=client) as factory,
              patch.object(bot, 'run_bot', side_effect=run)):
            with self.assertRaises(asyncio.CancelledError):
                await bot.bot({'bot_token': 'test'})
            factory.assert_called_once()
        self.assertTrue(client.is_closed)
        self.assertIsNone(bot.telegram_client.get())
