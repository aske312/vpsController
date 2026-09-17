#!/usr/bin/env python3
"""Bound libev error storms and let systemd recover only the affected instance."""
import signal
import subprocess
import sys
import threading
import time


RESOURCE_ERRORS = (b"too many open files", b"cannot allocate memory", b"no buffer space available")


def supervise(command, stop=None, emit=None):
    stop = stop if stop is not None else threading.Event()
    emit = emit if emit is not None else lambda message: print(message, flush=True)
    exhausted = threading.Event()
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def read_output():
        window, sent, dropped = time.monotonic(), 0, 0
        while True:
            # Bound memory even if a child writes without newlines.
            line = child.stdout.readline(4096)
            if not line:
                break
            if any(error in line.lower() for error in RESOURCE_ERRORS):
                exhausted.set()
                emit("Shadowsocks resource exhaustion; stopping this instance for automatic recovery")
                return
            now = time.monotonic()
            if now - window >= 10:
                if dropped:
                    emit(f"Shadowsocks guard suppressed {dropped} log fragments")
                window, sent, dropped = now, 0, 0
            if sent < 20:
                emit(line.decode("utf-8", errors="replace").rstrip())
                sent += 1
            else:
                dropped += 1

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        while child.poll() is None and not stop.is_set() and not exhausted.wait(0.1):
            pass
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
        reader.join(timeout=3)
        child.stdout.close()
    if stop.is_set():
        return 0
    return 75 if exhausted.is_set() else (child.returncode if child.returncode >= 0 else 1)


def main():
    command = sys.argv[1:]
    if not command:
        return 2
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    return supervise(command, stop)


if __name__ == "__main__":
    sys.exit(main())
