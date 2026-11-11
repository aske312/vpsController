"""Stage live application data, using SQLite's online backup before archiving."""
from __future__ import annotations

import argparse
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time

SQLITE_HEADER = b'SQLite format 3\0'
EXCLUDED = {'recovery', 'tmp', 'logs', 'application-action.json'}


def is_sqlite(path):
    if not path.is_file() or path.is_symlink():
        return False
    with path.open('rb') as stream:
        return stream.read(16) == SQLITE_HEADER


def snapshot_data(source: Path, destination: Path, *, database_timeout=30):
    def metadata(original, copied):
        shutil.copystat(original, copied, follow_symlinks=False)
        if os.name != 'nt':
            details = original.lstat()
            os.chown(copied, details.st_uid, details.st_gid, follow_symlinks=False)

    def copy(original, copied, relative):
        details = original.lstat()
        if stat.S_ISLNK(details.st_mode):
            copied.symlink_to(os.readlink(original), target_is_directory=original.is_dir())
        elif stat.S_ISDIR(details.st_mode):
            copied.mkdir(mode=0o700, parents=True)
            for child in original.iterdir():
                child_relative = relative / child.name
                if child_relative.parts == ('assistant', 'conversations'):
                    continue  # Disposable context cache can be 5 GB; durable preferences are backed up.
                if len(child_relative.parts) == 1 and child.name in EXCLUDED:
                    continue
                if any(child.name.endswith(suffix) and is_sqlite(child.with_name(child.name[:-len(suffix)]))
                       for suffix in ('-wal', '-shm', '-journal')):
                    continue
                copy(child, copied / child.name, child_relative)
        elif stat.S_ISREG(details.st_mode):
            if is_sqlite(original) or original.suffix in {'.sqlite', '.sqlite3'}:
                deadline = time.monotonic() + database_timeout

                def progress(*_):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('SQLite snapshot did not complete in time')

                with closing(sqlite3.connect(original.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as reader:
                    with closing(sqlite3.connect(copied)) as writer:
                        reader.backup(writer, pages=256, progress=progress, sleep=.05)
                        if writer.execute('PRAGMA quick_check').fetchone() != ('ok',):
                            raise ValueError('SQLite snapshot failed integrity verification')
            else:
                shutil.copyfile(original, copied)
        else:
            raise ValueError('Unsupported file type in application recovery data')
        metadata(original, copied)

    if not source.is_dir() or source.is_symlink() or destination.exists():
        raise ValueError('Invalid application snapshot paths')
    copy(source, destination, Path())


def build_archive(data_dir: Path, archive: Path, paths, *, run=subprocess.run):
    if not data_dir.is_absolute() or not archive.is_absolute():
        raise ValueError('Recovery paths must be absolute')
    # Refuse collisions rather than replacing a previous recovery candidate.
    descriptor = os.open(archive, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with tempfile.TemporaryDirectory(prefix='.data-snapshot-', dir=archive.parent) as temporary:
            relative = data_dir.relative_to(data_dir.anchor)
            snapshot_data(data_dir, Path(temporary) / relative)
            run(['tar', '--exclude=__pycache__', '-czf', str(archive), *map(str, paths),
                 '-C', temporary, relative.as_posix()], check=True)
    except BaseException:
        archive.unlink(missing_ok=True)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('paths', nargs='+', type=Path)
    args = parser.parse_args()
    try:
        build_archive(args.data_dir, args.archive, args.paths)
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError):
        raise SystemExit('Recovery snapshot failed; server settings were not changed')
