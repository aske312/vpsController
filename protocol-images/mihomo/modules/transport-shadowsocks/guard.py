#!/usr/bin/env python3
"""Bound libev error storms and let systemd recover only the affected instance."""
import json
import signal
import subprocess
import sys
import threading
import time


RESOURCE_ERRORS = (b"too many open files", b"cannot allocate memory", b"no buffer space available")


def supervise(command, stop=None, emit=None, monitor_factory=None):
    stop = stop if stop is not None else threading.Event()
    emit = emit if emit is not None else lambda message: print(message, flush=True)
    exhausted = threading.Event()
    configuration_error = threading.Event()
    monitor_stop = threading.Event()
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def monitor_empty_connections():
        monitor = monitor_factory(child.pid)
        warned = False
        while not monitor_stop.wait(5):
            try:
                count = monitor.sweep(stopped=monitor_stop.is_set)
                if count:
                    emit(f"Shadowsocks expired {count} TCP connections without payload")
                warned = False
            except OSError as error:
                # Never replace selective cleanup with a restart of healthy flows.
                # Missing kernel support/permissions must be visible, not silent.
                if not warned and not monitor_stop.is_set():
                    emit(f"Shadowsocks empty-connection cleanup unavailable: {error}")
                warned = True

    def read_output():
        window, sent, dropped = time.monotonic(), 0, 0
        while True:
            # Bound memory even if a child writes without newlines.
            line = child.stdout.readline(4096)
            if not line:
                break
            if b"bind: address already in use" in line.lower():
                configuration_error.set()
                exhausted.set()
                emit("Shadowsocks port conflict; automatic restart disabled until configuration is corrected")
                return
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
    monitor_thread = None
    if monitor_factory is not None:
        monitor_thread = threading.Thread(target=monitor_empty_connections, daemon=True)
        monitor_thread.start()
    try:
        while child.poll() is None and not stop.is_set() and not exhausted.wait(0.1):
            pass
    finally:
        monitor_stop.set()
        if monitor_thread is not None:
            monitor_thread.join(timeout=3)
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
    if configuration_error.is_set():
        return 78
    return 75 if exhausted.is_set() else (child.returncode if child.returncode >= 0 else 1)


def main():
    command = sys.argv[1:]
    if not command:
        return 2
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    from empty_connections import EmptyConnections
    with open(command[command.index("-c") + 1], encoding="utf-8") as source:
        port = int(json.load(source)["server_port"])
    if not 1 <= port <= 65535:
        return 2
    return supervise(command, stop, monitor_factory=lambda pid: EmptyConnections(pid, port))


if __name__ == "__main__":
    sys.exit(main())
