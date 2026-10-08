"""Structural discovery of the calendar page, with personal data redacted before anything is written.

Why: the Selenium adapter cannot read availability or create appointments until we know the real page
structure. Guessing selectors against a live account is how wrong things get clicked. So we first dump
the page STRUCTURE (tags, class names, roles, geometry, redacted text) for a person to review.

Redaction keeps what is needed to write parsers (times, prices, small numbers, known UI words) and masks
everything else (names, phone numbers, emails, free text) character-by-character. Raw text exists only in
memory; only the redacted form is written, to the git-ignored local evidence directory.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Words that are UI vocabulary, not personal data. Extend as discovery shows more labels.
KEEP_WORDS = {
    w.casefold()
    for w in (
        "Aria Salon Shobs NEW APPOINTMENT SAVE NOT NOW Select service NOTES & INFO Today Calendar Day Week Month "
        "Working staff Staff Time off Walk-in Cancel Reschedule Edit Close Back Next Confirm Delete Add Plus "
        "Mon Tue Wed Thu Fri Sat Sun Monday Tuesday Wednesday Thursday Friday Saturday Sunday "
        "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec min mins h"
    ).split()
}

_KEEP_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\d{1,2}:\d{2}$",  # 15:15
        r"^\d{1,2}(:\d{2})?(am|pm)$",  # 3pm, 3:15pm
        r"^(am|pm)$",
        r"^[$€£]?\d{1,5}([.,]\d{1,2})?$",  # prices / small numbers (NOT long digit runs like phone numbers)
        r"^\d{4}-\d{2}-\d{2}$",
        r"^\d{1,2}/\d{1,2}(/\d{2,4})?$",
        r"^[-–—:/|]+$",
    )
]

_BUSINESS_ID_IN_PATH = re.compile(r"/(\d{3,})(?=/|$)")


def redact_text(text: str) -> str:
    out = []
    for token in (text or "").split():
        core = token.strip(",.;()[]")
        if core.casefold() in KEEP_WORDS or any(p.match(core) for p in _KEEP_PATTERNS):
            out.append(token)
        else:
            out.append(re.sub(r"[A-Za-z0-9]", "x", token))
    return " ".join(out)


def mask_url(url: str) -> str:
    """Keep the site and path shape; hide the business id and any query values."""
    if not url:
        return ""
    base, _, query = url.partition("?")
    base = _BUSINESS_ID_IN_PATH.sub("/<business-id>", base)
    keys = ",".join(sorted({kv.split("=")[0] for kv in query.split("&") if kv})) if query else ""
    return base + (f"?[{keys}]" if keys else "")


def sanitize_nodes(nodes: Iterable[dict[str, Any]], *, limit: int = 4000) -> list[dict[str, Any]]:
    cleaned = []
    for node in list(nodes)[:limit]:
        cleaned.append(
            {
                "tag": node.get("tag"),
                "depth": node.get("depth"),
                "cls": list(node.get("cls") or [])[:6],
                "attrs": sorted(node.get("attrs") or []),  # attribute NAMES only, never values
                "role": node.get("role"),
                "testid": node.get("testid"),
                "aria": redact_text(node["aria"]) if node.get("aria") else None,
                "text": redact_text(node["text"]) if node.get("text") else None,
                "box": [node.get("x"), node.get("y"), node.get("w"), node.get("h")],
            }
        )
    return cleaned


def build_report(url: str, title: str, nodes: Iterable[dict[str, Any]], *, day: str) -> dict[str, Any]:
    sanitized = sanitize_nodes(nodes)
    return {
        "kind": "aria-booksy-structure-discovery",
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "day_requested": day,
        "page": {"url": mask_url(url), "title": redact_text(title)},
        "node_count": len(sanitized),
        "note": "Structure only. Text is redacted; attribute values are not recorded. Local, git-ignored.",
        "nodes": sanitized,
    }


def write_report(evidence_dir: Path, report: dict[str, Any]) -> Path:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = evidence_dir / f"discovery-{stamp}.json"
    path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return path


# Collected in the page. Returns raw text, which Python redacts before anything is stored.
DISCOVERY_JS = r"""
const out = [];
const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
let count = 0;
while (walker.nextNode() && count < 6000) {
  const el = walker.currentNode;
  const r = el.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) continue;
  let depth = 0; for (let p = el; p && p !== document.body; p = p.parentElement) depth++;
  const own = [...el.childNodes].filter(c => c.nodeType === 3).map(c => c.textContent.trim()).filter(Boolean).join(' ');
  out.push({
    tag: el.tagName.toLowerCase(), depth: depth,
    cls: (el.getAttribute('class') || '').split(/\s+/).filter(Boolean).slice(0, 6),
    attrs: el.getAttributeNames().filter(a => a !== 'class' && a !== 'style'),
    role: el.getAttribute('role'), testid: el.getAttribute('data-testid'),
    aria: el.getAttribute('aria-label'), text: own.slice(0, 120),
    x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)
  });
  count++;
}
return out;
"""
