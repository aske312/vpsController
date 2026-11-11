"""Exercise real SDK stdio handshakes and tool calls against an isolated API."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
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
        assert self.headers['Authorization'] == 'Bearer isolated-test-token'
        assert self.headers['X-MCP-Actor'] == 'panel'
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
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
                    'mcp': {'telegram-mcp': False, 'vps-controller-mcp': True},
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
                           f'runner.serve_mcp(json.loads(Path({str(config)!r}).read_text()),"panel","vps-controller-mcp")\n')
        try:
            params = StdioServerParameters(command=sys.executable, args=[str(launcher)])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {tool.name for tool in (await session.list_tools()).tools}
                    assert names == {'vps_overview', 'vps_services', 'vps_components', 'vps_metrics', 'vps_panel'}, names
                    result = await session.call_tool('vps_overview', {})
                    assert not result.isError, result
                    assert 'isolated-fixture' in '\n'.join(part.text for part in result.content)
                    rejected = await session.call_tool('shell', {'cmd': 'whoami'})
                    assert rejected.isError
            assert Endpoint.seen == [{'name': 'vps_overview'}]
            print('stdio SDK handshake, scoped HTTP tool call and unknown-tool rejection: passed')
            # The Telegram server also negotiates over stdio without opening a
            # Telegram connection until a tool is actually invoked.
            settings['mcp']['telegram-mcp'] = True
            settings['accounts'] = [{'telegram_id': 11, 'telegram_access': True, 'session': 'not-used'}]
            config.write_text(json.dumps(settings))
            params = StdioServerParameters(command=sys.executable,
                args=[str(ROOT / 'protocol-images/ai-agent/runner.py'), '--mcp', 'telegram-mcp'],
                env={'NODERA_CONFIG': str(config), 'NODERA_ACTOR': '11'})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {tool.name for tool in (await session.list_tools()).tools}
                    assert names == {'tg_auth_status', 'tg_dialogs', 'tg_history', 'tg_search'}, names
            print('Telegram stdio tool discovery (no account login or write): passed')
        finally:
            server.shutdown(); server.server_close(); thread.join()


asyncio.run(asyncio.wait_for(main(), timeout=40))
