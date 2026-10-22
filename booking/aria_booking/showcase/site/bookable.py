"""What the page's open-times form offers: the treatments the demo calendar CAN check, by group, with each length and price and the technicians who do it.

Built from the same registry the voice tools use (``demo_registry``), never typed in by hand, so the form cannot offer a treatment the voice would refuse, and a technician is listed for
a treatment only if the calendar's own eligibility says they do it. The data is public and synthetic: names of the fictional team, lengths and example prices. It is embedded in the
page as JSON and carries nothing secret.
"""

from __future__ import annotations

import json
from typing import Optional, Sequence

from ...demo.dataset import ROSTER, demo_services
from ..catalog import SHOWCASE_CATALOG


def _length(minutes: Optional[int]) -> str:
    hours, rest = divmod(minutes or 0, 60)
    return " ".join(p for p in (f"{hours} hr" if hours else "", f"{rest} min" if rest else "") if p)


def bookable_data(groups: Sequence[tuple[str, str]], calendar_origin: str, catalog=SHOWCASE_CATALOG, *, refresh_seconds: int = 5, poll_mode: str = "always") -> dict:
    """``groups`` is (category id, title) in the order the page shows them. Only treatments the demo registry can check appear."""
    pairs = demo_services(ROSTER)
    out = []
    for category, title in groups:
        families: dict[str, list] = {}
        for service, service_category in pairs:
            if service_category != category:
                continue
            item = catalog.get(service.catalog_item)
            families.setdefault(item.family, []).append((item, service))
        if not families:
            continue
        built = []
        for family, entries in families.items():
            entries.sort(key=lambda pair: pair[0].duration_minutes or 0)
            variants = []
            for item, service in entries:
                label = item.variant_label
                length = _length(item.duration_minutes)
                descriptive = label and label != length and not label.endswith(("hour", "minutes", "hours"))
                text = " · ".join(p for p in ((label if descriptive else ""), length, f"${item.price_usd}" if item.price_usd is not None else "") if p)
                variants.append({
                    "service_id": service.service_id, "label": text, "minutes": item.duration_minutes, "price": item.price_usd,
                    "technicians": [s.name for s in ROSTER if service.eligible(s.name, s.staff_id)],
                })
            built.append({"name": family, "variants": variants})
        out.append({"category": category, "title": title, "families": built})
    data = {"calendarOrigin": calendar_origin, "refreshSeconds": refresh_seconds, "groups": out}
    if poll_mode == "call":  # a free host is not asked "has anything changed?" while nobody is on a call: Refresh buttons, one fresh look on opening and one when a call ends
        data["pollMode"] = "call"
    return data


def script_tag(data: dict) -> str:
    """The data as an inline script. '</' is escaped so nothing in the data can end the script element."""
    return "<script>window.SERENITY_BOOKABLE = " + json.dumps(data, ensure_ascii=True).replace("</", "<\\/") + ";</script>"
