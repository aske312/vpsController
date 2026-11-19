"""Bounded, redacted journald excerpts stored alongside failed operation stages."""
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from types import SimpleNamespace

from operation_log import public_text


def needs_capture(value):
    if not isinstance(value, dict): return True
    if value.get('available') or value.get('reason') not in ('capture_unavailable', 'journal_empty_or_rotated'): return False
    try:
        captured = datetime.fromisoformat(value['captured_at'])
        return (datetime.now(timezone.utc) - captured).total_seconds() >= 60
    except (KeyError, ValueError, TypeError):
        return False


def capture(root, record):
    from application_operation import timestamp
    evidence = {'captured_at': timestamp(), 'available': False, 'lines': [],
                'scope': 'operation_unit_at_capture; trailing_or_rotated_logs_may_be_missing'}
    unit = record.get('unit') or record.get('origin_unit')
    if not isinstance(unit, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9@_.-]{0,150}\.service', unit):
        return {**evidence, 'reason': 'operation_unit_not_recorded'}
    try:
        started = datetime.fromisoformat(record['started_at'].replace('Z', '+00:00'))
        if started.tzinfo is None: raise ValueError('Missing timezone')
        finished = datetime.fromisoformat((record.get('finished_at') or evidence['captured_at']).replace('Z', '+00:00'))
        if finished.tzinfo is None or finished < started: raise ValueError('Invalid interval')
        import assistant_control
        settings = assistant_control._read(SimpleNamespace(DATA_DIR=root))
        secrets = []
        def collect(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if re.search(r'(?i)password|passwd|token|secret|private.?key|api.?key|session|credential', str(key)) and isinstance(item, str) and item:
                        secrets.append(item)
                        if '\n' in item: secrets.extend(part for part in item.splitlines() if part)
                    elif isinstance(item, (dict, list)): collect(item)
            elif isinstance(value, list):
                for item in value: collect(item)
        collect(settings)
        collect(dict(os.environ))
        args = ['journalctl', '--no-pager', '-o', 'json', '-n', '60', '-u', unit,
                '--since', started.astimezone(timezone.utc).isoformat(), '--until', finished.astimezone(timezone.utc).isoformat(),
                '--output-fields=__REALTIME_TIMESTAMP,MESSAGE,PRIORITY']
        invocation = record.get('systemd_invocation_id')
        if isinstance(invocation, str) and re.fullmatch('[0-9a-f]{32}', invocation):
            args.append('_SYSTEMD_INVOCATION_ID=' + invocation)
        result = subprocess.run(args, capture_output=True, text=True, timeout=4, check=True)
        budget, lines = 16000, []
        raw = result.stdout.splitlines()
        for line in raw:
            row = json.loads(line)
            message = row.get('MESSAGE')
            if not isinstance(message, str): continue
            for secret in sorted(secrets, key=len, reverse=True): message = message.replace(secret, '[redacted]')
            message = public_text(message)
            message = re.sub(r'[A-Za-z0-9+/=_-]{64,}', '[opaque-redacted]', message)
            size = len(message.encode('utf-8'))
            if size > budget: break
            budget -= size
            lines.append({'at': datetime.fromtimestamp(int(row['__REALTIME_TIMESTAMP']) / 1000000, timezone.utc).isoformat(), 'message': message,
                          'priority': str(row.get('PRIORITY', ''))[:1]})
        return {**evidence, 'available': bool(lines), 'reason': 'captured' if lines else 'journal_empty_or_rotated',
                'unit': unit, 'lines': lines, 'truncated': len(raw) >= 60 or budget <= 0 or len(lines) < len(raw)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError, subprocess.SubprocessError):
        return {**evidence, 'reason': 'capture_unavailable'}
