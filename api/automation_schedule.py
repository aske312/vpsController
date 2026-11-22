"""Admission for destructive maintenance, independent of a timer's saved state."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path

DEFAULTS = {
    'reboot': {'enabled': False, 'cadence': 'weekly', 'weekday': 'Sun', 'hour': 4, 'minute': 0},
    'cleanup': {'enabled': False, 'cadence': 'weekly', 'weekday': 'Sun', 'hour': 3, 'minute': 0},
    'update': {'enabled': False, 'cadence': 'weekly', 'weekday': 'Sun', 'hour': 2, 'minute': 30},
}
WEEKDAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
# Timers use <=120s jitter and 1s accuracy. Late/catch-up work is skipped.
START_WINDOW = timedelta(seconds=180)


def default_settings():
    return deepcopy(DEFAULTS)


def read_settings(path: Path):
    if path.is_symlink():
        raise ValueError('Schedule replaced by symlink')
    try:
        stored = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        stored = {}
    if not isinstance(stored, dict):
        raise ValueError('Invalid maintenance settings')
    settings = default_settings()
    for kind, defaults in settings.items():
        item = stored.get(kind, {})
        if not isinstance(item, dict):
            raise ValueError('Invalid maintenance schedule')
        item = {**defaults, **item}
        if (type(item['enabled']) is not bool or item['cadence'] not in ('daily', 'weekly', 'monthly')
                or item['weekday'] not in WEEKDAYS or type(item['hour']) is not int
                or not 0 <= item['hour'] <= 23 or type(item['minute']) is not int
                or not 0 <= item['minute'] <= 59):
            raise ValueError('Invalid maintenance schedule')
        settings[kind] = item
    return settings


def due_slot(schedule, now: datetime):
    # The previous day covers jitter for a 23:59 schedule crossing midnight.
    for date in (now.date(), now.date() - timedelta(days=1)):
        if schedule['cadence'] == 'weekly' and WEEKDAYS[date.weekday()] != schedule['weekday']:
            continue
        if schedule['cadence'] == 'monthly' and date.day != 1:
            continue
        start = now.replace(year=date.year, month=date.month, day=date.day,
                            hour=schedule['hour'], minute=schedule['minute'], second=0, microsecond=0)
        if timedelta(0) <= now - start < START_WINDOW:
            # One wall-clock slot even if daylight saving repeats an hour.
            return start.strftime('%Y-%m-%dT%H:%M')
    return None


def admit(data_dir: Path, kind: str, *, origin: str, now: datetime, manual=False):
    from application_operation import atomic_json, locked
    if kind not in ('update', 'reboot'):
        raise ValueError('Unsupported automatic action')
    automatic_origin = origin.startswith('vps-control-auto-') or origin == 'apt-daily-upgrade.service'
    if manual and not automatic_origin:
        return 'allow'
    if origin != f'vps-control-auto-{kind}.service':
        return 'foreign-scheduler'
    with locked(data_dir):
        if any((data_dir / name).exists() or (data_dir / name).is_symlink()
               for name in ('service-mode.json', 'automation-recovery.json')):
            return 'paused'
        schedule = read_settings(data_dir / 'automation.json')[kind]
        if not schedule['enabled']:
            return 'disabled'
        slot = due_slot(schedule, now)
        if slot is None:
            return 'outside-schedule'
        path = data_dir / 'automation-runs.json'
        if path.is_symlink():
            raise ValueError('Maintenance history replaced by symlink')
        try:
            runs = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            runs = {}
        if not isinstance(runs, dict):
            raise ValueError('Invalid maintenance history')
        previous = runs.get(kind, {})
        if not isinstance(previous, dict):
            raise ValueError('Invalid maintenance history')
        if previous.get('slot') == slot:
            return 'duplicate'
        runs[kind] = {'slot': slot, 'started_at': now.isoformat()}
        # Claim before packages/reboot. An interrupted attempt requires a manual retry.
        atomic_json(path, runs)
    return 'allow'


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='action', required=True)
    render = subparsers.add_parser('render')
    render.add_argument('settings', type=Path)
    check = subparsers.add_parser('check')
    check.add_argument('data_dir', type=Path)
    check.add_argument('kind', choices=('update', 'reboot'))
    check.add_argument('--manual', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'render':
            for item in read_settings(args.settings).values():
                print('yes' if item['enabled'] else 'no', item['cadence'], item['weekday'], item['hour'], item['minute'])
        else:
            from application_operation import worker_origin
            print(admit(args.data_dir, args.kind, origin=worker_origin().get('origin_unit', ''),
                        now=datetime.now().astimezone(), manual=args.manual))
    except (OSError, ValueError, TypeError):
        parser.exit(2, 'Не удалось проверить сохранённое расписание; автоматическое действие запрещено.\n')


if __name__ == '__main__':
    main()
