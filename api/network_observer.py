"""Bounded read-only host socket/conntrack collectors. Never a general terminal."""
import shutil
import re
import threading
import time
import uuid
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError


class Query(BaseModel):
    model_config = {'extra': 'forbid'}
    source: Literal['summary', 'ss', 'conntrack'] = 'summary'
    protocol: Literal['tcp', 'udp'] = 'tcp'
    family: Literal['ipv4', 'ipv6'] = 'ipv4'
    state: Literal['all', 'established'] = 'established'
    limit: int = Field(default=60, strict=True, ge=1, le=120)


_active = threading.BoundedSemaphore(2)


def command(query, executable):
    if query.source == 'ss':
        args = [executable, '-H', '-n', '-t' if query.protocol == 'tcp' else '-u', '-4' if query.family == 'ipv4' else '-6']
        args += ['state', 'established'] if query.state == 'established' else ['-a']
        return args
    args = [executable, '-L', '-p', query.protocol, '-f', query.family]
    if query.state == 'established' and query.protocol == 'tcp': args += ['--state', 'ESTABLISHED']
    return args


def launch(query, args):
    # Conntrack alone needs NET_ADMIN to inspect the host network namespace.
    # The executable and every argument are fixed by the typed query above.
    props = ['DynamicUser=yes', 'NoNewPrivileges=yes', 'ProtectSystem=strict', 'ProtectHome=yes',
             'PrivateTmp=yes', 'ProtectKernelTunables=yes', 'ProtectControlGroups=yes',
             'ProtectKernelModules=yes', 'RestrictSUIDSGID=yes', 'LockPersonality=yes',
             'RuntimeMaxSec=8', 'MemoryMax=64M', 'TasksMax=8',
             'CapabilityBoundingSet=' + ('CAP_NET_ADMIN' if query.source == 'conntrack' else ''),
             'AmbientCapabilities=' + ('CAP_NET_ADMIN' if query.source == 'conntrack' else ''),
             'RestrictAddressFamilies=AF_UNIX AF_NETLINK AF_INET AF_INET6', 'IPAddressDeny=any']
    result = ['systemd-run', '--quiet', '--pipe', '--wait', '--collect', '--service-type=exec',
              '--unit=vps-control-network-read-' + uuid.uuid4().hex]
    for prop in props: result += ['--property=' + prop]
    return [*result, '--', *args]


def collect(api, query):
    if query.source == 'summary':
        import assistant_diagnostics
        return assistant_diagnostics.sanitize(api, api.get_network(None))
    result = {'source': query.source, 'checked_at': time.time(), 'scope': 'host_network_namespace',
              'filters': query.model_dump(), 'available': False, 'lines': [], 'observed_count': None,
              'truncated': False, 'total_count': None}
    executable = shutil.which(query.source)
    if not executable: return {**result, 'reason': 'collector_not_installed'}
    if not _active.acquire(blocking=False): raise HTTPException(429, 'Наблюдение соединений уже выполняется')
    try:
        from terminal_observer import bounded_run
        import assistant_diagnostics
        raw, code, timeout, truncated = bounded_run(launch(query, command(query, executable)))
        if timeout or code and not truncated: return {**result, 'reason': 'collector_unavailable'}
        lines = raw.splitlines()
        if query.source == 'conntrack': lines = [line for line in lines if re.match(r'^(?:tcp|udp)\s', line)]
        safe = [assistant_diagnostics.sanitize(api, line, max_chars=500, max_string=500) for line in lines[:query.limit]]
        return {**result, 'available': True, 'lines': safe, 'observed_count': len(safe),
                'truncated': truncated or len(lines) > query.limit,
                'state_filter_supported': query.protocol == 'tcp' or query.source == 'ss'}
    except OSError: return {**result, 'reason': 'sandbox_unavailable'}
    finally: _active.release()


def parse(body):
    try: return Query.model_validate(body)
    except ValidationError: raise HTTPException(422, 'Некорректные фильтры соединений') from None
