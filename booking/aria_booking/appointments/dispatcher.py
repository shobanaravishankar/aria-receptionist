"""Sending the owner alerts that the store's outbox holds. A step of its own: it can never undo or delay a saved appointment.

The guarantees, each tested with a fake sender:
  * NO text before the commit: the outbox row exists only because the change committed (store.py), and only rows already committed are ever claimed.
  * NO duplicate text from a retry: a row is claimed atomically (pending -> sending) by exactly one dispatcher; a request retried by the model, a second dispatcher or a
    restart finds nothing left to claim. A send whose outcome is unknown (a timeout, an exception, a crash after the claim) is marked ``uncertain`` and is never sent again
    automatically, because sending again could text the owner twice.
  * A failure does NOT roll anything back: the appointment stays saved. A definite refusal by the provider (``rejected``) goes back to ``pending`` and is tried again the next time
    the dispatcher runs, up to ``max_attempts``, then is marked ``failed``. A voice reply never mentions delivery, so a failed text can never be announced as sent.
  * With no destination or no provider configured, nothing is claimed or sent: the rows simply wait, and ``status`` says how many.
  * One recipient only: the configured owner number. Rows carry none.
  * Logging is by outbox id, kind and result: never the message, the number or a name.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

from .sender import REJECTED, SENT, UNCERTAIN, SmsSender

MAX_ATTEMPTS = 3
LEASE_SECONDS = 300  # a row 'sending' for longer than this belongs to a dispatcher that died: its outcome is unknown


@dataclass
class DispatchReport:
    sent: int = 0
    retry: int = 0
    failed: int = 0
    uncertain: int = 0
    skipped: str = ""  # why nothing was attempted, when that is so


class OutboxDispatcher:
    def __init__(self, store, sender: Optional[SmsSender], destination: Optional[str], *, max_attempts: int = MAX_ATTEMPTS, lease_seconds: int = LEASE_SECONDS,
                 log: Callable[[str], None] = lambda message: None):
        self._store, self._sender, self._to = store, sender, destination
        self._max_attempts, self._lease, self._log = max_attempts, lease_seconds, log

    @property
    def configured(self) -> bool:
        return self._sender is not None and bool(self._to)

    def status(self) -> dict:
        """Counts only, safe to print: how many alerts are waiting, sent, uncertain or failed, and whether anything could be sent at all."""
        return {"configured": self.configured, "outbox": self._store.counts()["outbox"]}

    def dispatch(self, limit: int = 10) -> DispatchReport:
        report = DispatchReport()
        if not self.configured:
            report.skipped = "no owner number or no SMS provider is configured"
            return report
        expired = self._store.outbox_expire_sending(self._lease)
        if expired:
            self._log(f"alerts: {expired} earlier send(s) have an unknown outcome and will not be retried")
        for row in self._store.outbox_claim(limit):
            try:
                result = self._sender.send(self._to, row["body"], message_key=row["dedupe_key"])
                status, detail = result.status, result.detail
            except Exception as exc:  # the connection may have failed before or after the request went out: unknown, so never retried
                status, detail = UNCERTAIN, type(exc).__name__
            if status == SENT:
                self._store.outbox_finish(row["outbox_id"], "sent")
                report.sent += 1
            elif status == REJECTED and row["attempts"] < self._max_attempts:
                self._store.outbox_finish(row["outbox_id"], "pending", detail)
                report.retry += 1
            elif status == REJECTED:
                self._store.outbox_finish(row["outbox_id"], "failed", detail)
                report.failed += 1
            else:
                self._store.outbox_finish(row["outbox_id"], "uncertain", detail)
                report.uncertain += 1
            self._log(f"alert {row['kind']} #{row['outbox_id']}: {status}")
        return report


class AlertWorker:
    """Runs the dispatcher on its own thread, woken by a commit (``trigger``), never by a timer: there is no polling. One pass at start picks up anything left from before."""

    def __init__(self, dispatcher: OutboxDispatcher, *, log: Callable[[str], None] = lambda message: None):
        self._dispatcher, self._log = dispatcher, log
        self._wake = threading.Event()
        self._stop = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is None and self._dispatcher.configured:
            self._thread = threading.Thread(target=self._run, name="alert-worker", daemon=True)
            self._thread.start()
            self._wake.set()  # the start-up pass

    def trigger(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            if self._stop:
                return
            try:
                self._dispatcher.dispatch()
            except Exception as exc:  # the worker must outlive any one failure; the rows are still in the outbox
                self._log(f"alerts: dispatch failed ({type(exc).__name__})")
