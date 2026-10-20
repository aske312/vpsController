"""Identity of the Xray build supplied by the application release."""
from pathlib import Path
import re

BUNDLED_BINARY = Path(__file__).with_name('bin') / 'xray'
BUILD_MARKER = 'vpsController-trojan-fragment-v1'


def version(output: str) -> str:
    match = re.search(r'\bv?(\d+\.\d+\.\d+)\b', output)
    return match.group(1) + ('+vps1' if BUILD_MARKER in output else '') if match else ''
