"""Manual acknowledgement of errors without rewriting the operation outcome."""
import os
import base64
import hashlib
import hmac
import json
import time
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

import application_operation as operations
from operation_outcome import outcome


def set_resolved(data_dir, identity, source, resolved, *, review=None, expected_fingerprint=None):
    identity = operations.operation_id(identity)
    directories = {'system': data_dir / 'operations', 'mihomo': data_dir / 'mihomo/operations',
                   'cdn': data_dir / 'cdn-operations'}
    if source not in directories or type(resolved) is not bool:
        raise ValueError('Invalid resolution')
    root = data_dir / 'mihomo' if source == 'mihomo' else data_dir
    with operations.locked(root):
        path = record_path(data_dir, identity, source)
        value = operations.read(path)
        if not value:
            raise LookupError('Operation not found')
        if outcome(value) != 'error':
            raise operations.OperationConflict('Отметить решённой можно только завершённую операцию с ошибкой')
        if expected_fingerprint is not None and fingerprint(value) != expected_fingerprint:
            raise operations.OperationConflict('Операция изменилась после проверки')
        if bool(value.get('resolved_at')) == resolved:
            return {'resolved_at': value.get('resolved_at'), **({'resolution_review': value['resolution_review']} if value.get('resolution_review') else {})}
        details = path.stat()
        value['resolved_at'] = operations.timestamp() if resolved else None
        if review and resolved:
            value['resolution_review'] = review
        elif not resolved:
            value.pop('resolution_review', None)
        operations.atomic_json(path, value)
        # Acknowledgement must not reorder history or extend its retention.
        os.utime(path, ns=(details.st_atime_ns, details.st_mtime_ns))
        # Keep the check and later reversals in the stage journal as well.
        # This is an annotation event, never a new operation result.
        from operation_log import append
        append(data_dir if source != 'mihomo' else root, {
            'id': identity, 'state': value['state'], 'stage': 'resolution', 'stage_result': 'passed',
            'message': ('ИИ: ' + review['note']) if review and resolved else
                       ('Операция отмечена решённой' if resolved else 'Отметка «Решено» снята'),
        }, source)
        return {'resolved_at': value['resolved_at'], **({'resolution_review': value['resolution_review']} if value.get('resolution_review') else {})}


def fingerprint(value):
    fields = {key: value.get(key) for key in ('id', 'state', 'result', 'updated_at', 'finished_at', 'error_code', 'message')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def record_path(root, identity, source):
    if source == 'system': return operations.record_path(root, identity)
    directories = {'system': 'operations', 'mihomo': 'mihomo/operations', 'cdn': 'cdn-operations'}
    return root / directories[source] / (operations.operation_id(identity) + '.json')


def review_offer(api, actor, report):
    import assistant_control
    value = report.get('authoritative_result') or {}
    identity, source = value.get('operation_id'), value.get('source')
    retained = any(row.get('source') == 'retained_operation_evidence' and isinstance(row.get('data'), dict) and row['data'].get('available')
                   for row in report.get('observations', []))
    if value.get('context', {}).get('outcome') != 'error' or source not in ('system', 'mihomo', 'cdn') or not (report.get('timeline') or retained):
        return None
    settings = assistant_control.read(api)
    secret = settings.get('control_token')
    if not secret: return None
    record = operations.read(record_path(api.DATA_DIR, identity, source))
    payload = {'id': identity, 'source': source, 'actor': hashlib.sha256(str(actor).encode()).hexdigest(),
               'expires': int(time.time()) + 600, 'fingerprint': fingerprint(record)}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return {'tool': 'vps_observability', 'method': 'POST', 'path': '/operation-resolution',
            'operation_id': identity, 'source': source, 'review_token': encoded + '.' + signature,
            'requirement': 'Inspect cause and current state, then supply a review explaining checks and conclusion. Acknowledgement does not repair or change the original outcome.'}


class ReviewedResolution(BaseModel):
    model_config = {'extra': 'forbid'}
    operation_id: str = Field(pattern='^[0-9a-f]{32}$')
    source: Literal['system', 'mihomo', 'cdn'] = 'system'
    resolved: bool = Field(default=True, strict=True)
    review_token: str = Field(min_length=20, max_length=2048)
    review: str = Field(min_length=12, max_length=600)


def assistant_resolve(api, actor, body):
    import assistant_control
    import assistant_roles
    import assistant_diagnostics
    settings = assistant_control.read(api)
    for permission in ('server_manage', 'server_read', 'diagnostics', 'logs_read'):
        assistant_roles.require(settings, actor, permission)
    try:
        query = ReviewedResolution.model_validate(body)
        encoded, signature = query.review_token.split('.')
        secret = settings.get('control_token')
        if not secret: raise ValueError('No review signing key')
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature): raise ValueError('Invalid signature')
        payload = json.loads(base64.urlsafe_b64decode(encoded))
        if (payload['id'] != query.operation_id or payload['source'] != query.source
                or payload['actor'] != hashlib.sha256(str(actor).encode()).hexdigest()
                or payload['expires'] < time.time()): raise ValueError('Review expired or belongs to another operation/actor')
        with operations.locked(api.DATA_DIR / 'mihomo' if query.source == 'mihomo' else api.DATA_DIR):
            current = operations.read(record_path(api.DATA_DIR, query.operation_id, query.source))
            if fingerprint(current) != payload['fingerprint']: raise ValueError('Operation changed after review')
        if len(query.review.strip()) < 12: raise ValueError('Review must explain the checks')
        note = assistant_diagnostics.sanitize(api, query.review.strip(), max_chars=600, max_string=600)
        result = set_resolved(api.DATA_DIR, query.operation_id, query.source, query.resolved,
                              review={'by': 'assistant', 'note': note}, expected_fingerprint=payload['fingerprint'])
        return {**result, 'operation_id': query.operation_id, 'source': query.source,
                'scope': 'manual_review_annotation; original_operation_outcome_unchanged'}
    except (ValidationError, ValueError, KeyError, TypeError, LookupError):
        raise HTTPException(409, 'Сначала прочитайте актуальный operation_timeline и укажите результат проверки') from None
    except OSError:
        raise HTTPException(503, 'Не удалось сохранить результат проверки операции') from None
