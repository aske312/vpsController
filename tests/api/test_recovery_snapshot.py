"""A live SQLite writer must not break recovery or produce a partial database."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import tempfile
import threading
import unittest

from tests.api.support import load_module

snapshot = load_module('recovery_snapshot', 'api/recovery_snapshot.py')


class RecoverySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = self.root / 'data'
        self.source.mkdir()
        self.database = self.source / 'metrics/history.sqlite3'
        self.database.parent.mkdir()

    def test_online_snapshot_includes_committed_wal_without_copying_journals(self):
        with closing(sqlite3.connect(self.database)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('CREATE TABLE samples (value INTEGER)')
            writer.execute('INSERT INTO samples VALUES (42)')
            writer.commit()
            self.assertTrue(Path(str(self.database) + '-wal').exists())
            writer.execute('INSERT INTO samples VALUES (99)')  # uncommitted, not part of the point
            destination = self.root / 'snapshot'
            snapshot.snapshot_data(self.source, destination)
            with closing(sqlite3.connect(destination / 'metrics/history.sqlite3')) as reader:
                self.assertEqual(reader.execute('SELECT value FROM samples').fetchall(), [(42,)])
                self.assertEqual(reader.execute('PRAGMA quick_check').fetchone(), ('ok',))
            self.assertFalse(Path(str(destination / 'metrics/history.sqlite3') + '-wal').exists())
            writer.rollback()

    def test_snapshot_remains_valid_while_history_is_being_recorded(self):
        with closing(sqlite3.connect(self.database)) as database:
            database.execute('CREATE TABLE samples (value INTEGER)')
            database.commit()
        stop, ready = threading.Event(), threading.Event()

        def write():
            with closing(sqlite3.connect(self.database)) as writer:
                writer.execute('PRAGMA journal_mode=WAL')
                for value in range(10000):
                    writer.execute('INSERT INTO samples VALUES (?)', (value,))
                    writer.commit()
                    ready.set()
                    if stop.wait(.002):
                        break

        thread = threading.Thread(target=write)
        thread.start()
        try:
            self.assertTrue(ready.wait(5))
            snapshot.snapshot_data(self.source, self.root / 'snapshot')
        finally:
            stop.set()
            thread.join(timeout=5)
        with closing(sqlite3.connect(self.root / 'snapshot/metrics/history.sqlite3')) as reader:
            self.assertGreater(reader.execute('SELECT COUNT(*) FROM samples').fetchone()[0], 0)
            self.assertEqual(reader.execute('PRAGMA quick_check').fetchone(), ('ok',))

    def test_transient_files_are_excluded_and_personal_data_preserved(self):
        for name in ('recovery/point', 'tmp/request', 'logs/trace', 'application-action.json', 'clients.json'):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture')
        destination = self.root / 'snapshot'
        snapshot.snapshot_data(self.source, destination)
        self.assertEqual((destination / 'clients.json').read_text(), 'fixture')
        self.assertFalse(any((destination / name).exists() for name in snapshot.EXCLUDED))

    def test_archive_failure_removes_raw_data_and_staging_but_preserves_older_archive(self):
        archive = self.root / 'recovery.tar.gz'
        self.database.write_bytes(b'not a sqlite database')
        with self.assertRaises(sqlite3.DatabaseError):
            snapshot.build_archive(self.source, archive, [])
        self.assertFalse(archive.exists())
        self.assertEqual(list(self.root.glob('.data-snapshot-*')), [])
        archive.write_bytes(b'previous point')
        with self.assertRaises(FileExistsError):
            snapshot.build_archive(self.source, archive, [])
        self.assertEqual(archive.read_bytes(), b'previous point')

    @unittest.skipIf(os.name == 'nt', 'GNU tar integration runs on Linux')
    def test_real_archive_retains_original_data_paths_and_sqlite_integrity(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('CREATE TABLE samples (value INTEGER)')
            db.execute('INSERT INTO samples VALUES (42)')
            db.commit()
        config = self.root / 'config'
        config.mkdir()
        (config / 'setting').write_text('preserved')
        archive = self.root / 'recovery.tar.gz'
        snapshot.build_archive(self.source, archive, [config])
        extracted = self.root / 'unpacked'
        with tarfile.open(archive) as tar:
            tar.extractall(extracted, filter='data')
        data = extracted / self.source.relative_to(self.source.anchor)
        with closing(sqlite3.connect(data / 'metrics/history.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT value FROM samples').fetchall(), [(42,)])
        self.assertEqual((extracted / config.relative_to(config.anchor) / 'setting').read_text(), 'preserved')
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
