"""Bounded, private stage journals. Never ingest command output or environment."""
from __future__ import annotations

import fcntl
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import operation_policy

MAX_EVENTS = 256
MAX_BYTES = 128 * 1024
_mutex = threading.Lock()


def location(root: Path, source: str, identity: str) -> Path:
    if source not in {"system", "mihomo", "cdn"} or not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise ValueError("Invalid operation journal identity")
    return root / "operation-logs" / source / f"{identity}.json"


@contextmanager
def locked(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _mutex, (root / "operation-logs.lock").open("a+b") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def public_text(value):
    text = str(value or "")
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*", "[скрыто]", text)
    text = re.sub(r"(?i)\b(?:https?|ss|vless|vmess|trojan|hysteria2|tuic)://[^\s]+", "[адрес скрыт]", text)
    text = re.sub(r"(?i)(password|passwd|token|secret|private[_ -]?key|authorization|пароль)\s*[:=]\s*[^\s,;]+", r"\1=[скрыто]", text)
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [скрыто]", text)
    return "".join(character for character in text if character >= " " or character == "\n")[:1024]


def append(data_dir: Path, value: dict, source: str | None = None):
    """Logging failures must not turn a committed mutation into a failed mutation."""
    from application_operation import atomic_json, read, timestamp
    root = operation_policy.root_for(data_dir)
    source = source or ("mihomo" if data_dir.name == "mihomo" else "system")
    value = {**phase(value), **value}
    if isinstance(value.get('stage_result'), str): value['result'] = value['stage_result']
    if value.get('result') not in ('running', 'passed', 'failed', 'unknown', 'cancelled', 'existing_result', 'skipped'):
        value['result'] = phase(value)['result']
    try:
        path = location(root, source, value["id"])
        with locked(root):
            if path.is_symlink():
                raise ValueError("Journal replaced by symlink")
            document = read(path) or {"id": value["id"], "source": source, "events": [], "dropped": 0, 'schema_version': 2}
            if not isinstance(document.get("events"), list) or any(not isinstance(event, dict) for event in document["events"]) or type(document.get("dropped")) is not int:
                raise ValueError("Invalid stage journal")
            progress = value.get("progress")
            event = {"state": public_text(value.get("state")), "progress": progress if type(progress) is int else None, "message": public_text(value.get("message"))}
            for key in ('stage', 'result', 'component', 'error_code', 'dependency', 'span_id', 'command'):
                if isinstance(value.get(key), str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', value[key]): event[key] = value[key]
            if isinstance(value.get('dependency'), str) and re.fullmatch(r'[a-zA-Z0-9@_.-]{1,160}', value['dependency']): event['dependency'] = value['dependency']
            if type(value.get('exit_code')) is int: event['exit_code'] = value['exit_code']
            if type(value.get('duration_ms')) in (int, float): event['duration_ms'] = max(0, round(value['duration_ms'], 2))
            if isinstance(value.get('health_check'), dict): event['health_check'] = safe_health(value['health_check'])
            if isinstance(value.get('changed_components'), list): event['changed_components'] = [name for name in value['changed_components'][:32] if isinstance(name, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', name)]
            previous = document["events"][-1] if document["events"] else {}
            if all(previous.get(key) == item for key, item in event.items()):
                return
            event["at"] = timestamp()
            document["events"].append(event)
            document["state"] = value.get("state", "unknown")
            document["updated_at"] = event["at"]
            import json
            while len(document["events"]) > MAX_EVENTS or len(json.dumps(document, ensure_ascii=False).encode()) > MAX_BYTES:
                document["events"].pop(0)
                document["dropped"] += 1
            atomic_json(path, document)
            _prune(root)
    except (OSError, ValueError, KeyError, TypeError):
        logging.getLogger(__name__).warning("Operation stage journal unavailable; operation result is preserved")


def _prune(root):
    from application_operation import read
    settings = operation_policy.read(root)
    cutoff = time.time() - settings["log_retention_days"] * 86400
    budget = settings["log_disk_limit_mb"] * 1024 * 1024
    protected = set()
    for recovery in (root / "dns-recovery.json", root / "automation-recovery.json", root / "mihomo/profile-recovery/manifest.json"):
        if recovery.exists():
            protected.add(read(recovery).get("operation_id"))
    for base in (root, root / "mihomo"):
        for marker in (base / "tmp").glob("*/.vps-control-temp.json"):
            protected.add(read(marker).get("operation_id"))
    paths = sorted((root / "operation-logs").glob("*/*.json"), key=lambda path: path.stat().st_mtime)
    total = sum(path.stat().st_size for path in paths if not path.is_symlink())
    for path in paths:
        if path.is_symlink() or path.stem in protected:
            continue
        location(root, path.parent.name, path.stem)
        journal = read(path)
        # The authoritative record can be newer than the last journal event.
        archive = {"system": root / "operations", "cdn": root / "cdn-operations", "mihomo": root / "mihomo" / "operations"}[path.parent.name]
        record = read(archive / path.name)
        state = record.get("state", journal.get("state"))
        if state not in {"succeeded", "done", "failed", "cancelled"}:
            continue
        details = path.stat()
        if details.st_mtime < cutoff or total > budget:
            path.unlink()
            total -= details.st_size


def prune(root):
    with locked(root):
        _prune(root)


def get(root, source, identity):
    from application_operation import read
    path = location(root, source, identity)
    if path.is_symlink():
        raise ValueError("Journal replaced by symlink")
    with locked(root):
        _prune(root)
        document = read(path)
        if not document:
            return {"id": identity, "source": source, "events": [], "available": False, "dropped": 0}
        if document.get("id") != identity or document.get("source") != source or not isinstance(document.get("events"), list) or type(document.get("dropped")) is not int:
            raise ValueError("Invalid stage journal")
        events = []
        for event in document["events"]:
            if not isinstance(event, dict):
                raise ValueError("Invalid stage event")
            events.append({"at": str(event.get("at", ""))[:64], "state": public_text(event.get("state")),
                           "progress": event.get("progress") if type(event.get("progress")) is int else None,
                           "message": public_text(event.get("message")),
                           **{key: event[key] for key in ('stage', 'result', 'component', 'error_code', 'span_id', 'command')
                              if isinstance(event.get(key), str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', event[key])}})
            for key in ('exit_code', 'duration_ms'):
                if type(event.get(key)) in (int, float): events[-1][key] = event[key]
            if isinstance(event.get('dependency'), str) and re.fullmatch(r'[a-zA-Z0-9@_.-]{1,160}', event['dependency']): events[-1]['dependency'] = event['dependency']
            if isinstance(event.get('health_check'), dict): events[-1]['health_check'] = safe_health(event['health_check'])
        return {"id": identity, "source": source, "events": events, "available": True, "dropped": document["dropped"]}


def safe_health(value):
    states = ('passed', 'failed', 'unknown', 'not_checked', 'skipped')
    result = {'state': value.get('state') if value.get('state') in states else 'unknown'}
    checks = []
    for row in value.get('checks', [])[:32]:
        if not isinstance(row, dict): continue
        checks.append({key: row[key] for key in ('component', 'level', 'state', 'observed', 'error_code')
                       if isinstance(row.get(key), str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', row[key])})
    result['checks'] = checks
    return result


def phase(value):
    """Give every existing lifecycle writer structured, non-invented phase semantics."""
    state = value.get('state', 'unknown')
    component = str(value.get('action', '')).split(':', 1)[0]
    component = 'mihomo' if component.startswith(('mihomo', 'profile-', 'module-', 'policy-')) else 'dns' if component.startswith('dns') else 'network' if component.startswith(('network', 'relay')) else 'application'
    return {'component': component, 'stage': 'complete' if state in ('succeeded', 'done', 'failed', 'cancelled') else 'admission' if state == 'queued' else 'progress_' + str(value.get('progress', 0)),
            'result': 'passed' if state in ('succeeded', 'done') else 'failed' if state == 'failed' else 'cancelled' if state == 'cancelled' else 'unknown' if state == 'unknown' else 'running'}


@contextmanager
def command(root, arguments, source='system'):
    """Record called executable classes and unit IDs, never arguments/stdin/output."""
    import diagnostic_context as tracing
    import uuid
    identity = tracing.snapshot().get('operation_id') or os.getenv('VPS_CONTROL_MIHOMO_OPERATION_ID')
    if not tracing.valid(identity):
        yield lambda code: None
        return
    executable = Path(str(arguments[0])).name if arguments else ''
    allowed = {'systemctl', 'journalctl', 'curl', 'caddy', 'getent', 'apt-get', 'dpkg', 'wg', 'awg', 'xray', 'mihomo', 'python', 'python3', 'ip', 'nft', 'ss', 'openssl', 'runuser'}
    category = executable if executable in allowed else 'external_command'
    unit = next((str(arg) for arg in arguments[1:] if re.fullmatch(r'(?:vps-control-[a-zA-Z0-9@_.-]+|caddy|ssh|sshd|fail2ban|systemd-resolved)\.service', str(arg))), None)
    span = uuid.uuid4().hex
    value = {'id': identity, 'state': 'running', 'stage': 'command', 'result': 'running', 'command': category, 'span_id': span,
             'component': 'system', 'message': '', **({'dependency': unit} if unit else {})}
    append(root, value, source)
    started, code = time.monotonic(), None
    def completed(result):
        nonlocal code
        code = result
    try:
        yield completed
    except BaseException as exc:
        import subprocess
        value['error_code'] = 'command_timeout' if isinstance(exc, subprocess.TimeoutExpired) else 'command_failed'
        raise
    finally:
        value.update(result='passed' if code == 0 else 'failed', duration_ms=(time.monotonic() - started) * 1000)
        if type(code) is int: value['exit_code'] = code
        append(root, value, source)


class DiagnosticOperation:
    """Journal a synchronous check without claiming the application's mutation slot."""
    def __init__(self, root, action, component, *, integration_id=None, scope='provider_catalog'):
        import uuid
        import diagnostic_context as tracing
        from application_operation import timestamp
        self.root = root
        self.scope = scope
        self.stored = False
        self.value = {'id': uuid.uuid4().hex, 'action': action, 'component': component,
                      'state': 'running', 'started_at': timestamp(),
                      **{key: value for key, value in tracing.snapshot().items() if key in ('trace_id', 'parent_request_id') and tracing.valid(value)}}
        if isinstance(integration_id, str) and re.fullmatch(r'ai:[0-9a-f]{32}', integration_id): self.value['integration_id'] = integration_id
        request = tracing.snapshot().get('request_id')
        if tracing.valid(request): self.value['parent_request_id'] = request
        tracing.operation(self.value['id'])
        self.stage('start', 'running')

    @property
    def identity(self): return self.value['id']

    def stage(self, stage, result, *, error_code=None):
        from application_operation import atomic_json, timestamp, prune
        self.value.update(stage=stage, result=result, updated_at=timestamp())
        if error_code:
            import diagnostic_context as tracing
            tracing.failure(error_code)
            self.value['error_code'] = error_code
        if stage == 'complete':
            self.value.update(state='succeeded' if result == 'passed' else 'failed', finished_at=timestamp())
            self.value['health_check'] = {'scope': self.scope, 'state': result,
                'application_function': 'not_checked', 'changed_components': []}
        try:
            atomic_json(self.root / 'operations' / (self.identity + '.json'), self.value)
            self.stored = True
            append(self.root, self.value, 'system')
            if stage == 'complete': prune(self.root, self.identity)
        except (OSError, ValueError):
            self.stored = False
            logging.getLogger(__name__).warning('Diagnostic operation evidence unavailable')


if __name__ == '__main__':
    import sys
    import diagnostic_context
    if len(sys.argv) == 8 and sys.argv[1] == 'shell':
        _, _, root, category, phase_name, span, status, unit = sys.argv
        identity = diagnostic_context.snapshot().get('operation_id')
        if diagnostic_context.valid(identity) and diagnostic_context.valid(span) and (Path(root) / 'operations' / (identity + '.json')).is_file() and category in ('systemctl', 'curl') and phase_name in ('start', 'finish'):
            value = {'id': identity, 'state': 'running', 'stage': 'command', 'command': category, 'span_id': span,
                     'component': 'system', 'result': 'running' if phase_name == 'start' else 'passed' if status == '0' else 'failed'}
            if status.isdecimal() and phase_name == 'finish': value['exit_code'] = int(status)
            if re.fullmatch(r'[a-zA-Z0-9@_.-]{1,160}', unit): value['dependency'] = unit
            append(Path(root), value)
