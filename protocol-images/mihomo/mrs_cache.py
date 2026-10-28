"""Publish automatically refreshed MRS files from a fixed catalog of feeds."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import urllib.request

import yaml


MAX_BYTES = 16 * 1024 * 1024


class MrsCache:
    def __init__(self, root, core, feeds):
        self.root = Path(root)
        self.core = Path(core)
        self.feeds = feeds
        self.lock = threading.Lock()
        self.retry_after = {}

    def path(self, name):
        if name not in self.feeds:
            raise KeyError(name)
        return self.root / (name + '.mrs')

    def metadata(self, name):
        self.path(name)  # Validate catalog membership before constructing a path.
        try:
            return json.loads((self.root / (name + '.json')).read_text())
        except (OSError, ValueError):
            return {}

    def read(self, name):
        data = self.path(name).read_bytes()
        if not 16 <= len(data) <= MAX_BYTES:
            raise ValueError('Invalid cached MRS size')
        return data

    def refresh(self, name, *, now=None, force=False):
        """Only maintenance calls this; HTTP requests never download or compile."""
        with self.lock:
            destination = self.path(name)
            feed = self.feeds[name]
            now = time.time() if now is None else now
            old = self.metadata(name)
            if not force and (now < self.retry_after.get(name, 0) or (
                    destination.is_file() and old.get('url') == feed['url']
                    and now - old.get('checked_at', 0) < feed['interval'])):
                return False
            self.retry_after[name] = now + 300
            self.root.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(feed['url'], timeout=25) as response:
                source = response.read(MAX_BYTES + 1)
            if not 0 < len(source) <= MAX_BYTES:
                raise ValueError('Invalid source feed size')
            digest = hashlib.sha256(source).hexdigest()
            with tempfile.TemporaryDirectory(prefix='build-', dir=self.root) as folder:
                folder = Path(folder)
                if digest != old.get('source_sha256') or not destination.is_file():
                    # Reject empty/error documents before invoking the converter.
                    if feed['format'] == 'yaml':
                        document = yaml.safe_load(source)
                        entries = document.get('payload') if isinstance(document, dict) else None
                    else:
                        entries = [line.strip() for line in source.decode('utf-8-sig').splitlines()
                                   if line.strip() and not line.lstrip().startswith(('#', '!'))]
                    if not isinstance(entries, list) or not entries or not all(
                            isinstance(entry, str) and entry and not any(c.isspace() for c in entry)
                            and not any(c in entry for c in '<>/') for entry in entries):
                        raise ValueError('Invalid domain feed')
                    input_path = folder / ('source.' + feed['format'])
                    input_path.write_bytes(source)
                    output_path = folder / 'rules.mrs'
                    result = subprocess.run([str(self.core), 'convert-ruleset', 'domain', feed['format'],
                                             str(input_path), str(output_path)],
                                            capture_output=True, timeout=45, check=False)
                    if result.returncode or not output_path.is_file() or not 16 <= output_path.stat().st_size <= MAX_BYTES:
                        raise RuntimeError('MRS conversion failed')
                    output_path.chmod(0o600)
                    os.replace(output_path, destination)
                metadata = folder / 'metadata.json'
                metadata.write_text(json.dumps({'url': feed['url'], 'source_sha256': digest, 'checked_at': now}))
                metadata.chmod(0o600)
                os.replace(metadata, self.root / (name + '.json'))
            self.retry_after.pop(name, None)
            return True

    def maintain(self, stopped, logger):
        while not stopped.is_set():
            for name in self.feeds:
                if stopped.is_set():
                    break
                try:
                    self.refresh(name)
                except Exception:
                    logger.warning('MRS refresh failed for %s; retaining the last successful version', name)
            stopped.wait(60)
