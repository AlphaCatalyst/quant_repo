"""Machine-wide cap on concurrent evaluations; each one holds the full dev panel in memory."""

import fcntl
import os
import time
from contextlib import contextmanager

from alphasieve.config import Settings


@contextmanager
def evaluation_slot(settings: Settings, poll_s: float = 1.0):
    slots = max(1, int(os.environ.get("ALPHASIEVE_EVAL_SLOTS", "2")))
    lock_dir = settings.hot_root / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    while True:
        for i in range(slots):
            handle = open(lock_dir / f"eval-slot-{i}.lock", "w")  # noqa: SIM115
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                continue
            try:
                yield i
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
                handle.close()
            return
        time.sleep(poll_s)
