"""Phone number normalisation that never invents a digit."""

from __future__ import annotations

import re
from typing import Any, Optional


def normalize_phone(value: Any) -> Optional[str]:
    """A complete phone number as "+<digits>", or None when it is not complete or not understood. Digits are never added or guessed.

    * Formatting (spaces, dashes, dots, brackets) is ignored. Letters (for example an extension) make it not understood.
    * With a leading "+", the caller gave the country code: 8 to 15 digits.
    * Otherwise the salon's country (US, +1) is assumed ONLY for a full ten-digit number, or eleven digits starting with 1; an area code
      never starts with 0 or 1. Seven digits (no area code), nine digits or anything else is not complete.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 40 or re.search(r"[^\d\s().+\-]", text):  # letters (an extension, "five five"), punctuation and symbols: not understood
        return None
    digits = re.sub(r"\D", "", text)
    if text.startswith("+"):
        return "+" + digits if 8 <= len(digits) <= 15 and digits[0] != "0" else None
    if "+" in text:
        return None
    if len(digits) == 10 and digits[0] not in "01" and digits[3] not in "01":
        return "+1" + digits
    if len(digits) == 11 and digits[0] == "1" and digits[1] not in "01" and digits[4] not in "01":
        return "+" + digits
    return None
