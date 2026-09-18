"""Shared Direct protocol readiness and recent traffic; no client-side inference."""
from __future__ import annotations

import threading
import time


class TrafficSampler:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.samples = {}
        self.lock = threading.Lock()

    def sample(self, protocol: str, rx, tx) -> dict:
        now = self.clock()
        with self.lock:
            previous = self.samples.get(protocol)
            if previous and now - previous[0] < 3:
                return dict(previous[3])
            result = {"rx_bps": 0.0, "tx_bps": 0.0, "available": False}
            if rx is not None and tx is not None:
                if previous and 0 < now - previous[0] <= 30 and rx >= previous[1] and tx >= previous[2]:
                    seconds = now - previous[0]
                    result = {"rx_bps": (rx - previous[1]) / seconds, "tx_bps": (tx - previous[2]) / seconds, "available": True}
                self.samples[protocol] = (now, rx, tx, result)
            else:
                self.samples.pop(protocol, None)
            return dict(result)


def protocol_health(runtime: dict, diagnostics: dict, traffic: dict, clients: int, *, stale=False, checking=False) -> dict:
    checks = diagnostics.get("checks", [])
    findings = diagnostics.get("findings", [])
    critical_checks = {"protocol_service", "service", "listener", "udp", "forwarding", "route", "configuration", "reality_target"}
    failed = [check for check in checks if check.get("ok") is False]
    checked = bool(diagnostics.get("checked_at") and checks and diagnostics.get("status") != "pending")
    runtime_ready = bool(runtime.get("service_active", runtime.get("active", False)))
    error = not runtime_ready or diagnostics.get("status") == "critical" or any(f.get("severity") == "critical" for f in findings) or any(c.get("severity") == "critical" or c.get("id") in critical_checks for c in failed)
    warning = not checked or stale or bool(failed) or diagnostics.get("status") == "warning" or any(f.get("severity") == "warning" for f in findings)
    base = "ERROR" if error else "WARN" if warning else "READY"
    traffic_now = clients > 0 and traffic.get("available", False) and traffic.get("rx_bps", 0) + traffic.get("tx_bps", 0) > 0
    state = ("WORKS" if base == "READY" else "WARN") if traffic_now else base
    reason = "Проверки пройдены"
    if not runtime_ready:
        reason = "Служба протокола не запущена"
    elif not checked:
        reason = "Диагностика выполняется" if checking else "Результат диагностики пока недоступен"
    elif stale:
        reason = "Обновляем устаревшую диагностику"
    elif findings:
        reason = "; ".join(str(f.get("title", "")) for f in findings)
    elif failed:
        reason = "; ".join(str(c.get("name", "")) for c in failed)
    if traffic_now:
        reason = ("Есть клиенты и текущий трафик; " + reason[0].lower() + reason[1:])
    return {"state": state, "base_state": base, "reason": reason, "checked_at": diagnostics.get("checked_at"), "checking": checking, "clients": clients, "traffic": traffic, "traffic_now": bool(traffic_now)}
