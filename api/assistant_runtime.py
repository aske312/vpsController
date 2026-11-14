"""Cross-process admission and bounded NDJSON delivery from the optional runtime."""
from contextlib import contextmanager
from contextvars import copy_context
import fcntl
import hashlib
import json
import os
import signal
import threading
import time

from fastapi import HTTPException
import assistant_jobs


@contextmanager
def admission(api, provider, *, wait_seconds=90):
    directory = api.DATA_DIR / 'assistant' / 'runtime-locks'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    handles = []
    deadline = time.monotonic() + wait_seconds
    try:
        # A provider runs one generation at a time; two providers can run concurrently.
        for names in ([hashlib.sha256(provider.encode()).hexdigest()], ['slot-0', 'slot-1']):
            candidates = [open(directory / (name + '.lock'), 'a+b') for name in names]
            for candidate in candidates: os.chmod(candidate.name, 0o600)
            try:
                while True:
                    acquired = None
                    for candidate in candidates:
                        try:
                            fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            acquired = candidate
                            break
                        except BlockingIOError: pass
                    if acquired:
                        handles.append(acquired)
                        break
                    assistant_jobs.emit({'stage': 'waiting_provider'})
                    if time.monotonic() >= deadline:
                        raise HTTPException(429, 'Провайдер занят. Этот запрос к модели ещё не отправлялся')
                    time.sleep(.5)
            finally:
                for candidate in candidates:
                    if candidate not in handles: candidate.close()
        yield
    finally:
        for handle in reversed(handles): handle.close()


def communicate(process, payload, timeout=255):
    output, failures = [], []
    size = 0

    def exchange():
        nonlocal size
        try:
            process.stdin.write(json.dumps(payload))
            process.stdin.close()
            for line in process.stdout:
                size += len(line)
                if size > 32 * 1024 * 1024: raise ValueError('Runtime output limit')
                value = json.loads(line)
                if value.get('event') == 'progress': assistant_jobs.emit(value)
                else: output.append(value)
        except Exception as exc:
            failures.append(exc)

    context = copy_context()
    thread = threading.Thread(target=lambda: context.run(exchange), daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive() or failures:
        if process.poll() is None:
            if os.name == 'posix': os.killpg(process.pid, signal.SIGKILL)
            else: process.kill()
        process.wait(timeout=5)
        thread.join(5)
        if failures: raise ValueError('Invalid runtime events') from None
        error = HTTPException(504, 'Истёк общий срок выполнения AI. Автоматического повтора не было')
        error.error_code = 'runtime_timeout'
        raise error
    process.wait(timeout=5)
    if len(output) != 1 or not isinstance(output[0], dict): raise ValueError('Missing runtime result')
    return output[0], process.returncode
