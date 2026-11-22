import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tests.api.support import api
from automation_schedule import admit, default_settings, due_slot, read_settings


class AutomationScheduleTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = default_settings()
        self.settings['update'].update(enabled=True, weekday='Wed', hour=5, minute=21)
        self.settings['reboot'].update(enabled=True, weekday='Sat', hour=4, minute=12)
        self.save()

    def save(self):
        (self.root / 'automation.json').write_text(json.dumps(self.settings), encoding='utf-8')

    def check(self, kind='update', now='2026-11-18T05:22:00+02:00', origin=None, manual=False):
        return admit(self.root, kind, now=datetime.fromisoformat(now),
                     origin=origin if origin is not None else f'vps-control-auto-{kind}.service', manual=manual)

    def test_distinct_saved_days_control_update_and_reboot_independently(self):
        self.assertEqual(self.check(), 'allow')
        self.assertEqual(self.check('reboot'), 'outside-schedule')
        self.assertEqual(self.check('reboot', now='2026-11-21T04:13:00+02:00', manual=True), 'allow')
        self.assertEqual(self.check(now='2026-11-22T02:31:00+02:00'), 'outside-schedule')

    def test_disabled_or_new_day_blocks_previously_rendered_units(self):
        self.settings['update']['enabled'] = False
        self.save()
        self.assertEqual(self.check(), 'disabled')
        self.settings['update'].update(enabled=True, weekday='Thu')
        self.save()
        self.assertEqual(self.check(), 'outside-schedule')
        self.assertEqual(self.check(now='2026-11-19T05:22:00+02:00'), 'allow')
        self.settings['reboot']['enabled'] = False
        self.save()
        self.assertEqual(self.check('reboot', now='2026-11-21T04:13:00+02:00', manual=True), 'disabled')

    def test_daily_distro_scheduler_is_rejected_even_at_the_correct_time(self):
        for origin in ('apt-daily-upgrade.service', '', 'vps-control-auto-cleanup.service'):
            with self.subTest(origin=origin):
                self.assertEqual(self.check(origin=origin), 'foreign-scheduler')
        self.assertFalse((self.root / 'automation-runs.json').exists())
        self.assertEqual(self.check('reboot', origin='apt-daily-upgrade.service', manual=True), 'foreign-scheduler')

    def test_manual_reboot_and_ui_action_remain_independent_of_disabled_schedule(self):
        self.settings['reboot']['enabled'] = False
        self.save()
        for origin in ('', 'vps-control-action-fixture.service', 'ssh.service'):
            with self.subTest(origin=origin):
                self.assertEqual(self.check('reboot', origin=origin, manual=True), 'allow')
        self.assertFalse((self.root / 'automation-runs.json').exists())

    def test_pending_maintenance_and_recovery_block_automatic_work(self):
        for name in ('service-mode.json', 'automation-recovery.json'):
            with self.subTest(name=name):
                marker = self.root / name
                marker.touch()
                self.assertEqual(self.check(), 'paused')
                marker.unlink()
        self.assertFalse((self.root / 'automation-runs.json').exists())

    def test_duplicate_slot_is_durable_across_worker_and_server_restarts(self):
        self.assertEqual(self.check(), 'allow')
        self.assertEqual(self.check(now='2026-11-18T05:22:59+02:00'), 'duplicate')
        self.assertEqual(self.check(now='2026-11-25T05:22:00+02:00'), 'allow')
        self.assertEqual(self.check(now='2026-11-18T05:22:00+02:00'), 'duplicate')

    def test_missed_or_late_schedule_never_catches_up_after_boot(self):
        for now in ('2026-11-18T05:20:59+02:00', '2026-11-18T05:24:00+02:00',
                    '2026-11-18T07:00:00+02:00', '2026-11-19T05:22:00+02:00'):
            with self.subTest(now=now):
                self.assertEqual(self.check(now=now), 'outside-schedule')
        self.assertEqual(self.check(now='2026-11-18T05:23:00+02:00'), 'allow')

    def test_daily_monthly_and_midnight_jitter_follow_server_calendar(self):
        cases = [
            ({'cadence': 'daily', 'hour': 18, 'minute': 9}, '2026-11-19T18:10:00+02:00', True),
            ({'cadence': 'monthly', 'hour': 1, 'minute': 5}, '2026-12-01T01:06:00+02:00', True),
            ({'cadence': 'monthly', 'hour': 1, 'minute': 5}, '2026-12-02T01:06:00+02:00', False),
            ({'cadence': 'weekly', 'weekday': 'Sun', 'hour': 23, 'minute': 59}, '2026-11-23T00:01:00+02:00', True),
        ]
        for patch, now, due in cases:
            with self.subTest(patch=patch, now=now):
                schedule = {**self.settings['update'], **patch}
                self.assertEqual(due_slot(schedule, datetime.fromisoformat(now)) is not None, due)

    def test_invalid_settings_and_history_fail_closed(self):
        path = self.root / 'automation.json'
        for value in ('{', '[]', '{"update":{"enabled":"yes"}}', '{"update":{"hour":true}}'):
            with self.subTest(value=value):
                path.write_text(value)
                with self.assertRaises(ValueError):
                    self.check()
        self.save()
        for value in ('{', '{"update":{}}', '{"update":{"slot":12,"started_at":"now"}}'):
            with self.subTest(history=value):
                (self.root / 'automation-runs.json').write_text(value)
                with self.assertRaises(ValueError):
                    self.check()

    def test_missing_settings_are_disabled_and_render_does_not_rewrite_user_values(self):
        path = self.root / 'automation.json'
        original = path.read_bytes()
        self.assertEqual(read_settings(path), self.settings)
        self.assertEqual(path.read_bytes(), original)
        path.unlink()
        self.assertEqual(self.check(), 'disabled')
        self.assertEqual(read_settings(path), api.default_automation())
        self.assertTrue(all(item['enabled'] is False for item in api.default_automation().values()))
        self.assertFalse(path.exists())
