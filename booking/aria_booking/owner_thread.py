"""One thread that owns a non-thread-safe resource (here: Playwright's sync API, its browser context and its page) and runs work for everyone else.

Playwright for Python is NOT thread-safe: the objects it hands out must only be used from the thread that created them
(https://playwright.dev/python/docs/library#threading). The voice server calls the driver from many threads (one per HTTP request, and a
fresh worker per calendar read), so no caller may touch the browser directly. Instead:

  * every browser operation is a callable submitted to THIS thread, which runs them one at a time, in order (no overlap, ever);
  * a job carries an expiry: a job that has not STARTED by then is never run (an expired queued read is dropped, not executed late);
  * a caller that gives up (its own deadline) marks the job abandoned: if it had not started it is skipped, and if it is running its result
    is discarded, never returned to anyone and never cached by the caller;
  * the resource is created lazily on this thread by the first job and torn down on this thread by ``close()``; ``close()`` is idempotent
    and bounded.

This module knows nothing about Playwright, Booksy or calendars, so the threading rules can be tested with plain callables.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable, Optional

from .driver import DriverError


class RunnerClosed(DriverError):
    """The owner thread has been closed (or never accepted work)."""


class JobExpired(DriverError):
    """The job did not start before its deadline, so it was never run."""


class JobTimedOut(DriverError):
    """The caller stopped waiting. The job may still be running; its result will be discarded."""


class Job:
    def __init__(self, fn: Callable[[], Any], expires_at: float, submitted_at: float):
        self.fn, self.expires_at, self.submitted_at = fn, expires_at, submitted_at
        self.started_at: Optional[float] = None
        self.done = threading.Event()
        self.result: Any = None
        self.error: Optional[BaseException] = None
        self.abandoned = False  # the caller gave up: never start it, and never hand its result to anyone
        self.ran = False

    @property
    def queue_wait(self) -> Optional[float]:
        return None if self.started_at is None else self.started_at - self.submitted_at


_STOP = object()


class OwnerThread:
    def __init__(self, *, name: str = "browser-owner", monotonic: Callable[[], float] = time.monotonic, on_stop: Optional[Callable[[], None]] = None):
        self._name, self._mono, self._on_stop = name, monotonic, on_stop
        self._queue: "queue.Queue[Any]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._closed = False
        self.thread_id: Optional[int] = None
        self.stats = {"run": 0, "expired": 0, "abandoned_skipped": 0, "discarded": 0}

    # ---- submitting work (any thread) ---------------------------------------------------------
    def submit(self, fn: Callable[[], Any], *, expires_in: float) -> Job:
        """Queue ``fn`` for the owner thread. It will not be STARTED after ``expires_in`` seconds from now."""
        with self._lock:
            if self._closed:
                raise RunnerClosed("the browser owner thread is closed")
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
                self._thread.start()
            now = self._mono()
            job = Job(fn, now + max(0.0, expires_in), now)
            self._queue.put(job)
            return job

    def call(self, fn: Callable[[], Any], *, timeout: float) -> Any:
        """Run ``fn`` on the owner thread and wait up to ``timeout`` seconds for its result (queue time included)."""
        job = self.submit(fn, expires_in=timeout)
        return self.wait(job, timeout)

    def wait(self, job: Job, timeout: float) -> Any:
        if not job.done.wait(max(0.0, timeout)):
            job.abandoned = True
            raise JobTimedOut("the browser did not finish in time; its late result will be discarded")
        if job.error is not None:
            raise job.error
        return job.result

    # ---- the owner thread ----------------------------------------------------------------------
    def _loop(self) -> None:
        self.thread_id = threading.get_ident()
        while True:
            item = self._queue.get()
            if item is _STOP:
                break
            job: Job = item
            if job.abandoned:
                self.stats["abandoned_skipped"] += 1
                job.error = JobTimedOut("abandoned before it started")
                job.done.set()
                continue
            if self._mono() > job.expires_at:
                self.stats["expired"] += 1
                job.error = JobExpired("the job's deadline passed before it could start, so it was not run")
                job.done.set()
                continue
            job.started_at = self._mono()
            job.ran = True
            try:
                value = job.fn()
                if job.abandoned:
                    self.stats["discarded"] += 1  # a late result: dropped here, not stored where anyone could pick it up
                else:
                    job.result = value
            except BaseException as exc:  # handed to the waiting caller, never swallowed
                job.error = exc
            finally:
                self.stats["run"] += 1
                job.done.set()
        try:
            if self._on_stop is not None:
                self._on_stop()  # tear the resource down ON this thread, the one that created it
        except Exception:
            pass

    # ---- shutting down ---------------------------------------------------------------------------
    def close(self, timeout: float = 15.0) -> bool:
        """Stop accepting work, finish what is running, tear the resource down on its own thread. Idempotent. True when the thread has exited."""
        with self._lock:
            already = self._closed
            self._closed = True
            thread = self._thread
            if thread is not None and not already:
                self._queue.put(_STOP)
        if thread is None:
            if self._on_stop is not None and not already:
                try:
                    self._on_stop()
                except Exception:
                    pass
            return True
        thread.join(timeout)
        return not thread.is_alive()

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
