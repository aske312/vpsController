"""Exercise real SDK stdio handshakes and tool calls against an isolated API."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import hmac
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import threading

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


class Endpoint(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        assert self.path == '/api/assistant/control-tool'
        assert self.headers['X-MCP-Actor'] == 'panel'
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        module = 'mihomo-mcp' if body['name'].startswith('mihomo_') else 'vps-controller-mcp'
        assert self.headers['X-MCP-Module'] == module
        expected = hmac.new(b'isolated-test-token', f'nodera-mcp-v1:{module}:panel'.encode(), hashlib.sha256).hexdigest()
        assert self.headers['Authorization'] == 'Bearer ' + expected
        self.seen.append(body)
        value = {'server': {'name': 'isolated-fixture'}, 'resources': {'cpu_percent': 12}}
        encoded = json.dumps(value).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers(); self.wfile.write(encoded)

    def log_message(self, *_): pass


async def main():
    with tempfile.TemporaryDirectory(prefix='nodera-sdk-test-') as folder:
        folder = Path(folder)
        settings = {'modules': {'ai-agent': True, 'nodera-bot': False},
                    'mcp': {'telegram-mcp': False, 'vps-controller-mcp': True, 'mihomo-mcp': True},
                    'control_token': 'isolated-test-token', 'accounts': []}
        config = folder / 'settings.json'
        config.write_text(json.dumps(settings))
        server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        launcher = folder / 'launch.py'
        launcher.write_text('import sys,json\nfrom pathlib import Path\n'
                           f'sys.path.insert(0,{str(ROOT / "protocol-images/ai-agent")!r})\n'
                           'import runner\n'
                           f'runner.VPS_URL="http://127.0.0.1:{server.server_port}/api/assistant/control-tool"\n'
                           f'settings=json.loads(Path({str(config)!r}).read_text())\n'
                           'runner.serve_mcp(runner.scoped_mcp_settings(settings,"panel",sys.argv[1]),"panel",sys.argv[1])\n')
        try:
            params = StdioServerParameters(command=sys.executable, args=[str(launcher), 'vps-controller-mcp'])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {tool.name for tool in (await session.list_tools()).tools}
                    assert names == {'vps_overview', 'vps_services', 'vps_components', 'vps_metrics', 'vps_panel', 'vps_service_action', 'vps_operation', 'vps_api', 'vps_logs', 'vps_network', 'vps_security', 'vps_diagnostics', 'vps_access', 'vps_observability', 'vps_changes', 'vps_incident', 'vps_probe', 'application_diagnostics', 'operation_timeline', 'integration_probe', 'diagnostic_baseline', 'diagnostic_alerts', 'diagnostic_alert_rules', 'component_check', 'vps_terminal'}, names
                    vps_names = names
                    assert (await session.call_tool('mihomo_api', {'method': 'GET', 'path': '/profiles'})).isError
                    result = await session.call_tool('vps_overview', {})
                    assert not result.isError, result
                    assert 'isolated-fixture' in '\n'.join(part.text for part in result.content)
                    observed = await session.call_tool('vps_observability', {'path': '/events', 'body': {'minutes': 15, 'errors': True}})
                    assert not observed.isError, observed
                    for name, arguments in [('vps_changes', {'minutes': 15}), ('vps_incident', {'include_connections': False}),
                                            ('application_diagnostics', {}), ('operation_timeline', {'operation_id': 'a' * 32}), ('diagnostic_baseline', {}), ('diagnostic_alerts', {}), ('diagnostic_alert_rules', {}), ('component_check', {'component': 'api'}), ('vps_terminal', {'command': 'help'}),
                                            ('integration_probe', {'integration_id': 'ai:' + 'b' * 32, 'mode': 'tls'}),
                                            ('vps_probe', {'kind': 'service', 'service': 'api'})]:
                        result = await session.call_tool(name, arguments)
                        assert not result.isError, result
                    rejected = await session.call_tool('shell', {'cmd': 'whoami'})
                    assert rejected.isError
            assert Endpoint.seen[:2] == [{'name': 'vps_overview'}, {'name': 'vps_observability', 'method': 'GET', 'path': '/events', 'body': {'minutes': 15, 'errors': True}}]
            assert [item['name'] for item in Endpoint.seen[2:]] == ['vps_changes', 'vps_incident', 'application_diagnostics', 'operation_timeline', 'diagnostic_baseline', 'diagnostic_alerts', 'diagnostic_alert_rules', 'component_check', 'vps_terminal', 'integration_probe', 'vps_probe']
            assert Endpoint.seen[-1]['body']['service'] == 'api'
            print('stdio SDK handshake, scoped HTTP tool call and unknown-tool rejection: passed')
            settings['mcp']['vps-controller-mcp'] = False
            config.write_text(json.dumps(settings))
            params = StdioServerParameters(command=sys.executable, args=[str(launcher), 'mihomo-mcp'])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {tool.name for tool in (await session.list_tools()).tools}
                    assert names == {'mihomo_user', 'mihomo_capabilities', 'mihomo_api', 'mihomo_compare', 'mihomo_operation'}, names
                    assert not names & vps_names
                    for name, arguments in [('mihomo_capabilities', {}), ('mihomo_api', {'method': 'GET', 'path': '/profiles'}),
                                            ('mihomo_compare', {'left_profile_id': 'one', 'right_profile_id': 'two'}),
                                            ('mihomo_operation', {'operation_id': 'a' * 32})]:
                        result = await session.call_tool(name, arguments)
                        assert not result.isError, result
                    assert (await session.call_tool('vps_overview', {})).isError
            print('independent Mihomo stdio scope, disjoint tool catalog and scoped credential calls: passed')
            # The Telegram server also negotiates over stdio without opening a
            # Telegram connection until a tool is actually invoked.
            settings['mcp']['telegram-mcp'] = True
            settings.update(telegram_api_id=123, telegram_api_hash='a' * 32)
            settings['accounts'] = [{'telegram_id': 11, 'telegram_access': True, 'session': 'not-used'}]
            config.write_text(json.dumps(settings))
            params = StdioServerParameters(command=sys.executable,
                args=[str(ROOT / 'protocol-images/ai-agent/runner.py'), '--mcp', 'telegram-mcp'],
                env={'NODERA_CONFIG': str(config), 'NODERA_ACTOR': '11'})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {tool.name for tool in (await session.list_tools()).tools}
                    assert names == {'tg_auth_status', 'tg_get_my_dialogs', 'tg_get_messages', 'tg_search_messages', 'tg_get_stats'}, names
                    assert (await session.call_tool('tg_send_message', {'group': '42', 'text': 'blocked'})).isError
            print('Telegram stdio tool discovery (no account login or write): passed')
            catalog = json.loads((ROOT / 'protocol-images/ai-agent/telegram_catalog.json').read_text(encoding='utf-8'))
            settings['accounts'][0].update(telegram_tools=[row['name'] for row in catalog], telegram_action_targets=['42'])
            config.write_text(json.dumps(settings))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    assert {tool.name for tool in tools} == {row['name'] for row in catalog}
                    assert len(tools) == 54
                    send = next(tool for tool in tools if tool.name == 'tg_send_message')
                    assert send.inputSchema['properties']['dry_run']['default'] is True
                    result = await session.call_tool('tg_list_sessions', {})
                    assert not result.isError
                    assert '11' in '\n'.join(part.text for part in result.content)
                    assert (await session.call_tool('tg_use_session', {'session_name': '22'})).isError
            print('all 54 opted-in Telegram tools, safe schemas and session isolation: passed')
            settings['accounts'][0]['bot_chat_access'] = True
            config.write_text(json.dumps(settings))
            params = StdioServerParameters(command=sys.executable,
                args=[str(ROOT / 'protocol-images/ai-agent/runner.py'), '--mcp', 'web'],
                env={'NODERA_CONFIG': str(config), 'NODERA_ACTOR': '11'})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {tool.name for tool in (await session.list_tools()).tools}
                    assert names == {'web_search', 'web_read'}, names
                    assert (await session.call_tool('web_read', {'url': 'https://localhost/'})).isError
            print('Web stdio discovery and private target rejection: passed')
        finally:
            server.shutdown(); server.server_close(); thread.join()


asyncio.run(asyncio.wait_for(main(), timeout=40))
subprocess.run([sys.executable, '-X', 'utf8', '-m', 'unittest', 'tests.api.test_telegram_guarded', '-q'], cwd=ROOT, check=True)
