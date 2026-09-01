#!/usr/bin/env python3
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

USERS = Path("/etc/vps-control/hysteria2/users.json")

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/auth":
            self.send_error(404)
            return
        try:
            size = min(int(self.headers.get("content-length", "0")), 8192)
            supplied = str(json.loads(self.rfile.read(size)).get("auth", ""))
            username, password = supplied.split(":", 1)
            users = json.loads(USERS.read_text(encoding="utf-8"))
            allowed = isinstance(users.get(username), str) and hmac.compare_digest(users[username], password)
        except (OSError, ValueError, json.JSONDecodeError):
            allowed, username = False, ""
        body = json.dumps({"ok": allowed, "id": username if allowed else ""}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        return

ThreadingHTTPServer(("127.0.0.1", 18081), Handler).serve_forever()
