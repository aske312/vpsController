"""Read-only reboot and service lifecycle evidence, independent of HTTP traffic."""
from datetime import datetime
import json
import math
import re
import subprocess
import time

from fastapi import HTTPException
from pydantic import BaseModel, Field

import application_operation as operations
import diagnostic_tools as reports

UUID = r'(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
UNIT = r'[a-zA-Z0-9][a-zA-Z0-9@_.-]{0,150}\.(?:service|socket|timer|target)'
LIFECYCLE = 'Starting |Started |Stopping |Stopped |Main process exited|Failed |Failed with result|Scheduled restart job|Rebooting|Shutting down|Reached target .*Reboot|Deactivated successfully|Watchdog|oom-kill'
KERNEL = 'Out of memory|oom-kill|Killed process|Kernel panic|watchdog:|soft lockup|hard LOCKUP|I/O error'
TIMERS = ('apt-daily.timer', 'apt-daily-upgrade.timer', 'vps-control-auto-update.timer', 'vps-control-auto-reboot.timer')


class Query(BaseModel):
    model_config = {'extra': 'forbid'}
    minutes: int = Field(default=1440, strict=True, ge=1, le=10080)
    limit: int = Field(default=40, strict=True, ge=1, le=120)
    until: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    boot_id: str | None = Field(default=None, pattern='^' + UUID + '$')
    services: list[str] = Field(default_factory=list, max_length=8)


def normalized(value):
    return value.replace('-', '') if isinstance(value, str) and re.fullmatch(UUID, value) else None


def epoch(value):
    try:
        result = datetime.fromisoformat(value.strip().replace('Z', '+00:00')).timestamp()
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, AttributeError, OverflowError, OSError): return None


def command(*args):
    """No shell; grep exit 1 without diagnostics is an empty matching set."""
    try:
        value = subprocess.run(args, capture_output=True, text=True, timeout=6, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    except (OSError, subprocess.TimeoutExpired): raise HTTPException(503, 'Источник диагностики недоступен') from None
    empty_match = value.returncode == 1 and args[0] == 'journalctl' and any(arg.startswith('--grep=') for arg in args) and not value.stderr.strip() and not value.stdout.strip()
    missing_units = args[0] == 'systemctl' and value.returncode == 4 and bool(value.stdout.strip())
    if value.returncode and not empty_match and not missing_units: raise HTTPException(503, 'Источник диагностики недоступен')
    return value.stdout


def journal(api, query, start, end, *, units=(), kernel=False):
    args = ['journalctl', '--since', '@' + str(start), '--until', '@' + str(end), '--reverse',
            '--no-pager', '--output=json', '--lines=' + str(query.limit), '--grep=' + (KERNEL if kernel else LIFECYCLE),
            '--output-fields=__REALTIME_TIMESTAMP,__MONOTONIC_TIMESTAMP,_BOOT_ID,_SYSTEMD_UNIT,UNIT,_SYSTEMD_INVOCATION_ID,INVOCATION_ID,PRIORITY,MESSAGE']
    if query.boot_id: args += ['--boot=' + normalized(query.boot_id)]
    if kernel: args += ['_TRANSPORT=kernel']  # --dmesg would silently imply the current boot.
    for unit in units: args += ['--unit=' + unit]
    raw = command(*args)
    import assistant_diagnostics
    rows, invalid, budget = [], 0, 18000
    for line in raw.splitlines()[:query.limit]:
        if budget <= 0: break
        try:
            value = json.loads(line)
            at = int(value['__REALTIME_TIMESTAMP']) / 1000000
            if not math.isfinite(at) or not start <= at <= end or not isinstance(value.get('MESSAGE'), str): raise ValueError()
            boot = normalized(value.get('_BOOT_ID'))
            if query.boot_id and boot != normalized(query.boot_id): raise ValueError()
            message = assistant_diagnostics.sanitize(api, value['MESSAGE'], max_string=1200)[:budget]
            budget -= len(message)
            unit = value.get('UNIT') or value.get('_SYSTEMD_UNIT')
            monotonic = str(value.get('__MONOTONIC_TIMESTAMP', ''))
            rows.append({'at': at, 'boot_id': boot, 'unit': unit if isinstance(unit, str) and re.fullmatch(UNIT, unit) else None,
                         'invocation_id': normalized(value.get('INVOCATION_ID') or value.get('_SYSTEMD_INVOCATION_ID')),
                         'monotonic_us': int(monotonic) if re.fullmatch(r'[0-9]{1,20}', monotonic) else None,
                         'message': message})
        except (ValueError, TypeError, KeyError, OverflowError, AttributeError): invalid += 1
    return {'records': rows, 'coverage': {'available': True, 'possibly_truncated': len(raw.splitlines()) >= query.limit or budget <= 0,
            'unparsed_records': invalid, 'scope': 'matching_retained_events_only; absence_does_not_exclude_failure'}}


def collect(api, query):
    result = reports.report('vps_restarts')
    end = query.until if query.until is not None else time.time()
    if end > time.time() + 60: raise HTTPException(422, 'until не может быть в будущем')
    start = max(0, end - query.minutes * 60)
    page_boundaries = []
    definitions = api.managed_services()
    chosen = list(dict.fromkeys(query.services or [key for key in ('api', 'web', 'gateway', 'nodera') if key in definitions]))
    if any(key not in definitions for key in chosen): raise HTTPException(422, 'Выберите службы из vps_services')
    units = [definitions[key]['unit'] for key in chosen]
    if any(not isinstance(unit, str) or not re.fullmatch(UNIT, unit) for unit in units): raise HTTPException(422, 'Недопустимая служба')
    current = normalized(operations.boot_id())
    try: boot_at = api.system_boot_time()
    except (OSError, AttributeError): boot_at = None
    reports.observation(result, 'current_boot', 'proc', {'boot_id': current, 'booted_at': boot_at.timestamp() if boot_at else None,
                        'scope': 'current_host; not_historical_window'}, 'observed' if current else 'unknown')
    try:
        raw = command('journalctl', '--list-boots', '--no-pager', '--output=json', '--lines=16')
        entries = json.loads(raw)
        if not isinstance(entries, list): raise ValueError()
        boots = []
        for entry in entries[-16:]:
            identity = normalized(entry.get('boot_id'))
            first, last = int(entry['first_entry']) / 1000000, int(entry['last_entry']) / 1000000
            if identity and math.isfinite(first) and math.isfinite(last) and 0 < first <= last:
                boots.append({'boot_id': identity, 'first_retained_at': first, 'last_retained_at': last})
        reports.observation(result, 'boots', 'journald_boot_inventory', boots)
        result['coverage']['boots'] = {'possibly_truncated': len(entries) >= 16,
            'scope': 'up_to_16_retained_boots; first_entry_is_not_exact_boot_time; missing_boots_and_rotation_unknown'}
    except (HTTPException, OSError, ValueError, TypeError, KeyError, OverflowError, AttributeError):
        reports.unknown(result, 'boots', 'boot_inventory_unavailable')
    for identity, kernel in [('service_lifecycle', False), ('kernel_failures', True)]:
        try:
            value = journal(api, query, start, end, kernel=kernel,
                units=units + ['apt-daily-upgrade.service', 'vps-control-auto-update.service', 'systemd-reboot.service'] if not kernel else ())
            reports.observation(result, identity, 'journald', value['records'])
            result['coverage'][identity] = value['coverage']
            if value['coverage']['possibly_truncated'] and value['records']:
                page_boundaries.append(min(row['at'] for row in value['records']))
        except (HTTPException, OSError): reports.unknown(result, identity, 'journal_unavailable')
    try:
        raw = command('systemctl', 'show', *TIMERS, 'apt-daily-upgrade.service', '--no-pager',
            '--property=Id,LoadState,ActiveState,LastTriggerUSec,NextElapseUSecRealtime,ExecStart,TriggeredBy')
        timers = []
        entrypoint = None
        for block in raw.strip().split('\n\n'):
            values = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
            if values.get('Id') in TIMERS:
                timers.append({key: values[key] for key in ('Id', 'LoadState', 'ActiveState', 'LastTriggerUSec', 'NextElapseUSecRealtime') if key in values})
            elif values.get('Id') == 'apt-daily-upgrade.service':
                entrypoint = {'unit': 'apt-daily-upgrade.service',
                    'recognized_application_auto_update': bool(re.search(r'argv\[\]=/usr/local/sbin/vps-control auto-safe-update(?:\s|;|$)', values.get('ExecStart', ''))),
                    'triggered_by_apt_timer': 'apt-daily-upgrade.timer' in values.get('TriggeredBy', '').split(),
                    'scope': 'current_unit_definition; not_proof_of_past_invocation; unrecognized_entrypoint_is_not_proof_of_absence'}
        reports.observation(result, 'timers', 'systemd', timers)
        if entrypoint: reports.observation(result, 'apt_upgrade_entrypoint', 'systemd', entrypoint)
        else: reports.unknown(result, 'apt_upgrade_entrypoint', 'unit_definition_unavailable')
    except (HTTPException, OSError): reports.unknown(result, 'timers', 'timer_inventory_unavailable')
    try:
        from application_diagnostics import records
        values, coverage = records(api)
        requests = []
        for source, record in values:
            if source != 'system' or record.get('action') not in ('auto-safe-update', 'safe-update', 'kernel-update', 'reboot', 'restart', 'poweroff'): continue
            at = epoch(record.get('started_at'))
            boot = normalized(record.get('boot_id'))
            if at is None or not start <= at <= end or query.boot_id and boot != normalized(query.boot_id): continue
            state = record.get('state')
            if state not in ('queued', 'running', 'rebooting', 'powering-off', 'succeeded', 'failed', 'unknown', 'cancelled'): state = 'unknown'
            requests.append({'operation_id': record['id'], 'action': record['action'], 'state': state,
                'started_at': at, 'boot_id': boot,
                'origin_unit': record.get('origin_unit') if isinstance(record.get('origin_unit'), str) and re.fullmatch(UNIT, record['origin_unit']) else None,
                'systemd_invocation_id': normalized(record.get('systemd_invocation_id')),
                'relation': 'recorded_in_another_boot' if current and boot and current != boot else 'same_boot_or_unknown',
                'next_step': {'tool': 'operation_timeline', 'arguments': {'operation_id': record['id']}}})
        requests.sort(key=lambda row: row['started_at'], reverse=True)
        reports.observation(result, 'operations', 'application_operation_history', requests[:query.limit])
        if len(requests) > query.limit: page_boundaries.append(requests[query.limit - 1]['started_at'])
        result['coverage']['operations'] = {'sources': coverage, 'possibly_truncated': len(requests) > query.limit,
            'causation': 'operation_presence_and_different_boot_alone_do_not_prove_reboot_cause; inspect_rebooting_stage_and_journal'}
    except (OSError, ValueError): reports.unknown(result, 'operations', 'operation_history_unavailable')
    # The update report is overwritten by each update. Never attach it to an
    # older operation as its result, and never expose arbitrary report text.
    try:
        path = api.DATA_DIR / 'recovery' / 'safe-update-report.txt'
        if path.is_symlink(): raise OSError()
        with path.open('rb') as stream: raw = stream.read(262145)
        text = raw[:262144].decode('utf-8', errors='replace').replace('\r\n', '\n')
        begun = re.search(r'^Started: (.+)$', text, re.M)
        summary = re.search(r'^(\d+) upgraded, (\d+) newly installed, (\d+) to remove and (\d+) not upgraded\.', text, re.M)
        reports.observation(result, 'latest_update_report', 'safe_update_report', {
            'started_at': epoch(begun[1]) if begun else None,
            'packages': dict(zip(('upgraded', 'installed', 'removed', 'not_upgraded'), map(int, summary.groups()))) if summary else None,
            'automatic_reboot_recorded': 'Reboot: automatic after successful checks' in text,
            'success_recorded': bool(re.search(r'^Result: success$', text, re.M)),
            'scope': 'latest_report_only; compare_started_at_to_operation; not_a_reboot_confirmation', 'truncated': len(raw) > 262144})
    except OSError: reports.unknown(result, 'latest_update_report', 'update_report_unavailable')
    result['coverage'].update(requested_from=start, requested_until=end, boot_id=normalized(query.boot_id),
        service_ids=chosen, current_state_is_not_history=True,
        restart_counter='NRestarts_counts_automatic_service_restarts_only; not_manual_restarts_or_host_reboots')
    result['missing_data'].append({'source': 'hypervisor', 'reason': 'provider_reset_power_loss_and_kernel_crash_cannot_be_distinguished_without_retained_evidence_or_provider_events'})
    before = max(page_boundaries) - 0.000001 if page_boundaries else start
    if before > 0:
        result['next_steps'] = [{'tool': 'vps_observability', 'arguments': {'path': '/restarts',
            'body': {**query.model_dump(exclude_none=True), 'until': before}},
            'reason': 'inspect_older_matching_events; boundary_events_with_identical_timestamps_may_be_truncated'}]
    return result
