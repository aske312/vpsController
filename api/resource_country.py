"""Country lookup using the server identity country quorum policy."""
from __future__ import annotations

import ipaddress
import json
import re
import threading
import time
import urllib.request
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor


# Shared across sessions: optional geography must not exhaust API request workers.
_WORKERS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="resource-country")
_PROVIDER_URLS = (
    "https://ipwho.is/{ip}?fields=success,ip,country_code",
    "https://ip.guide/{ip}",
    "https://ipapi.co/{ip}/json/",
    "https://free.freeipapi.com/api/json/{ip}",
    "https://ipinfo.io/{ip}/json",
)


def public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def country_record(data: object, expected: str) -> str | None:
    """Normalize the same provider fields as refresh_server_identity.

    Unlike own-IP discovery, every remote lookup must match the requested IP.
    """
    if not isinstance(data, dict) or data.get("success") is False or data.get("error"):
        return None
    address = data.get("ip") or data.get("ipAddress") or data.get("ip_address") or data.get("query")
    try:
        if str(ipaddress.ip_address(address)) != expected:
            return None
    except (ValueError, TypeError):
        return None
    location = data.get("location") if isinstance(data.get("location"), dict) else data
    network = data.get("network") if isinstance(data.get("network"), dict) else {}
    autonomous = network.get("autonomous_system") if isinstance(network.get("autonomous_system"), dict) else {}
    raw = location.get("country")
    code = (location.get("country_code") or location.get("countryCode") or location.get("country_code2")
            or data.get("country_code") or data.get("code") or autonomous.get("country")
            or (raw if isinstance(raw, str) and re.fullmatch(r"[A-Za-z]{2}", raw) else ""))
    if not isinstance(code, str):
        return None
    code = code.upper().strip()
    return code if re.fullmatch(r"[A-Z]{2}", code) else None


def country_consensus(records: list[str]) -> str | None:
    # Same strict-majority rule as scripts/vps-control.sh:refresh_server_identity.
    code, votes = Counter(records).most_common(1)[0] if records else (None, 0)
    return code if votes >= max(2, len(records) // 2 + 1) else None


class CountryLookup:
    def __init__(self, *, opener=urllib.request.urlopen, clock=time.monotonic, executor=None):
        self.opener, self.clock = opener, clock
        self.executor = executor if executor is not None else _WORKERS
        self.cache: OrderedDict[str, tuple[float, str | None]] = OrderedDict()
        self.pending: set[str] = set()
        self.lock = threading.Lock()

    def _resolve(self, ip: str) -> None:
        records = []
        code = None
        try:
            for template in _PROVIDER_URLS:
                try:
                    request = urllib.request.Request(template.format(ip=ip), headers={
                        "Accept": "application/json", "User-Agent": "312-net-control/1"})
                    with self.opener(request, timeout=4) as response:
                        raw = response.read(65537)
                    if len(raw) > 65536:
                        continue
                    record = country_record(json.loads(raw), ip)
                    if record:
                        records.append(record)
                except (OSError, ValueError, TypeError):
                    continue
            code = country_consensus(records)
        finally:
            with self.lock:
                self.cache[ip] = (self.clock() + (86400 if code else 300), code)
                self.cache.move_to_end(ip)
                while len(self.cache) > 4096:
                    self.cache.popitem(last=False)
                self.pending.discard(ip)

    def lookup(self, addresses: list[str]) -> dict:
        ips = list(dict.fromkeys(str(ipaddress.ip_address(value)) for value in addresses))
        now = self.clock()
        with self.lock:
            missing = [ip for ip in ips if public_ip(ip)
                       and (ip not in self.cache or self.cache[ip][0] <= now) and ip not in self.pending]
            missing = missing[:max(0, 200 - len(self.pending))]
            self.pending.update(missing)
        for ip in missing:
            try:
                self.executor.submit(self._resolve, ip)
            except RuntimeError:
                with self.lock:
                    self.pending.discard(ip)
        with self.lock:
            now = self.clock()
            items = [{"address": ip, "country_code": self.cache[ip][1]
                      if ip in self.cache and self.cache[ip][0] > now else None} for ip in ips]
            retry = any(public_ip(ip) and (ip in self.pending or ip not in self.cache or self.cache[ip][0] <= now) for ip in ips)
        return {"items": items, "pending": retry}
