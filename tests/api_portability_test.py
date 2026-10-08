"""Isolated API contracts; never uses a deployed panel or system service."""
import importlib.util
import ipaddress
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import sys
import time
import unittest
from unittest.mock import patch
import yaml

from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location("panel_test_api", Path(__file__).resolve().parents[1] / "api/main.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)


class PortabilityTests(unittest.TestCase):
    def test_tunnel_connection_requires_fresh_handshake_and_bidirectional_traffic(self):
        with patch.object(api, 'interface_dump', return_value=[{
            'handshake_age_s': 12, 'rx_bytes': 4096, 'tx_bytes': 2048,
        }]):
            confirmed = api.observed_tunnel_connection('awg')
        self.assertEqual(confirmed['state'], 'confirmed')
        self.assertEqual(confirmed['method'], 'observed-client-traffic')

        with patch.object(api, 'interface_dump', return_value=[{
            'handshake_age_s': 12, 'rx_bytes': 4096, 'tx_bytes': 0,
        }]):
            unverified = api.observed_tunnel_connection('awg')
        self.assertEqual(unverified['state'], 'unverified')
        self.assertIn('двусторонняя', unverified['detail'])

    def test_direct_diagnostics_do_not_claim_healthy_without_data_plane_probe(self):
        with patch.object(api, 'protocol_listener', return_value=('unit.service', 8443, 'udp', True)), \
             patch.object(api, 'run', return_value='active'), \
             patch.object(api, 'connection_probe_cache', {}):
            diagnostics = api.direct_protocol_diagnostics('hysteria2')
        self.assertEqual(diagnostics['status'], 'warning')
        self.assertEqual(diagnostics['checks'][-1]['state'], 'unknown')
        self.assertEqual(diagnostics['findings'][0]['code'], 'data_plane_unverified')

    def test_direct_probe_configs_use_existing_accounts_without_mutating_server_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hysteria = root / 'hysteria2'; hysteria.mkdir()
            (hysteria / 'settings.json').write_text('{"port":8443,"listen":"8443-8450","obfs":"gecko","obfs_password":"probe-obfs","gecko_min_packet_size":512,"gecko_max_packet_size":1200}', encoding='utf-8')
            hysteria_probe = api.diagnostic_client_id('hysteria2')
            (hysteria / 'users.json').write_text(json.dumps({
                'real-user': 'real-password', hysteria_probe: 'diagnostic-password',
            }), encoding='utf-8')
            (hysteria / 'server.crt').write_text('certificate', encoding='utf-8')
            tuic = root / 'tuic'; tuic.mkdir()
            (tuic / 'settings.json').write_text('{"port":8444,"congestion_control":"bbr","heartbeat":"10s"}', encoding='utf-8')
            (tuic / 'config.json').write_text(json.dumps({'inbounds': [{'type': 'tuic', 'users': [
                {'name': 'real-user', 'uuid': 'tuic-real', 'password': 'real-password'},
                {'name': api.diagnostic_client_id('tuic'), 'uuid': 'tuic-diagnostic', 'password': 'diagnostic-password'},
            ]}]}), encoding='utf-8')
            (tuic / 'server.crt').write_text('certificate', encoding='utf-8')
            xray = root / 'xray'; xray.mkdir()
            (xray / 'settings.json').write_text(json.dumps({'port': 8445, 'path': '/probe', 'server_name': 'example.com', 'password': 'public-key', 'short_id': '0123456789abcdef'}), encoding='utf-8')
            (xray / 'config.json').write_text(json.dumps({'inbounds': [{'protocol': 'vless', 'settings': {'clients': [
                {'id': 'xray-real', 'email': 'real@312.net'},
                {'id': 'xray-diagnostic', 'email': f"{api.diagnostic_client_id('xray')}@312.net"},
            ]}}]}), encoding='utf-8')
            originals = {
                path: path.read_bytes()
                for path in (hysteria / 'settings.json', hysteria / 'users.json', tuic / 'config.json', xray / 'config.json')
            }
            with patch.multiple(
                api,
                HYSTERIA2_DIR=hysteria, HYSTERIA2_SETTINGS=hysteria / 'settings.json', HYSTERIA2_USERS=hysteria / 'users.json',
                TUIC_DIR=tuic, TUIC_SETTINGS=tuic / 'settings.json', TUIC_CONFIG=tuic / 'config.json',
                XRAY_DIR=xray, XRAY_SETTINGS=xray / 'settings.json', XRAY_CONFIG=xray / 'config.json',
            ), patch.object(api, 'certificate_server_name', return_value='endpoint.internal'), \
                 patch.object(api, 'run', return_value='SHA256 Fingerprint=AA:BB'):
                commands = {protocol: api.direct_probe_client(protocol, 19080, root) for protocol in api.DIRECT_PROTOCOLS}

            hysteria_config = (root / 'hysteria2.yaml').read_text(encoding='utf-8')
            self.assertIn('pinSHA256: AA:BB', hysteria_config)
            self.assertIn(f'auth: {hysteria_probe}:diagnostic-password', hysteria_config)
            self.assertIn('server: 127.0.0.1:8443-8450', hysteria_config)
            self.assertIn('type: gecko', hysteria_config)
            self.assertIn('minHopInterval: 15s', hysteria_config)
            tuic_probe = json.loads((root / 'tuic.json').read_text(encoding='utf-8'))
            self.assertEqual(tuic_probe['outbounds'][0]['uuid'], 'tuic-diagnostic')
            xray_probe = json.loads((root / 'xray.json').read_text(encoding='utf-8'))
            xray_target = xray_probe['outbounds'][0]['settings']['vnext'][0]
            self.assertEqual(xray_target['address'], '127.0.0.1')
            self.assertEqual(xray_target['users'][0]['id'], 'xray-diagnostic')
            self.assertEqual(xray_probe['outbounds'][0]['streamSettings']['xhttpSettings']['mode'], 'auto')
            self.assertEqual(xray_probe['outbounds'][0]['streamSettings']['realitySettings']['password'], 'public-key')
            self.assertEqual(xray_probe['outbounds'][0]['streamSettings']['security'], 'reality')
            self.assertTrue(all(commands.values()))
            for path, content in originals.items():
                self.assertEqual(path.read_bytes(), content)

    def test_hysteria_probe_identity_is_managed_and_hidden_from_user_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hysteria = root / 'hysteria2'
            hysteria.mkdir()
            users = hysteria / 'users.json'
            users.write_text('{}', encoding='utf-8')
            clients = root / 'clients.json'
            with patch.multiple(
                api,
                DATA_DIR=root,
                CLIENTS_FILE=clients,
                HYSTERIA2_DIR=hysteria,
                HYSTERIA2_USERS=users,
            ):
                self.assertTrue(api.ensure_direct_probe_identity('hysteria2'))
                self.assertFalse(api.ensure_direct_probe_identity('hysteria2'))
                identity = api.diagnostic_client_id('hysteria2')
                self.assertIn(identity, json.loads(users.read_text(encoding='utf-8')))
                stored = api.read_clients()
                self.assertEqual(len(stored), 1)
                self.assertTrue(stored[0]['diagnostic'])
                self.assertEqual(api.direct_client_rows(), [])

    def test_tuic_and_xray_probe_identities_are_validated_once_and_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tuic = root / 'tuic'; tuic.mkdir()
            xray = root / 'xray'; xray.mkdir()
            tuic_config = tuic / 'config.json'
            xray_config = xray / 'config.json'
            tuic_config.write_text(json.dumps({'inbounds': [{'type': 'tuic', 'users': []}]}), encoding='utf-8')
            xray_config.write_text(json.dumps({'inbounds': [{'protocol': 'vless', 'settings': {'clients': []}}]}), encoding='utf-8')
            validated = type('Result', (), {'returncode': 0, 'stderr': ''})()
            with patch.multiple(
                api,
                DATA_DIR=root,
                CLIENTS_FILE=root / 'clients.json',
                TUIC_CONFIG=tuic_config,
                XRAY_CONFIG=xray_config,
            ), patch.object(api.subprocess, 'run', return_value=validated) as validate, \
                 patch.object(api, 'run', return_value='active') as systemctl:
                self.assertTrue(api.ensure_direct_probe_identity('tuic'))
                self.assertTrue(api.ensure_direct_probe_identity('xray'))
                self.assertFalse(api.ensure_direct_probe_identity('tuic'))
                self.assertFalse(api.ensure_direct_probe_identity('xray'))
                stored = api.read_clients()

            tuic_users = json.loads(tuic_config.read_text(encoding='utf-8'))['inbounds'][0]['users']
            xray_users = json.loads(xray_config.read_text(encoding='utf-8'))['inbounds'][0]['settings']['clients']
            self.assertTrue(api.is_diagnostic_identity('tuic', tuic_users[0]))
            self.assertTrue(api.is_diagnostic_identity('xray', xray_users[0]))
            self.assertEqual(len([item for item in stored if item['diagnostic']]), 2)
            self.assertEqual(validate.call_count, 2)
            self.assertEqual(systemctl.call_count, 2)

    def test_direct_probe_identity_rolls_back_rejected_server_config(self):
        cases = {
            'tuic': {'inbounds': [{'type': 'tuic', 'users': []}]},
            'xray': {'inbounds': [{'protocol': 'vless', 'settings': {'clients': []}}]},
        }
        rejected = type('Result', (), {'returncode': 1, 'stderr': 'invalid config'})()
        for protocol, payload in cases.items():
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = root / f'{protocol}.json'
                config.write_text(json.dumps(payload), encoding='utf-8')
                original = config.read_bytes()
                paths = {'TUIC_CONFIG': config} if protocol == 'tuic' else {'XRAY_CONFIG': config}
                with patch.multiple(api, DATA_DIR=root, CLIENTS_FILE=root / 'clients.json', **paths), \
                     patch.object(api.subprocess, 'run', return_value=rejected), \
                     patch.object(api, 'run', return_value='active'):
                    with self.assertRaises(RuntimeError):
                        api.ensure_direct_probe_identity(protocol)
                    self.assertEqual(api.read_clients(), [])
                self.assertEqual(config.read_bytes(), original)

    def test_regional_reachability_does_not_mistake_server_probe_for_russia(self):
        reachability = api.regional_reachability('hysteria2', {
            'state': 'confirmed',
            'method': 'local-protocol-roundtrip',
        })
        self.assertEqual(reachability['state'], 'unverified')
        self.assertEqual(reachability['region'], 'RU')
        self.assertIn('российскую сеть', reachability['detail'])

    def test_fresh_external_ru_probe_is_reported_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            reports = Path(directory) / 'regional-probes.json'
            reports.write_text(json.dumps({
                'xray': {
                    'region': 'RU', 'state': 'confirmed',
                    'checked_at': datetime.now(timezone.utc).isoformat(),
                    'latency_ms': 140, 'bytes_sent': 88, 'bytes_received': 87,
                },
            }), encoding='utf-8')
            with patch.object(api, 'REGIONAL_PROBES_FILE', reports):
                reachability = api.regional_reachability('xray', {})
        self.assertEqual(reachability['state'], 'confirmed')
        self.assertEqual(reachability['method'], 'external-regional-probe')
        self.assertEqual(reachability['bytes_received'], 87)

    def test_stale_external_ru_probe_does_not_remain_green(self):
        with tempfile.TemporaryDirectory() as directory:
            reports = Path(directory) / 'regional-probes.json'
            reports.write_text(json.dumps({
                'tuic': {
                    'region': 'RU', 'state': 'confirmed',
                    'checked_at': (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
                    'latency_ms': 100, 'bytes_sent': 88, 'bytes_received': 87,
                },
            }), encoding='utf-8')
            with patch.object(api, 'REGIONAL_PROBES_FILE', reports):
                reachability = api.regional_reachability('tuic', {})
        self.assertEqual(reachability['state'], 'unverified')

    def test_external_ru_probe_report_is_persisted_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            reports = Path(directory) / 'regional-probes.json'
            with patch.object(api, 'REGIONAL_PROBES_FILE', reports):
                reachability = api.report_protocol_reachability(
                    'hysteria2',
                    api.RegionalProbeReport(
                        state='confirmed', latency_ms=90,
                        bytes_sent=88, bytes_received=87,
                    ),
                    None,
                )
            stored = json.loads(reports.read_text(encoding='utf-8'))['hysteria2']
        self.assertEqual(reachability['state'], 'confirmed')
        self.assertEqual(stored['bytes_received'], 87)
        self.assertIsNotNone(stored['checked_at'])

    def test_direct_roundtrip_reports_transferred_request_and_response_bytes(self):
        class Process:
            def poll(self): return None
            def terminate(self): pass
            def wait(self, timeout=None): return 0
            def kill(self): pass

        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'client'
            binary.write_text('', encoding='utf-8')

            def curl(command, **_kwargs):
                self.assertEqual(command[command.index('--noproxy') + 1], '')
                output = Path(command[command.index('--output') + 1])
                output.write_text('{"ok":true}', encoding='utf-8')
                return type('Result', (), {'returncode': 0, 'stdout': '200 84 15', 'stderr': ''})()

            with patch.object(api, 'protocol_listener', return_value=('unit.service', 8443, 'udp', True)), \
                 patch.object(api, 'run', return_value='active'), \
                 patch.object(api, 'ensure_direct_probe_identity', return_value=False), \
                 patch.object(api, 'check_proxy_internet', return_value={'state': 'confirmed', 'items': [], 'scope': 'External HTTPS'}), \
                 patch.object(api, 'direct_probe_client', return_value=[str(binary)]), \
                 patch.object(api.subprocess, 'Popen', return_value=Process()), \
                 patch.object(api.subprocess, 'run', side_effect=curl), \
                 patch.object(api, 'wait_for_proxy', return_value=True), \
                 patch.object(api, 'connection_probe_cache', {}):
                result = api.check_protocol_connection('hysteria2')
                with patch.object(api, 'check_proxy_internet', return_value={
                    'state': 'failed', 'items': [], 'scope': 'External HTTPS',
                }):
                    internet_failed = api.check_protocol_connection('hysteria2')

        self.assertEqual(result['state'], 'confirmed')
        self.assertEqual(result['bytes_sent'], 84)
        self.assertEqual(result['bytes_received'], 15)
        self.assertEqual(result['internet']['state'], 'confirmed')
        self.assertEqual(internet_failed['state'], 'failed')
        self.assertEqual(internet_failed['internet']['state'], 'failed')
        self.assertEqual(internet_failed['bytes_received'], 15)

    def test_proxy_internet_requires_tls_http_success_and_response_bytes(self):
        cases = [
            ((0, '200', b'page'), (0, '200', b'ip=198.51.100.1\n'), 'confirmed'),
            ((28, '000', b''), (0, '200', b'ip=198.51.100.1\n'), 'confirmed'),
            ((0, '200', b''), (0, '503', b'error'), 'failed'),
            ((60, '200', b'page'), (28, '000', b''), 'failed'),
        ]
        for first, second, expected in cases:
            with self.subTest(expected=expected, first=first), tempfile.TemporaryDirectory() as directory:
                responses = iter((first, second))

                def curl(command, **kwargs):
                    self.assertEqual(command[command.index('--noproxy') + 1], '')
                    self.assertIn('--socks5-hostname', command)
                    self.assertNotIn('--insecure', command)
                    code, status, body = next(responses)
                    Path(command[command.index('--output') + 1]).write_bytes(body)
                    return type('Result', (), {'returncode': code, 'stdout': status})()

                with patch.object(api.subprocess, 'run', side_effect=curl):
                    result = api.check_proxy_internet(1080, Path(directory))
                self.assertEqual(result['state'], expected)
                self.assertEqual(len(result['items']), 2)
                if second[0] == 0 and second[1] == '200':
                    self.assertEqual(result['items'][1]['exit_ip'], '198.51.100.1')

    def test_proxy_internet_timeout_reports_failure_without_raising(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            api.subprocess, 'run', side_effect=api.subprocess.TimeoutExpired('curl', 10),
        ):
            result = api.check_proxy_internet(1080, Path(directory))
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(len(result['items']), 2)

    def test_application_task_is_published_before_systemd_starts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            action_file = root / 'application-action.json'

            def launch(command, **_kwargs):
                published = json.loads(action_file.read_text(encoding='utf-8'))
                self.assertEqual(published['action'], 'integrity-check')
                self.assertEqual(published['state'], 'activating')
                self.assertIn(f"--unit={published['unit'].removesuffix('.service')}", command)
                self.assertIn('--property=RuntimeMaxSec=1200', command)
                return type('Result', (), {'returncode': 0, 'stderr': ''})()

            with patch.multiple(api, DATA_DIR=root, ACTION_FILE=action_file), \
                 patch.object(api, 'run', return_value='inactive'), \
                 patch.object(api.subprocess, 'run', side_effect=launch):
                action = api.start_application_task(
                    'vps-control-test', 'integrity-check', ['/bin/true'],
                    'Starting', 'Unable to start', runtime_max_seconds=1200,
                )

            self.assertEqual(action['state'], 'activating')
            self.assertEqual(json.loads(action_file.read_text(encoding='utf-8')), action)

    def test_application_task_records_launcher_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            action_file = root / 'application-action.json'
            failed = type('Result', (), {'returncode': 1, 'stderr': 'launcher failed'})()
            with patch.multiple(api, DATA_DIR=root, ACTION_FILE=action_file), \
                 patch.object(api, 'run', return_value='inactive'), \
                 patch.object(api.subprocess, 'run', return_value=failed):
                with self.assertRaises(api.HTTPException) as raised:
                    api.start_application_task(
                        'vps-control-test', 'integrity-check', ['/bin/false'],
                        'Starting', 'Unable to start',
                    )

            self.assertEqual(raised.exception.status_code, 500)
            recorded = json.loads(action_file.read_text(encoding='utf-8'))
            self.assertEqual(recorded['state'], 'failed')
            self.assertEqual(recorded['result'], 'failed')
            self.assertEqual(recorded['message'], 'launcher failed')

    def test_missing_transient_unit_is_not_reported_as_success(self):
        action = {
            'unit': 'vps-control-protocol-tuic-test.service',
            'action': 'protocol-install:tuic',
            'state': 'running',
            'message': 'Installing',
        }
        with patch.object(api, 'run', side_effect=['inactive', 'unknown']):
            resolved = api.resolve_application_action(action)

        self.assertEqual(resolved['state'], 'failed')
        self.assertEqual(resolved['result'], 'unknown')
        self.assertIn('без подтверждённого результата', resolved['message'])

    def test_all_management_routes_require_authentication(self):
        client = TestClient(api.app)
        count = 0
        for route in api.app.routes:
            if not route.path.startswith('/api/') or route.path in ('/api/health', '/api/auth/status'):
                continue
            path = route.path.replace('{protocol}', 'awg').replace('{image_id}', 'awg').replace('{service_id}', 'ssh').replace('{client_id}', 'missing')
            for method in route.methods:
                with self.subTest(method=method, path=path):
                    self.assertEqual(client.request(method, path, json={}).status_code, 401)
                    count += 1
        self.assertGreater(count, 20)

    def test_ssh_service_without_socket(self):
        for state in ('loaded', 'not-found', ''):
            for action in ('start', 'restart'):
                calls = []
                def run(*args, **kwargs):
                    calls.append(args)
                    return state if 'show' in args else ''
                with patch.object(api, 'run', side_effect=run), patch.object(api, 'service_details', return_value={}):
                    api.manage_service('ssh', api.ServiceAction(action=action))
                units = ('ssh.socket', 'ssh.service') if state == 'loaded' else ('ssh.service',)
                self.assertIn(('systemctl', action, *units), calls)

    def test_metrics_history_endpoint_returns_persisted_server_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            store = api.MetricsHistory(Path(directory) / 'history.sqlite3')
            now = int(time.time() // 3) * 3
            store.record({
                'cpu_percent': 37,
                'memory_total': 100,
                'memory_available': 40,
                'disk_total': 1000,
                'disk_available': 750,
                'network_rx': 10,
                'network_tx': 20,
                'uptime_s': 100,
            }, now)
            api.app.dependency_overrides[api.require_token] = lambda: None
            self.addCleanup(api.app.dependency_overrides.clear)
            with patch.object(api, 'metrics_history_store', store):
                response = TestClient(api.app).get('/api/metrics/history?period=live')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['period'], 'live')
            self.assertEqual(response.json()['resolution_s'], 3)
            self.assertTrue(any(point['cpu_percent'] == 37 for point in response.json()['points']))

    def test_awg_client_create_delete_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'awg.conf'
            config.write_text('[Interface]\nPrivateKey = server-key\n', encoding='utf-8')
            with patch.multiple(api, DATA_DIR=root, CLIENTS_FILE=root / 'clients.json', AWG_CONFIG=config, PUBLIC_IP='192.0.2.1'), \
                 patch.object(api, 'key', return_value='test-key'), \
                 patch.object(api, 'run', return_value='server-public-key'), \
                 patch.object(api, 'run_with_input'):
                settings = api.ClientSettings(
                    dns='9.9.9.9', mtu=1420, keepalive=45, route_mode='all',
                    awg_jc=9, awg_jmin=12, awg_jmax=96,
                )
                created = api.create_client(api.ClientCreate(
                    name='QA client', protocol='awg', settings=settings,
                ))
                self.assertIn('Endpoint = 192.0.2.1:', created['config'])
                self.assertIn('DNS = 9.9.9.9', created['config'])
                self.assertIn('MTU = 1420', created['config'])
                self.assertIn('AllowedIPs = 0.0.0.0/0, ::/0', created['config'])
                self.assertIn('PersistentKeepalive = 45', created['config'])
                self.assertIn('Jc = 9', created['config'])
                self.assertIn('Jmin = 12', created['config'])
                self.assertIn('Jmax = 96', created['config'])
                self.assertEqual(created['profile']['protocol'], 'awg')
                self.assertEqual(created['profile']['name'], 'QA client')
                self.assertEqual(created['profile']['delivery']['file']['content'], created['config'])
                self.assertEqual(created['profile']['delivery']['qr']['content'], created['config'])
                self.assertIsNone(created['profile']['delivery']['link'])
                self.assertTrue(created['profile']['one_time'])
                clients = api.read_clients()
                self.assertEqual(len(clients), 1)
                self.assertIn(ipaddress.ip_interface(clients[0]['address']).ip, api.AWG_SUBNET)
                self.assertIn(created['id'], config.read_text())
                api.delete_client(created['id'])
                self.assertEqual(api.read_clients(), [])
                self.assertNotIn('[Peer]', config.read_text())

    def test_awg_signature_is_client_only_and_invalid_input_has_no_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'awg.conf'
            config.write_text('[Interface]\nPrivateKey = server-key\n', encoding='utf-8')
            with patch.multiple(api, DATA_DIR=root, CLIENTS_FILE=root / 'clients.json', AWG_CONFIG=config), patch.object(api, 'key', return_value='test-key') as keys, patch.object(api, 'run', return_value='server-public-key'), patch.object(api, 'run_with_input'):
                for settings in ({'awg_signature': 'invalid'}, {'awg_signature': 'quic', 'awg_signature_domain': 'https://bad.example'}):
                    with self.assertRaises(api.HTTPException) as error:
                        api.create_client(api.ClientCreate(name='QA invalid', protocol='awg', settings=api.ClientSettings(**settings)))
                    self.assertEqual(error.exception.status_code, 422)
                keys.assert_not_called()
                self.assertEqual(api.read_clients(), [])
                self.assertNotIn('[Peer]', config.read_text())
                created = api.create_client(api.ClientCreate(name='QA decoy', protocol='awg', settings=api.ClientSettings(awg_signature='stun')))
                self.assertIn('I1 = <b 0x000100002112a442><r 12>', created['config'])
                self.assertNotIn('I1 =', config.read_text())
                for name in ('S1', 'S2', 'H1', 'H2', 'H3', 'H4'):
                    self.assertIn(f'{name} = {api.AWG_PROFILE[name]}', created['config'])
                self.assertEqual(api.read_clients()[0]['awg_signature'], 'stun')
                api.delete_client(created['id'])

    def test_hysteria_client_profile_applies_individual_advanced_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protocol = root / 'hysteria2'
            protocol.mkdir()
            settings_path = protocol / 'settings.json'
            users_path = protocol / 'users.json'
            certificate = protocol / 'server.crt'
            settings_path.write_text('{"port":8443,"listen":"8443-8450","domain":"vpn.example","obfs":"gecko","obfs_password":"server-obfs","gecko_min_packet_size":512,"gecko_max_packet_size":1200}', encoding='utf-8')
            users_path.write_text('{}', encoding='utf-8')
            certificate.write_text('certificate', encoding='utf-8')

            def command(*args, **_kwargs):
                if args[:2] == ('systemctl', 'is-enabled'):
                    return 'enabled'
                if args and args[0] == 'openssl':
                    return 'SHA256 Fingerprint=AA:BB'
                return ''

            client_settings = api.ClientSettings(
                local_socks_port=1180, local_http_port=8180, http_proxy_enabled=True,
                proxy_bind='lan', local_auth_enabled=True, local_username='local-user', local_password='local-password',
                disable_udp=True, fast_open=True, lazy=True,
                hysteria_congestion='bbr', bbr_profile='conservative', up_mbps=25, down_mbps=75,
                disable_loss_compensation=True, hysteria_hop_min=20, hysteria_hop_max=50,
            )
            with patch.multiple(
                api,
                DATA_DIR=root, CLIENTS_FILE=root / 'clients.json', PUBLIC_IP='192.0.2.1',
                HYSTERIA2_DIR=protocol, HYSTERIA2_SETTINGS=settings_path, HYSTERIA2_USERS=users_path,
            ), patch.object(api, 'run', side_effect=command), patch.object(api, 'certificate_server_name', return_value='vpn.example'):
                created = api.create_client(api.ClientCreate(name='Advanced client', protocol='hysteria2', settings=client_settings))

            config = created['config']
            parsed = yaml.safe_load(config)
            self.assertIn('listen: 0.0.0.0:1180', config)
            self.assertIn('listen: 0.0.0.0:8180', config)
            self.assertIn('username: "local-user"', config)
            self.assertIn('password: "local-password"', config)
            self.assertIn('disableUDP: true', config)
            self.assertIn('fastOpen: true', config)
            self.assertIn('lazy: true', config)
            self.assertIn('bbrProfile: conservative', config)
            self.assertIn('up: 25 mbps', config)
            self.assertIn('down: 75 mbps', config)
            self.assertIn('disableLossCompensation: true', config)
            self.assertIn('server: vpn.example:8443-8450', config)
            self.assertEqual(parsed['congestion']['bbrProfile'], 'conservative')
            self.assertEqual(parsed['obfs']['type'], 'gecko')
            self.assertEqual(parsed['obfs']['gecko']['password'], 'server-obfs')
            self.assertEqual(parsed['transport']['udp']['minHopInterval'], '20s')
            self.assertIn('obfs=gecko', created['profile']['delivery']['link']['uri'])
            self.assertTrue(parsed['bandwidth']['disableLossCompensation'])
            self.assertTrue(any(field['label'] == 'Локальный пароль' for field in created['profile']['fields']))

    def test_tuic_client_profile_applies_quic_and_local_proxy_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protocol = root / 'tuic'
            protocol.mkdir()
            config_path = protocol / 'config.json'
            settings_path = protocol / 'settings.json'
            config_path.write_text(json.dumps({'inbounds': [{'type': 'tuic', 'users': []}]}), encoding='utf-8')
            settings_path.write_text('{"port":8444}', encoding='utf-8')
            (protocol / 'server.crt').write_text('certificate', encoding='utf-8')
            valid = type('Result', (), {'returncode': 0, 'stderr': ''})()

            def command(*args, **_kwargs):
                return 'enabled' if args[:2] == ('systemctl', 'is-enabled') else ''

            settings = api.ClientSettings(
                local_socks_port=2180, local_auth_enabled=True,
                local_username='tuic-user', local_password='tuic-password',
                congestion_control='cubic', heartbeat='30s', udp_relay_mode='quic', network='tcp',
                tcp_fast_open=True, udp_fragment=True, udp_timeout='3m',
                initial_packet_size=1300, disable_path_mtu_discovery=True,
            )
            with patch.multiple(
                api,
                DATA_DIR=root, CLIENTS_FILE=root / 'clients.json', PUBLIC_IP='192.0.2.1',
                TUIC_DIR=protocol, TUIC_CONFIG=config_path, TUIC_SETTINGS=settings_path,
            ), patch.object(api, 'run', side_effect=command), patch.object(api.subprocess, 'run', return_value=valid), \
                 patch.object(api, 'certificate_server_name', return_value='endpoint.internal'):
                created = api.create_client(api.ClientCreate(name='TUIC client', protocol='tuic', settings=settings))

            client = json.loads(created['config'])
            inbound = client['inbounds'][0]
            outbound = client['outbounds'][0]
            self.assertEqual(inbound['users'][0], {'username': 'tuic-user', 'password': 'tuic-password'})
            self.assertTrue(inbound['tcp_fast_open'])
            self.assertTrue(inbound['udp_fragment'])
            self.assertEqual(inbound['udp_timeout'], '3m')
            self.assertEqual(outbound['udp_relay_mode'], 'quic')
            self.assertEqual(outbound['network'], 'tcp')
            self.assertEqual(outbound['initial_packet_size'], 1300)
            self.assertTrue(outbound['disable_path_mtu_discovery'])

    def test_xray_client_profile_applies_dns_auth_and_filtering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protocol = root / 'xray'
            protocol.mkdir()
            config_path = protocol / 'config.json'
            settings_path = protocol / 'settings.json'
            config_path.write_text(json.dumps({'inbounds': [{'protocol': 'vless', 'settings': {'clients': []}, 'streamSettings': {'realitySettings': {'serverNames': ['example.com', 'cdn.example.com']}}}]}), encoding='utf-8')
            settings_path.write_text(json.dumps({'port': 8445, 'path': '/xhttp', 'server_name': 'example.com', 'password': 'public-key', 'short_id': '0123456789abcdef'}), encoding='utf-8')
            valid = type('Result', (), {'returncode': 0, 'stderr': ''})()

            def command(*args, **_kwargs):
                return 'enabled' if args[:2] == ('systemctl', 'is-enabled') else ''

            settings = api.ClientSettings(
                local_auth_enabled=True, local_username='xray-user', local_password='xray-password',
                xray_dns='1.1.1.1, 8.8.8.8', block_bittorrent=True,
                sniffing=True, route_only=True, routing_domain_strategy='IPIfNonMatch',
                xray_sni='cdn.example.com', xray_xhttp_mode='packet-up', fingerprint='edge', mux_enabled=True, mux_concurrency=12,
                xudp_concurrency=24, xudp_proxy_udp443='skip',
            )
            with patch.multiple(
                api,
                DATA_DIR=root, CLIENTS_FILE=root / 'clients.json', PUBLIC_IP='192.0.2.1',
                XRAY_DIR=protocol, XRAY_CONFIG=config_path, XRAY_SETTINGS=settings_path,
            ), patch.object(api.Path, 'exists', return_value=True), patch.object(api, 'run', side_effect=command), \
                 patch.object(api.subprocess, 'run', return_value=valid):
                created = api.create_client(api.ClientCreate(name='Xray client', protocol='xray', settings=settings))

            client = json.loads(created['config'])
            self.assertEqual(client['dns']['servers'], ['1.1.1.1', '8.8.8.8'])
            self.assertEqual(client['inbounds'][0]['settings']['auth'], 'password')
            self.assertEqual(client['inbounds'][0]['settings']['users'][0]['user'], 'xray-user')
            self.assertTrue(client['inbounds'][0]['sniffing']['routeOnly'])
            self.assertEqual(client['routing']['domainStrategy'], 'IPIfNonMatch')
            self.assertEqual(client['routing']['rules'][0]['protocol'], ['bittorrent'])
            self.assertEqual(client['outbounds'][1]['protocol'], 'blackhole')
            target = client['outbounds'][0]['settings']['vnext'][0]
            self.assertEqual(target['address'], '192.0.2.1')
            self.assertEqual(target['users'][0]['id'], created['profile']['fields'][0]['value'])
            self.assertEqual(client['outbounds'][0]['streamSettings']['xhttpSettings']['mode'], 'packet-up')
            self.assertEqual(client['outbounds'][0]['streamSettings']['realitySettings']['fingerprint'], 'edge')
            self.assertEqual(client['outbounds'][0]['streamSettings']['realitySettings']['password'], 'public-key')
            self.assertEqual(client['outbounds'][0]['streamSettings']['realitySettings']['serverName'], 'cdn.example.com')
            self.assertEqual(client['outbounds'][0]['mux'], {'enabled': True, 'concurrency': 12, 'xudpConcurrency': 24, 'xudpProxyUDP443': 'skip'})
            self.assertIn('sni=cdn.example.com', created['profile']['delivery']['link']['uri'])
            self.assertIn('mode=packet-up', created['profile']['delivery']['link']['uri'])

    def test_connection_options_expose_server_bound_awg_and_xray_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / 'config.json'
            settings_path = root / 'settings.json'
            config_path.write_text(json.dumps({'inbounds': [{'protocol': 'vless', 'streamSettings': {'realitySettings': {'serverNames': ['one.example', 'two.example']}}}]}), encoding='utf-8')
            settings_path.write_text(json.dumps({'server_name': 'one.example'}), encoding='utf-8')
            with patch.multiple(api, XRAY_CONFIG=config_path, XRAY_SETTINGS=settings_path):
                options = api.client_options(None)

            self.assertEqual(options['xray']['server_names'], ['one.example', 'two.example'])
            self.assertEqual(options['xray']['default_sni'], 'one.example')
            self.assertEqual(options['awg']['s1'], int(api.AWG_PROFILE['S1']))
            self.assertEqual(options['awg']['h4'], int(api.AWG_PROFILE['H4']))

    def test_xray_custom_sni_creates_an_isolated_reality_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protocol = root / 'xray'
            protocol.mkdir()
            config_path = protocol / 'config.json'
            settings_path = protocol / 'settings.json'
            config_path.write_text(json.dumps({
                'inbounds': [{
                    'listen': '0.0.0.0', 'port': 8445, 'protocol': 'vless', 'tag': 'vless-xhttp-reality',
                    'settings': {'clients': [], 'decryption': 'none'},
                    'streamSettings': {'network': 'xhttp', 'security': 'reality', 'xhttpSettings': {'path': '/base', 'mode': 'auto'}, 'realitySettings': {'serverNames': ['example.com']}},
                }],
                'outbounds': [{'protocol': 'freedom', 'tag': 'direct'}],
            }), encoding='utf-8')
            settings_path.write_text(json.dumps({
                'port': 8445, 'path': '/base', 'server_name': 'example.com', 'password': 'base-public',
                'short_id': '0123456789abcdef', 'managed_port_start': 18445, 'profiles': {},
            }), encoding='utf-8')

            commands = []

            def command(*args, **_kwargs):
                commands.append(args)
                return 'enabled' if args[:2] == ('systemctl', 'is-enabled') else ''

            def process(args, **_kwargs):
                if args[1:3] == ['tls', 'ping']:
                    return type('Result', (), {'returncode': 0, 'stdout': 'Handshake succeeded\nTLS Version: TLS 1.3\nHandshake succeeded', 'stderr': ''})()
                if args[1:] == ['x25519']:
                    return type('Result', (), {'returncode': 0, 'stdout': 'PrivateKey: private-key\nPassword (PublicKey): public-key\nHash32: hash\n', 'stderr': ''})()
                return type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

            settings = api.ClientSettings(xray_sni='я.рф')
            with patch.multiple(
                api,
                DATA_DIR=root, CLIENTS_FILE=root / 'clients.json', PUBLIC_IP='192.0.2.1',
                XRAY_DIR=protocol, XRAY_CONFIG=config_path, XRAY_SETTINGS=settings_path,
            ), patch.object(api.Path, 'exists', return_value=True), patch.object(api, 'run', side_effect=command), \
                 patch.object(api.subprocess, 'run', side_effect=process):
                created = api.create_client(api.ClientCreate(name='Russian SNI', protocol='xray', settings=settings))

            server = json.loads(config_path.read_text(encoding='utf-8'))
            saved_settings = json.loads(settings_path.read_text(encoding='utf-8'))
            managed = server['inbounds'][1]
            self.assertEqual(managed['port'], 18445)
            self.assertEqual(managed['streamSettings']['realitySettings']['target'], 'xn--41a.xn--p1ai:443')
            self.assertEqual(managed['streamSettings']['realitySettings']['serverNames'], ['xn--41a.xn--p1ai'])
            self.assertEqual(saved_settings['profiles']['xn--41a.xn--p1ai']['password'], 'public-key')
            self.assertIn('@192.0.2.1:18445?', created['profile']['delivery']['link']['uri'])
            self.assertIn('sni=xn--41a.xn--p1ai', created['profile']['delivery']['link']['uri'])
            self.assertIn('encryption=none', created['profile']['delivery']['link']['uri'])
            self.assertIn('mode=stream-one', created['profile']['delivery']['link']['uri'])
            self.assertIn(('/usr/local/lib/vps-control-xray/firewall.sh', 'add'), commands)

    def test_xray_sni_validation_rejects_non_domain_values(self):
        with self.assertRaises(api.HTTPException) as invalid:
            api.normalized_xray_sni('127.0.0.1')
        self.assertEqual(invalid.exception.status_code, 422)

    def test_awg_address_allocation_ignores_other_protocols_and_uses_existing_awg_peers(self):
        rows = [
            {'protocol': 'xray'},
            {'protocol': 'awg', 'address': '10.73.0.2/32'},
            {'protocol': 'tuic'},
        ]
        with patch.object(api, 'read_clients', return_value=rows), patch.object(api, 'AWG_SUBNET', api.ipaddress.ip_network('10.73.0.0/24')):
            self.assertEqual(str(api.next_address()), '10.73.0.3')

    def test_deleting_last_custom_xray_client_removes_listener_and_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protocol = root / 'xray'
            protocol.mkdir()
            config_path = protocol / 'config.json'
            settings_path = protocol / 'settings.json'
            tag = f'{api.XRAY_MANAGED_TAG_PREFIX}test'
            config_path.write_text(json.dumps({'inbounds': [
                {'protocol': 'vless', 'tag': 'vless-xhttp-reality', 'settings': {'clients': []}},
                {'protocol': 'vless', 'tag': tag, 'settings': {'clients': [{'id': 'uuid', 'email': 'client-id@312.net'}]}},
            ]}), encoding='utf-8')
            settings_path.write_text(json.dumps({
                'port': 8445, 'profiles': {'ya.ru': {'tag': tag, 'port': 8446}},
            }), encoding='utf-8')
            clients_path = root / 'clients.json'
            clients_path.write_text(json.dumps([{
                'id': 'client-id', 'name': 'Client', 'protocol': 'xray', 'public_key': 'uuid',
            }]), encoding='utf-8')
            valid = type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

            commands = []

            def command(*args, **_kwargs):
                commands.append(args)
                return ''

            with patch.multiple(
                api, DATA_DIR=root, CLIENTS_FILE=clients_path, XRAY_DIR=protocol,
                XRAY_CONFIG=config_path, XRAY_SETTINGS=settings_path,
            ), patch.object(api.Path, 'exists', return_value=True), patch.object(api, 'run', side_effect=command), \
                 patch.object(api.subprocess, 'run', return_value=valid):
                api.delete_client('client-id')

            server = json.loads(config_path.read_text(encoding='utf-8'))
            saved_settings = json.loads(settings_path.read_text(encoding='utf-8'))
            self.assertEqual([row['tag'] for row in server['inbounds']], ['vless-xhttp-reality'])
            self.assertEqual(saved_settings['profiles'], {})
            self.assertEqual(json.loads(clients_path.read_text(encoding='utf-8')), [])
            firewall_calls = [call for call in commands if call and call[0] == '/usr/local/lib/vps-control-xray/firewall.sh']
            self.assertEqual(firewall_calls, [
                ('/usr/local/lib/vps-control-xray/firewall.sh', 'delete'),
                ('/usr/local/lib/vps-control-xray/firewall.sh', 'add'),
            ])


if __name__ == '__main__':
    unittest.main()
