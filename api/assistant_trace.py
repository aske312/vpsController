"""Bounded, private AI execution evidence; never store prompts or tool values."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import re
import threading
import time

from fastapi import HTTPException

current = ContextVar('assistant_trace', default=None)
RETENTION = 7 * 86400
MAX_FILES = 256
MAX_BYTES = 256 * 1024
STAGES = {'request_started', 'request_finished', 'runtime_started', 'model_start', 'model_result', 'model_error', 'provider_transport', 'provider_error', 'provider_retry', 'tool_start', 'tool_result', 'repeat_blocked', 'summary_start', 'summary_result', 'budget_exhausted', 'trace_truncated'}
FIELDS = {'name', 'view', 'method', 'path', 'body', 'values', 'expected_revision', 'operation_id', 'profile_id', 'device_id', 'routing', 'structured_policy', 'structured_policy_selection', 'groups', 'rules', 'members', 'kind', 'value', 'target', 'connections', 'devices', 'settings', 'limits', 'section', 'module_id', 'group_id', 'service', 'action', 'selection', 'telegram_id', 'query', 'url', 'timeout', 'interval', 'strategy', 'max_failed_times', 'tolerance', 'request_id', 'operation_ids'}


def safe_field(value):
    if not isinstance(value, str): return None
    return '.'.join(part if part in FIELDS or part.isdigit() and len(part) < 4 else '*' for part in value.split('.')[:8])[:160]


def safe_event(value):
    if not isinstance(value, dict) or not isinstance(value.get('stage'), str) or value['stage'] not in STAGES: return None
    result = {'stage': value['stage']}
    if isinstance(value.get('model_id'), str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_./+-]{0,159}', value['model_id']): result['model_id'] = value['model_id']
    if isinstance(value.get('runtime_version'), str) and re.fullmatch(r'[0-9]{1,3}(?:\.[0-9]{1,3}){2}', value['runtime_version']): result['runtime_version'] = value['runtime_version']
    for key in ('tool', 'error_code', 'status'):
        if isinstance(value.get(key), str) and re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]{0,79}', value[key]): result[key] = value[key]
    for key in ('request_id', 'operation_id', 'conversation_id'):
        if isinstance(value.get(key), str) and re.fullmatch(r'[a-f0-9]{32}', value[key]): result[key] = value[key]
    if isinstance(value.get('operation_ids'), list): result['operation_ids'] = [identity for identity in value['operation_ids'][:32] if isinstance(identity, str) and re.fullmatch(r'[a-f0-9]{32}', identity)]
    for key in ('round', 'duration_ms', 'http_status', 'tool_count', 'answer_chars', 'argument_chars', 'prompt_chars', 'history_turns', 'attachments', 'prompt_tokens', 'completion_tokens'):
        item = value.get(key)
        if type(item) in (int, float) and 0 <= item <= 10**9: result[key] = round(item, 2)
    if isinstance(value.get('fields'), list): result['fields'] = [safe_field(field) for field in value['fields'][:24] if safe_field(field)]
    if value.get('source') in ('local', 'api', 'mihomo'): result['source'] = value['source']
    if value.get('transport_phase') in ('dns', 'tcp_connect', 'tls_handshake', 'request_headers', 'request_body', 'response_headers', 'response_body', 'response_parse', 'unknown'):
        result['transport_phase'] = value['transport_phase']
    if value.get('transport_reason') in ('certificate_verification', 'timeout', 'cancelled', 'dns_resolution', 'connection_refused', 'connection_reset', 'unexpected_eof', 'tls_error', 'protocol_error', 'http_error', 'invalid_response', 'unknown'):
        result['transport_reason'] = value['transport_reason']
    if isinstance(value.get('exception_types'), list):
        allowed = {'ConnectTimeout', 'ReadTimeout', 'WriteTimeout', 'PoolTimeout', 'TimeoutError', 'ConnectError', 'ReadError', 'WriteError',
                   'RemoteProtocolError', 'LocalProtocolError', 'SSLError', 'SSLCertVerificationError', 'SSLEOFError', 'SSLZeroReturnError',
                   'SSLWantReadError', 'SSLWantWriteError', 'ConnectionResetError', 'ConnectionRefusedError', 'BrokenPipeError', 'OSError',
                   'gaierror', 'ProviderFailure', 'JSONDecodeError', 'OtherError'}
        result['exception_types'] = [name for name in value['exception_types'][:8] if isinstance(name, str) and name in allowed]
    for key, maximum in (('os_errno', 4095), ('certificate_verify_code', 999)):
        if type(value.get(key)) is int and 0 <= value[key] <= maximum: result[key] = value[key]
    if value.get('tls_reason') in ('CERTIFICATE_VERIFY_FAILED', 'WRONG_VERSION_NUMBER', 'UNEXPECTED_EOF_WHILE_READING',
                                  'TLSV1_ALERT_PROTOCOL_VERSION', 'TLSV1_ALERT_INTERNAL_ERROR', 'TLSV1_ALERT_UNKNOWN_CA',
                                  'SSLV3_ALERT_HANDSHAKE_FAILURE', 'SSLV3_ALERT_BAD_CERTIFICATE', 'SSLV3_ALERT_CERTIFICATE_EXPIRED',
                                  'TLSV1_ALERT_DECODE_ERROR', 'DECRYPTION_FAILED_OR_BAD_RECORD_MAC'):
        result['tls_reason'] = value['tls_reason']
    if type(value.get('attempt')) is int and 1 <= value['attempt'] <= 2:
        result['attempt'] = value['attempt']
    if isinstance(value.get('validation'), list):
        result['validation'] = [{'field': safe_field(row.get('field')), 'constraint': row['constraint']} for row in value['validation'][:8]
                                if isinstance(row, dict) and safe_field(row.get('field')) and row.get('constraint') in ('type', 'required', 'extra', 'enum', 'pattern', 'minimum', 'maximum', 'minLength', 'maxLength', 'revision_format', 'schema')]
    if isinstance(value.get('argument_types'), dict):
        result['argument_types'] = {safe_field(key): kind for key, kind in list(value['argument_types'].items())[:32] if safe_field(key) and kind in ('str', 'int', 'float', 'bool', 'dict', 'list', 'NoneType')}
    return result


def directory(api): return Path(api.DATA_DIR) / 'assistant' / 'traces'


def record(value):
    trace = current.get()
    event = safe_event(value)
    if not trace or not event: return
    try:
        with trace['lock']:
            file = trace['file']
            if trace.get('full'): return
            size = file.stat().st_size if file.exists() else 0
            event.update(at=time.time(), elapsed_ms=round((time.monotonic() - trace['start']) * 1000, 2))
            encoded = json.dumps(event, ensure_ascii=False) + '\n'
            if size + len(encoded.encode('utf-8')) > MAX_BYTES - 128:
                trace['full'] = True
                encoded = json.dumps({'stage': 'trace_truncated', 'at': time.time()}) + '\n'
                if size + len(encoded) > MAX_BYTES: return
            with os.fdopen(os.open(file, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), 'a', encoding='utf-8') as stream:
                stream.write(encoded)
    except OSError:
        pass  # Diagnostics cannot discard a dialogue result.


@contextmanager
def capture(api, identity, actor, channel):
    if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity): raise ValueError('Invalid AI trace identity')
    folder = directory(api)
    try:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        for index, (file, modified) in enumerate(recent_files(folder)):
            if index >= MAX_FILES - 1 or modified < time.time() - RETENTION: file.unlink(missing_ok=True)
        file = folder / (identity + '.jsonl')
        if not file.exists():
            with os.fdopen(os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as stream:
                stream.write(json.dumps({'request_id': identity, 'actor': actor, 'channel': channel, 'created_at': time.time()}) + '\n')
        context = {'file': file, 'start': time.monotonic(), 'lock': threading.Lock()}
    except OSError:
        context = None
    token = current.set(context)
    try: yield
    finally: current.reset(token)


def read(api, identity, actor='panel'):
    if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity): raise HTTPException(422, 'Некорректный ID запроса AI')
    try:
        file = directory(api) / (identity + '.jsonl')
        stat = file.stat()
        if stat.st_mtime < time.time() - RETENTION: raise OSError('expired')
        with file.open(encoding='utf-8') as stream: raw = stream.read(MAX_BYTES + 4096)
        lines = raw.splitlines()
        meta = json.loads(lines[0])
        if actor != 'panel' and str(meta.get('actor')) != str(actor): raise OSError('scope')
    except (OSError, ValueError, IndexError): raise HTTPException(404, 'Журнал запроса недоступен или срок хранения истёк') from None
    events = []
    for line in lines[1:]:
        try: events.append(json.loads(line))
        except ValueError: break  # A worker may still be appending the last line.
    return {**meta, 'events': events, 'retention_days': RETENTION // 86400, 'truncated': stat.st_size >= MAX_BYTES or any(row.get('stage') == 'trace_truncated' for row in events), 'complete': any(row.get('stage') == 'request_finished' for row in events)}


def recent_files(folder):
    result = []
    for file in folder.glob('*.jsonl'):
        try: result.append((file, file.stat().st_mtime))
        except OSError: continue
    return sorted(result, key=lambda row: row[1], reverse=True)


def listing(api, actor='panel'):
    rows = []
    for file, _ in recent_files(directory(api))[:MAX_FILES]:
        try: trace = read(api, file.stem, actor)
        except HTTPException: continue
        errors = [row for row in trace['events'] if row.get('error_code')]
        rows.append({key: trace[key] for key in ('request_id', 'actor', 'channel', 'created_at', 'complete')} | {'errors': len(errors), 'last_stage': trace['events'][-1]['stage'] if trace['events'] else None})
        if len(rows) == 20: break
    return {'items': rows, 'retention_days': RETENTION // 86400}
