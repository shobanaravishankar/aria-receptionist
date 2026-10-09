"""The single owner thread that makes a non-thread-safe browser safe for a multi-threaded voice server. Plain callables only: no Playwright."""

from __future__ import annotations

import threading
import time

import pytest

from aria_booking.driver import DriverError
from aria_booking.owner_thread import JobExpired, JobTimedOut, OwnerThread, RunnerClosed


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_nothing_is_started_until_the_first_job():
    before = threading.active_count()
    runner = OwnerThread()
    assert not runner.alive and threading.active_count() == before
    runner.close()


def test_every_job_runs_on_one_thread_that_is_not_the_caller():
    runner = OwnerThread(name="owner-test")
    ids = {runner.call(threading.get_ident, timeout=5) for _ in range(5)}
    assert len(ids) == 1 and threading.get_ident() not in ids and runner.thread_id in ids
    runner.close()


def test_many_caller_threads_share_the_one_owner_and_never_overlap():
    runner = OwnerThread()
    running, peak, owners = [0], [0], set()
    lock = threading.Lock()

    def job():
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        owners.add(threading.get_ident())
        time.sleep(0.01)
        with lock:
            running[0] -= 1
        return True

    threads = [threading.Thread(target=lambda: runner.call(job, timeout=10)) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1 and len(owners) == 1 and runner.stats["run"] == 12
    runner.close()


def test_one_thread_is_created_however_many_calls_are_made():
    names = []
    runner = OwnerThread(name="owner-count")
    for _ in range(20):
        runner.call(lambda: names.append(threading.current_thread().name), timeout=5)
    assert set(names) == {"owner-count"} and sum(1 for t in threading.enumerate() if t.name == "owner-count") == 1
    runner.close()


def test_jobs_run_in_submission_order():
    runner = OwnerThread()
    gate, order = threading.Event(), []
    first = runner.submit(lambda: gate.wait(5), expires_in=10)
    others = [runner.submit(lambda i=i: order.append(i), expires_in=10) for i in range(5)]
    gate.set()
    for job in (first, *others):
        runner.wait(job, 5)
    assert order == [0, 1, 2, 3, 4]
    runner.close()


def test_an_error_in_a_job_reaches_the_caller_unchanged_and_the_thread_survives():
    runner = OwnerThread()

    class Boom(Exception):
        pass

    def bad():
        raise Boom("x")

    with pytest.raises(Boom):
        runner.call(bad, timeout=5)
    assert runner.call(lambda: 7, timeout=5) == 7
    runner.close()


def test_a_job_that_cannot_start_before_its_deadline_is_never_run():
    clock = Clock()
    runner = OwnerThread(monotonic=clock)
    gate, ran = threading.Event(), []
    blocker = runner.submit(lambda: gate.wait(5), expires_in=100)
    late = runner.submit(lambda: ran.append("late"), expires_in=2)
    clock.now += 10  # the queue was stuck behind the blocker for longer than the late job's life
    gate.set()
    runner.wait(blocker, 5)
    with pytest.raises(JobExpired):
        runner.wait(late, 5)
    assert ran == [] and runner.stats["expired"] == 1
    runner.close()


def test_a_job_abandoned_before_it_starts_is_skipped():
    runner = OwnerThread()
    gate, ran = threading.Event(), []
    blocker = runner.submit(lambda: gate.wait(5), expires_in=100)
    queued = runner.submit(lambda: ran.append("x"), expires_in=100)
    with pytest.raises(JobTimedOut):
        runner.wait(queued, 0.05)  # the caller gave up while it was still queued
    gate.set()
    runner.wait(blocker, 5)
    runner.call(lambda: None, timeout=5)  # by now the abandoned job has been passed over
    assert ran == [] and runner.stats["abandoned_skipped"] == 1
    runner.close()


def test_a_late_result_is_discarded_not_returned_or_stored():
    runner = OwnerThread()
    release = threading.Event()
    job = runner.submit(lambda: (release.wait(5), "LATE")[1], expires_in=100)
    with pytest.raises(JobTimedOut):
        runner.wait(job, 0.05)
    release.set()
    assert job.done.wait(5) and job.result is None and job.error is None
    assert runner.stats["discarded"] == 1
    runner.close()


def test_a_timed_out_caller_does_not_let_the_next_job_overlap_the_running_one():
    runner = OwnerThread()
    release, running, peak = threading.Event(), [0], [0]

    def slow():
        running[0] += 1
        peak[0] = max(peak[0], running[0])
        release.wait(5)
        running[0] -= 1

    stuck = runner.submit(slow, expires_in=100)
    with pytest.raises(JobTimedOut):
        runner.wait(stuck, 0.05)

    def quick():
        running[0] += 1
        peak[0] = max(peak[0], running[0])
        running[0] -= 1
        return "ok"

    follow = runner.submit(quick, expires_in=100)
    time.sleep(0.05)
    assert not follow.done.is_set(), "the next job waits for the stuck one"
    release.set()
    assert runner.wait(follow, 5) == "ok" and peak[0] == 1
    runner.close()


def test_close_tears_down_on_the_owner_thread_once():
    torn = []
    runner = OwnerThread(on_stop=lambda: torn.append(threading.get_ident()))
    owner = runner.call(threading.get_ident, timeout=5)
    assert runner.close() is True and runner.close() is True
    assert torn == [owner], "torn down exactly once, on the thread that created the resource"
    assert not runner.alive


def test_close_before_any_work_still_runs_the_teardown_once():
    torn = []
    runner = OwnerThread(on_stop=lambda: torn.append(1))
    runner.close()
    runner.close()
    assert torn == [1]


def test_nothing_is_accepted_after_close():
    runner = OwnerThread()
    runner.call(lambda: 1, timeout=5)
    runner.close()
    with pytest.raises(RunnerClosed):
        runner.submit(lambda: 1, expires_in=5)
    with pytest.raises(DriverError):
        runner.call(lambda: 1, timeout=5)


def test_close_is_bounded_when_a_job_is_stuck():
    release = threading.Event()
    runner = OwnerThread()
    runner.submit(lambda: release.wait(10), expires_in=100)
    began = time.monotonic()
    assert runner.close(timeout=0.2) is False
    assert time.monotonic() - began < 2
    release.set()


def test_a_teardown_that_fails_does_not_break_close():
    runner = OwnerThread(on_stop=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    runner.call(lambda: 1, timeout=5)
    assert runner.close() is True


def test_queue_wait_is_measured_for_a_job_that_waited():
    runner = OwnerThread()
    gate = threading.Event()
    blocker = runner.submit(lambda: gate.wait(5), expires_in=100)
    waited = runner.submit(lambda: None, expires_in=100)
    time.sleep(0.05)
    gate.set()
    runner.wait(blocker, 5)
    runner.wait(waited, 5)
    assert waited.queue_wait is not None and waited.queue_wait >= 0.04
    runner.close()
