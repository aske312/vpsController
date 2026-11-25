"""Shell workers use system Python, without the API virtualenv dependencies."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = '''
import builtins, sys, types
from pathlib import Path
original_import = builtins.__import__
def system_import(name, *args, **kwargs):
    if name == 'cryptography' or name.startswith('cryptography.'):
        raise ModuleNotFoundError("No module named 'cryptography'")
    return original_import(name, *args, **kwargs)
builtins.__import__ = system_import
try:
    import fcntl
except ImportError:
    # Windows has no flock; Linux CI uses the real system implementation.
    sys.modules['fcntl'] = types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4, LOCK_UN=8,
                                                flock=lambda *args: None)
sys.path.insert(0, sys.argv[1])
root = Path(sys.argv[2])
'''


class OperationSystemPythonTests(unittest.TestCase):
    def run_worker(self, root, source):
        result = subprocess.run(
            [sys.executable, '-S', '-c', BOOTSTRAP + textwrap.dedent(source),
             str(ROOT / 'api'), str(root)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shell_progress_cli_and_metadata_need_no_api_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_worker(root, '''
                import application_operation as operations
                import os, runpy
                path = root / 'application-action.json'
                identity = 'a' * 32
                os.environ['VPS_CONTROL_OPERATION_ID'] = identity
                sys.argv = ['application_operation.py', str(path), 'update',
                            'running', '21', 'Installing release', operations.timestamp()]
                runpy.run_path(str(Path(operations.__file__)), run_name='__main__')
                current = operations.read(path)
                assert current['id'] == identity
                assert current['progress'] == 21
                assert operations.read(root / 'operations' / (identity + '.json')) == current
            ''')
            value = json.loads((root / 'application-action.json').read_text())
            self.assertEqual(value['state'], 'running')
            self.assertEqual(value['action'], 'update')

    def test_private_records_fail_closed_without_crypto(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_worker(Path(directory), '''
                import application_operation as operations
                path = root / 'assistant' / 'conversations' / 'chat.json'
                try:
                    operations.atomic_json(path, {'message': 'private content'})
                except ImportError:
                    pass
                else:
                    raise AssertionError('Private content written without encryption')
                assert not path.exists()
                assert not (root / 'assistant' / '.privacy-key').exists()
                path.parent.mkdir(parents=True)
                envelope = b'{"private_storage":"nodera-private-v1","ciphertext":"AAAA"}'
                path.write_bytes(envelope)
                try:
                    operations.read(path)
                except operations.OperationConflict:
                    pass
                else:
                    raise AssertionError('Unavailable encrypted record was accepted')
                assert path.read_bytes() == envelope
            ''')
