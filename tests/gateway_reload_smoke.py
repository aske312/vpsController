"""Run the real reload regression with the release's checksum-verified Caddy."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'api'))
from runtime_dependencies import candidate_caddy


if __name__ == '__main__':
    with candidate_caddy() as binary:
        subprocess.run([sys.executable, '-m', 'unittest', '-v', 'tests.api.test_gateway_reload'],
                       cwd=ROOT, env={**os.environ, 'PRIVACY_CADDY_BIN': str(binary)}, check=True)
