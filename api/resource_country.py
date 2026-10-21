"""Best-effort country lookup for public IPs; independent of route readiness."""
from __future__ import annotations

import ipaddress
import json
import re
import threading
import time
import urllib.request
from collections import OrderedDict


def public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


class CountryLookup:
    def __init__(self, *, opener=urllib.request.urlopen, clock=time.monotonic):
        self.opener, self.clock = opener, clock
        self.cache: OrderedDict[str, tuple[float, str | None]] = OrderedDict()
        self.pending: set[str] = set()
        self.lock = threading.Lock()

    def lookup(self, addresses: list[str]) -> dict:
        ips = list(dict.fromkeys(str(ipaddress.ip_address(value)) for value in addresses))
        now = self.clock()
        with self.lock:
            missing = [ip for ip in ips if public_ip(ip)
                       and (ip not in self.cache or self.cache[ip][0] <= now) and ip not in self.pending]
            # Bound concurrent batches across panel sessions; the caller can retry.
            if len(self.pending) + len(missing) > 200:
                missing = []
            self.pending.update(missing)
        if missing:
            found: dict[str, str] = {}
            try:
                request = urllib.request.Request('https://api.country.is/',
                    data=json.dumps(missing).encode(), method='POST',
                    headers={'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': '312-net-control/1'})
                with self.opener(request, timeout=4) as response:
                    raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError('Oversized country response')
                payload = json.loads(raw)
                if not isinstance(payload, list):
                    raise ValueError('Invalid country response')
                seen = set()
                for row in payload:
                    if not isinstance(row, dict):
                        raise ValueError('Invalid country row')
                    address = str(ipaddress.ip_address(row.get('ip', '')))
                    code = row.get('country')
                    if address not in missing or address in seen:
                        raise ValueError('Unexpected country address')
                    seen.add(address)
                    if code is not None:
                        if not isinstance(code, str) or not re.fullmatch('[A-Z]{2}', code):
                            raise ValueError('Invalid country code')
                        found[address] = code
            except (OSError, ValueError, TypeError):
                found = {}
            finally:
                with self.lock:
                    for ip in missing:
                        self.cache[ip] = (self.clock() + (86400 if ip in found else 300), found.get(ip))
                        self.cache.move_to_end(ip)
                    while len(self.cache) > 4096:
                        self.cache.popitem(last=False)
                    self.pending.difference_update(missing)
        with self.lock:
            items = [{'address': ip, 'country_code': self.cache[ip][1] if ip in self.cache and self.cache[ip][0] > self.clock() else None} for ip in ips]
            retry = any(public_ip(ip) and (ip in self.pending or ip not in self.cache) for ip in ips)
        return {'items': items, 'pending': retry}
