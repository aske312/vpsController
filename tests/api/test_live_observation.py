import asyncio
import json
import types
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from tests.api.support import api
import journal_stream
import network_observer as network
import observability_control
import terminal_observer


class NetworkObservationTests(unittest.TestCase):
    def test_fixed_argv_and_separate_capability_boundary(self):
        query = network.Query(source='conntrack', protocol='tcp', family='ipv6')
        command = network.launch(query, network.command(query, '/usr/sbin/conntrack'))
        self.assertEqual(command[-8:], ['/usr/sbin/conntrack', '-L', '-p', 'tcp', '-f', 'ipv6', '--state', 'ESTABLISHED'])
        self.assertIn('--property=CapabilityBoundingSet=CAP_NET_ADMIN', command)
        with patch.object(terminal_observer.shutil, 'which', return_value='/usr/bin/ss'):
            self.assertNotIn('CAP_NET_ADMIN', ' '.join(terminal_observer.launch_command(['ss'], 1000, 1000, unit='test')))
        for body in ({'source': 'conntrack', 'args': ['-F']}, {'protocol': 'tcp; reboot'}, {'limit': 121}):
            with self.assertRaises(HTTPException): network.parse(body)

    def test_unavailable_is_not_zero_and_output_is_bounded(self):
        with patch.object(network.shutil, 'which', return_value=None):
            result = network.collect(api, network.Query(source='conntrack'))
        self.assertFalse(result['available'])
        self.assertIsNone(result['observed_count'])
        with patch.object(network.shutil, 'which', return_value='/usr/bin/ss'), \
                patch.object(terminal_observer, 'bounded_run', return_value=('a\nb\nc\n', 0, False, False)), \
                patch('assistant_diagnostics.sanitize', side_effect=lambda _, text, **kw: text):
            result = network.collect(api, network.Query(source='ss', limit=2))
        self.assertTrue(result['truncated'])
        self.assertEqual(result['lines'], ['a', 'b'])
        self.assertIsNone(result['total_count'])


class JournalStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_releases_process_and_secrets_are_redacted(self):
        class Process:
            returncode = None
            killed = False
            stdout = types.SimpleNamespace(readline=AsyncMock(return_value=json.dumps({
                '__CURSOR': 's=abc;i=1', '__REALTIME_TIMESTAMP': '1000000', 'MESSAGE': 'token=hidden', '_SYSTEMD_UNIT': 'api.service'}).encode()))
            def kill(self): self.killed = True
            async def wait(self): self.returncode = -9
        process = Process()
        lease = journal_stream.reserve('test')
        with patch.object(journal_stream.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)), \
                patch.object(api, 'managed_services', return_value={}), \
                patch('assistant_diagnostics.control.read', return_value={}):
            stream = journal_stream.events(api, types.SimpleNamespace(is_disconnected=AsyncMock(return_value=False)), lease, observability_control.JournalQuery())
            row = await anext(stream)
            self.assertIn('[redacted]', row)
            self.assertNotIn('hidden', row)
            await stream.aclose()
        self.assertTrue(process.killed)
        self.assertNotIn('test', journal_stream._users)

    async def test_rotation_failure_emits_gap(self):
        process = types.SimpleNamespace(returncode=1, stdout=types.SimpleNamespace(readline=AsyncMock(return_value=b'')))
        lease = journal_stream.reserve('test')
        with patch.object(journal_stream.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)), patch.object(api, 'managed_services', return_value={}):
            rows = [row async for row in journal_stream.events(api, types.SimpleNamespace(is_disconnected=AsyncMock(return_value=False)), lease, observability_control.JournalQuery(cursor='s=old'))]
        self.assertIn('gap', rows[0])

    async def test_caps_and_authentication(self):
        leases = [journal_stream.reserve(user) for user in ('one', 'one', 'two', 'two')]
        try:
            for user in ('one', 'three'):
                with self.assertRaises(HTTPException): journal_stream.reserve(user)
        finally:
            for lease in leases: journal_stream.release(lease)
        client = TestClient(api.app)
        self.assertEqual(client.get('/api/observability/journal/stream').status_code, 401)
        with patch.object(api, 'managed_services', return_value={}):
            with self.assertRaises(HTTPException): observability_control.journal_arguments(api, cursor='cursor\n--file=/etc/shadow')
