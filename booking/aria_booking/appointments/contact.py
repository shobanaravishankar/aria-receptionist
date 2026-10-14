"""The salon-contact fallback: what Aria says when she cannot answer, cannot confirm, or a service fails.

She states briefly what she cannot confirm and gives the CONFIGURED salon name and phone number. She never claims anything was transferred, called back or left as a message:
giving a number is only giving a number. With no number configured she says to contact the salon and gives none (nothing is guessed or invented).

The name and number come from the showcase configuration (``contact_name`` defaults to the brand name; ``contact_phone`` is unset until the owner supplies one). The earlier
salon's template contained two numbers; neither is used, and none is chosen here.
"""

from __future__ import annotations

from typing import Optional

from ..history.phone import normalize_phone

GENERIC = "contact the salon directly"  # the phrase the reviewed tools already say; the phrase below replaces it when a number is configured


def spoken_phone(phone: str) -> str:
    """Digits for a voice, in the groups people say them: +12125550100 becomes '2 1 2, 5 5 5, 0 1 0 0'. Other countries: all digits, spaced."""
    if phone.startswith("+1") and len(phone) == 12:
        digits = phone[2:]
        groups = (digits[:3], digits[3:6], digits[6:])
    else:
        groups = (phone.lstrip("+"),)
    return ", ".join(" ".join(group) for group in groups)


def contact_phrase(showcase) -> Optional[str]:
    """'contact Serenity Spa at 2 1 2, 5 5 5, 0 1 0 0', or None when no number is configured."""
    if not showcase.contact_phone:
        return None
    phone = normalize_phone(showcase.contact_phone)
    if phone is None:
        raise ValueError("the showcase contact_phone is not a complete phone number (include the area code, or a + and the country code)")
    return f"contact {showcase.contact_name or showcase.name} at {spoken_phone(phone)}"
