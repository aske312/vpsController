"""Manual acknowledgement of errors without rewriting the operation outcome."""
import os

import application_operation as operations
from operation_outcome import outcome


def set_resolved(data_dir, identity, source, resolved):
    identity = operations.operation_id(identity)
    directories = {'system': data_dir / 'operations', 'mihomo': data_dir / 'mihomo/operations',
                   'cdn': data_dir / 'cdn-operations'}
    if source not in directories or type(resolved) is not bool:
        raise ValueError('Invalid resolution')
    root = data_dir / 'mihomo' if source == 'mihomo' else data_dir
    with operations.locked(root):
        path = directories[source] / (identity + '.json')
        value = operations.read(path)
        if not value:
            raise LookupError('Operation not found')
        if outcome(value) != 'error':
            raise operations.OperationConflict('Отметить решённой можно только завершённую операцию с ошибкой')
        if bool(value.get('resolved_at')) == resolved:
            return {'resolved_at': value.get('resolved_at')}
        details = path.stat()
        value['resolved_at'] = operations.timestamp() if resolved else None
        operations.atomic_json(path, value)
        # Acknowledgement must not reorder history or extend its retention.
        os.utime(path, ns=(details.st_atime_ns, details.st_mtime_ns))
        return {'resolved_at': value['resolved_at']}
