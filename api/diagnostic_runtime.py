"""Standard-library-only local HTTP contracts, also used by shell workers."""
import http.client
import time
import socket
import threading
from contextlib import contextmanager


@contextmanager
def read_budget(stream, seconds=6):
    def close():
        if stream is not None:
            try: stream.shutdown(socket.SHUT_RDWR)
            except OSError: pass
    timer = threading.Timer(seconds, close)
    timer.daemon = True
    timer.start()
    try: yield
    finally: timer.cancel()


def local_http(port, path, *, json_response=False):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    started = time.monotonic()
    try:
        connection.request('GET', path, headers={'Accept': 'application/json' if json_response else 'text/html'})
        with read_budget(connection.sock):
            response = connection.getresponse()
            body = response.read(65537)
        valid = response.status == 200
        if json_response:
            import json
            value = json.loads(body)
            valid = valid and len(body) <= 65536 and isinstance(value, dict) and (value.get('ok') is True or value.get('status') in ('ok', 'healthy'))
        else: valid = valid and b'<html' in body.lower()
        return {'state': 'passed' if valid else 'failed', 'status': response.status,
                'duration_ms': round((time.monotonic() - started) * 1000, 2),
                'scope': 'fixed_loopback_HTTP_health_contract' if json_response else 'fixed_loopback_HTML_delivery'}
    except (OSError, ValueError, http.client.HTTPException): return {'state': 'failed', 'error_code': 'local_HTTP_contract_failed'}
    finally: connection.close()

