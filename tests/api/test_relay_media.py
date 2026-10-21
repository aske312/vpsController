import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests.api.support import api
import relay_diagnostics
import relay_media
import relay_test_runner
from network_probe import ProbePlan, ProbeUnavailable


class RelayMediaTests(unittest.TestCase):
    def test_mihomo_plan_preserves_export_security_and_rejects_different_socket(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'mihomo').mkdir()
            profiles = [{'id': 'profile', 'connections': [{'id': 'connection', 'device_id': 'phone', 'component': 'transport-tuic', 'settings': {'relay_host_id': 'host'}}]}]
            (root/'mihomo/profiles.json').write_text(json.dumps(profiles))
            route = {'id': 'route', 'transport': 'udp', 'listen_port': 20001, 'target_ip': '8.8.8.8', 'target_port': 443}
            host = {'id': 'host', 'certificate_sha256': 'a'*64}
            control = SimpleNamespace(addresses=lambda _: {'ipv4': ['1.1.1.1'], 'ipv6': [], 'domains': []})
            context = SimpleNamespace(DATA_DIR=root, ADMIN_USER='test', ADMIN_PASSWORD='not-real', relay_control=lambda: control)
            node = {'type': 'tuic', 'server': '1.1.1.1', 'port': 20001, 'uuid': 'private', 'password': 'secret', 'sni': 'identity.example', 'fingerprint': 'b'*64, 'skip-cert-verify': True}
            with patch.object(relay_media.urllib.request, 'build_opener') as opener:
                read = opener.return_value.open.return_value.__enter__.return_value.read
                read.return_value = json.dumps({'proxies': [node]}).encode()
                plan = relay_media.mihomo_plan(context, route, host, 'mihomo:profile:connection')
                self.assertEqual(plan.client, node)
                self.assertEqual(plan.engine, 'mihomo')
                self.assertNotIn('secret', repr(plan))
                for changed in ({'server': '8.8.8.8'}, {'port': 20002}):
                    read.return_value = json.dumps({'proxies': [{**node, **changed}]}).encode()
                    with self.assertRaises(ProbeUnavailable): relay_media.mihomo_plan(context, route, host, 'mihomo:profile:connection')
                read.return_value = b'not an export'
                with self.assertRaises(ProbeUnavailable): relay_media.mihomo_plan(context, route, host, 'mihomo:profile:connection')

    def test_success_requires_full_video_and_all_ranges_and_cleans_client(self):
        import hashlib
        body = bytes(range(256)) * 18000
        process = Mock(); process.poll.return_value = None
        def transfer(_port, _path, byte_range=None):
            return body if byte_range is None else body[byte_range[0]:byte_range[1]+1]
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder)/'core'; binary.touch()
            with patch.object(relay_test_runner, 'MEDIA_SIZE', len(body)), patch.object(relay_test_runner, 'MEDIA_SHA256', hashlib.sha256(body).hexdigest()), patch.object(relay_test_runner, 'transfer', side_effect=transfer) as read, patch.object(relay_media.subprocess, 'Popen', return_value=process), patch.object(relay_media.socket, 'create_connection', return_value=unittest.mock.MagicMock()):
                result = relay_media.execute(self.plan(), str(binary))
            self.assertEqual(result['state'], 'ready')
            self.assertEqual(read.call_count, 4)
            self.assertEqual(result['range_checks'], 3)
            process.terminate.assert_called_once()
            self.assertFalse(Path(read.call_args_list[0].args[1]).exists())

    def test_timeout_cannot_report_success_and_terminates_client(self):
        process = Mock(); process.poll.return_value = None
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder)/'core'; binary.touch()
            with patch.object(relay_test_runner, 'transfer', side_effect=TimeoutError), patch.object(relay_media.subprocess, 'Popen', return_value=process), patch.object(relay_media.socket, 'create_connection', return_value=unittest.mock.MagicMock()):
                result = relay_media.execute(self.plan(), str(binary))
            self.assertEqual(result, {'state': 'failed', 'reason': 'timeout'})
            process.terminate.assert_called_once()

    def plan(self, engine='mihomo'):
        client = {'type': 'tuic', 'server': '1.1.1.1', 'port': 20001, 'uuid': 'private', 'password': 'secret'} if engine == 'mihomo' else {'protocol': 'vless', 'settings': {'vnext': []}}
        return ProbePlan('route', 1, 'consumer', '1.1.1.1', 20001, '', 443, 'udp', 'fp', client, engine=engine)

    def test_isolated_clients_have_one_outbound_without_fallback_or_system_tun(self):
        for engine in ('mihomo', 'xray', 'singbox'):
            plan = self.plan(engine)
            original = copy.deepcopy(plan.client)
            config = relay_media.client_config(plan, 32123)
            self.assertEqual(len(config.get('proxies', config.get('outbounds', []))), 1)
            self.assertNotIn('DIRECT', json.dumps(config))
            self.assertNotIn('tun', config)
            self.assertEqual(plan.client, original)

    def test_http_success_without_exact_video_is_not_accepted(self):
        self.assertFalse(relay_media.verify_content(b'HTTP 200 OK'))
        self.assertFalse(relay_media.verify_content(b'0' * relay_media.MEDIA_SIZE))

    def test_curl_must_use_socks_without_environment_bypass_or_redirect(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'media'; target.write_bytes(b'video')
            with patch.object(relay_media.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
                self.assertEqual(relay_media.transfer(1234, target), b'video')
            args = run.call_args.args[0]
            self.assertIn('--socks5-hostname', args)
            self.assertEqual(args[args.index('--noproxy')+1], '')
            self.assertNotIn('--location', args)
            self.assertEqual(args[-1], relay_media.MEDIA_URL)

    def test_active_awg_peer_is_rejected_before_requesting_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'mihomo').mkdir()
            profiles = [{'id': 'profile', 'connections': [{'id': 'connection', 'component': 'transport-awg', 'settings': {'relay_host_id': 'host'}}]}]
            (root/'mihomo/profiles.json').write_text(json.dumps(profiles))
            with patch.object(relay_media.urllib.request, 'build_opener') as opener, self.assertRaises(ProbeUnavailable):
                relay_media.mihomo_plan(SimpleNamespace(DATA_DIR=root), {}, {'id': 'host'}, 'mihomo:profile:connection')
            opener.assert_not_called()

    def test_malformed_diagnostics_never_reaches_ui(self):
        valid = {'sent_packets': 2, 'received_packets': 1, 'dropped_packets': 1, 'last_error': 'udp_buffer_full', 'last_error_at': 123, 'error_counts': {'udp_buffer_full': 1}}
        self.assertEqual(relay_diagnostics.diagnostics(valid)['dropped_packets'], 1)
        for change in ({'last_error': 'secret raw error'}, {'sent_packets': -1}, {'error_counts': {'token': 1}}, {'last_error_at': 2**53}):
            with self.assertRaises(ValueError): relay_diagnostics.diagnostics({**valid, **change})
        self.assertEqual(relay_diagnostics.updates({'enabled': True, 'interval_hours': 24, 'state': 'current', 'version': '1.2.0'})['version'], '1.2.0')
        with self.assertRaises(ValueError): relay_diagnostics.updates({'enabled': True, 'interval_hours': 24, 'state': 'raw log secret'})


if __name__ == '__main__': unittest.main()
