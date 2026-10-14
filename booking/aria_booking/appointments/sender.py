"""Where an owner alert goes, and the injectable thing that sends it. NO provider is built in: until Shobana chooses one and supplies its credentials, nothing can be sent.

The destination is ONE number from local, git-ignored configuration (``booking/.local/owner_sms.json`` or ``ARIA_OWNER_SMS_TO``). It is never a client's number and never
anything a caller said: the outbox rows carry a body and a dedupe key and no recipient at all, and the dispatcher adds the configured owner number. A number in the
reserved fictional range (the seeded clients' 555-01xx numbers) is refused as a destination, so a fixture can never receive a message.

A sender returns what it KNOWS:
  * ``sent``      the provider accepted it;
  * ``rejected``  the provider definitely did not send it (bad credentials, bad number, refused): safe to try again later, a limited number of times;
  * ``uncertain`` the request may or may not have gone out (a timeout after sending): it is NEVER retried automatically, because a retry could text the owner twice.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Protocol

from ..history.phone import normalize_phone

OWNER_ENV = "ARIA_OWNER_SMS_TO"
OWNER_FILE = "owner_sms.json"
SENT, REJECTED, UNCERTAIN = "sent", "rejected", "uncertain"


class SmsSender(Protocol):
    def send(self, to: str, body: str, *, message_key: str) -> "SendResult":
        """Send ``body`` to ``to``. ``message_key`` is stable for this one message: a provider that supports idempotency should be given it."""


@dataclass(frozen=True)
class SendResult:
    status: str  # sent / rejected / uncertain
    detail: str = ""  # a short reason with no message text, number or credential in it: safe to log and to store


class OwnerDestinationError(ValueError):
    pass


def fictional(phone: str) -> bool:
    """A number in the 555-01xx range North America reserves for fiction (+1 AAA 555 01XX)."""
    return phone.startswith("+1") and len(phone) == 12 and phone[5:9] == "5550" and phone[9] == "1"


def load_owner_destination(local_dir: Path, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The owner's number as +<digits>, from the environment or the local file; None when none is configured. Raises if one is configured but unusable."""
    env = os.environ if env is None else env
    raw = (env.get(OWNER_ENV) or "").strip()
    if not raw:
        path = Path(local_dir) / OWNER_FILE
        if not path.exists():
            return None
        try:
            raw = str(json.loads(path.read_text(encoding="utf-8")).get("to") or "").strip()
        except (OSError, ValueError, AttributeError):
            raise OwnerDestinationError("the owner SMS file is unreadable; it must be JSON with a \"to\" number") from None
        if not raw:
            return None
    phone = normalize_phone(raw)
    if phone is None:
        raise OwnerDestinationError("the owner SMS number is not a complete phone number (include the area code, or a + and the country code)")
    if fictional(phone):
        raise OwnerDestinationError("the owner SMS number is in the reserved fictional range; it must be a real number you control")
    return phone


class NotConfiguredSender:
    """What runs when no provider has been chosen: it sends nothing and says so."""

    def send(self, to: str, body: str, *, message_key: str) -> SendResult:
        return SendResult(REJECTED, "no SMS provider is configured")


class FakeSender:
    """For tests: records every send, answers from a script, and never touches a network."""

    def __init__(self, *script: str):
        self.sent: list[tuple[str, str, str]] = []  # (to, body, message_key), in the order they were attempted
        self._script = list(script)
        self._lock = threading.Lock()

    def send(self, to: str, body: str, *, message_key: str) -> SendResult:
        with self._lock:
            self.sent.append((to, body, message_key))
            outcome = self._script.pop(0) if self._script else SENT
        if outcome == "raise":
            raise RuntimeError("the provider connection failed")
        return SendResult(outcome, "scripted")
