"""A guard against the showcase leaking the earlier salon's identity: its staff names, its brand, its town and street.

The words are kept here only as one-way digests (first 16 hex characters of SHA-256 of the lowercased word), so this repository's NEW code does not itself restate
the real staff identities it is built to keep out. ``find_leaks`` tokenizes generated text (a prompt, a page, a tool reply) into whole words and reports any that match.
Whole words only: the digest of a short word never matches a fragment of a longer one.
"""

from __future__ import annotations

import hashlib
import re

OLD_IDENTIFIER_DIGESTS = frozenset({
    "b3c60be97d05b419", "4cd2c3633ee4c452", "aae5be5f6474904b", "55579b557896d0ce",
    "fdee430d40bd57de", "40903c59d19feef1", "6bab3007f56e2a91", "1906bc7c801f03c4",
    "e1fc45f7880e0505", "4c0db82499ca9a6d", "601b2c2473811afe", "4e766f099e980c1e",
    "01380046d1d8e216", "adfb4dbc1416d230", "3abd72ec6352d608", "cf4e39ae462a0f95",
    "2aa398443557d902", "ec7922138fb98dc5", "b4e78f1caf934167", "7e729287917e0833",
    "3b80d38f7686a8b5", "256009d9dc7704fc", "d1fee3a146636cbf", "5184a1cdb19a3bc3",
    "6fce328774a32745",
})
_WORD = re.compile(r"[A-Za-z0-9]+")
# a phone number written the usual ways: the showcase has none configured, so none may appear
_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)")


def _digest(word: str) -> str:
    return hashlib.sha256(word.lower().encode("utf-8")).hexdigest()[:16]


def find_leaks(text: str, *, allow_phone: bool = False, allowed: tuple = ()) -> list[str]:
    """Every old identifier or phone-number-looking string in ``text`` (empty when clean). ``allowed`` strings (the configured phone) are not reported."""
    for text_ok in allowed:
        text = text.replace(text_ok, " ")
    found = sorted({w for w in _WORD.findall(text) if _digest(w) in OLD_IDENTIFIER_DIGESTS}, key=str.lower)
    if not allow_phone:
        found += _PHONE.findall(text)
    return found
