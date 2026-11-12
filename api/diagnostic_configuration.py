"""Configuration generation inventory: metadata only, never secret values or their hashes."""
import hashlib
import json
from pathlib import Path


def inventory(api):
    root = api.DATA_DIR
    groups = {
        'application': [root / 'automation.json', root / 'service-mode.json', root / 'operation-history-settings.json'],
        'environment': [getattr(api, 'ENV_FILE', Path('/etc/vps-control/environment'))],
        'dns': [root / 'dns-settings.json'],
        'network': [root / name for name in ('network-endpoints.json', 'network-endpoint-retirements.json', 'server-addresses.json', 'protected-channels.json')],
        'security': [root / 'ssh-access.json', getattr(api, 'LOGGING_CONFIG_FILE', Path('/etc/vps-control-logging.conf')), Path('/etc/nftables.conf')],
        'mihomo_profiles': [root / 'mihomo/profiles.json'],
        'mihomo_settings': list((root / 'mihomo/settings').glob('*.json')),
        'relay': list((root / 'relay').glob('*.json')),
        'assistant_files': [root / 'assistant/settings.json'],
        'gateway': [getattr(api, 'CADDY_CONFIG', Path('/etc/caddy/Caddyfile')), *Path('/etc/caddy/vps-control.d').glob('*')],
    }
    result = {}
    for group, paths in groups.items():
        rows, unavailable = [], 0
        paths = sorted(paths, key=str)
        for index, path in enumerate(paths[:256]):
            try:
                if path.is_symlink(): raise ValueError('symlink')
                stat = path.stat()
                # Filenames label only predefined sources; metadata detects replacements too.
                rows.append([index, path.name, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])
            except FileNotFoundError: rows.append([index, 'absent'])
            except (OSError, ValueError): unavailable += 1
        result[group] = {'revision': hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest() if not unavailable else None,
                         'method': 'filesystem_metadata_generation_not_content_hash', 'sources': len(paths[:256]),
                         'unavailable': unavailable, 'truncated': len(paths) > 256}
    return result
