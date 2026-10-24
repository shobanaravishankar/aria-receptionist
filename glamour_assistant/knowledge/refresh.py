"""Refresh the verified fact sheet for the Glamour Day Spa information assistant from the salon's OWN public website (read only). Python standard library only.

    python glamour_assistant/knowledge/refresh.py --out glamour_assistant/knowledge/facts.json          fetch the allow-listed pages, extract, write if everything checks out
    python glamour_assistant/knowledge/refresh.py --check                                              fetch and compare with facts.json; exit 1 if anything changed or failed
    python glamour_assistant/knowledge/refresh.py --from-dir <saved html folder> --out <file>           the same, from pages already saved (no network)

What it guarantees:
  - only the allow-listed https pages of www.warrenglamourdayspanj.com are fetched (no other host, no redirect off-site, a size limit, no cookies, a plain identifying user agent);
  - page text is DATA. Nothing on a page is ever executed, followed or obeyed: text that reads like an instruction to an assistant is dropped from the facts and reported;
  - the fact sheet keeps NO prices (the assistant does not speak them; this keeps a price from ever being attributed to the wrong service) and says what the pages do NOT state;
  - if any required page fails, if the footer facts disagree between pages, or if a category page yields no services, nothing is written: the last verified facts.json stays and the
    problem is reported (exit code 1). A refresh never half-updates.
Every source page is recorded with the sha256 of its extracted text so a change is visible in review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

SITE = "https://www.warrenglamourdayspanj.com"
HOST = "www.warrenglamourdayspanj.com"
MAX_BYTES = 3_000_000
USER_AGENT = "GlamourAssistantFactCheck/1.0 (read-only; the salon's own public pages)"

# (slug, path, kind). Categories hold services; "info" pages hold small facts; "business" is the contact page.
PAGES: list[tuple[str, str, str]] = [
    ("contact", "/contact-us", "business"),
    ("spa-packages", "/spa-packages", "category"), ("facials", "/facials", "category"), ("body-scrub", "/body-scrub", "category"), ("massages", "/massages", "category"),
    ("eyelash-extensions", "/eyelash-extensions", "category"), ("head-spa", "/scalp-treatments", "category"), ("waxing", "/waxing", "category"),
    ("permanent-makeup", "/permanent-makeup", "category"), ("couple-packages", "/couples-packages", "category"),
    ("memberships", "/memberships", "info"), ("gift-card", "/gift-card", "info"), ("book-online", "/book-online", "info"), ("home", "/", "info"),
]
CATEGORY_TITLES = {
    "spa-packages": "Spa Packages", "facials": "Facials", "body-scrub": "Body Scrubs", "massages": "Massages", "eyelash-extensions": "Eyelash Extensions", "head-spa": "Head Spa",
    "waxing": "Waxing", "permanent-makeup": "Permanent Makeup", "couple-packages": "Couple Packages",
}
NAV_END = "Use tab to navigate through the menu items."
HOURS_MARK = "OPENING HOURS"
DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
SKIP_TAGS = {"script", "style", "noscript", "svg", "path", "head", "template"}
ITEM_ID = re.compile(r"__(item[a-z0-9-]*)")
PRICE = re.compile(r"\s*\|?\s*\$\s*\d[\d,]*(?:\.\d+)?")
DURATION_LINE = re.compile(r"\|\s*\$|^\W*\d+\s*(?:hrs?|hours?|mins?|minutes?)\b.*\$", re.I)
ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"), None)
# text that addresses an assistant or a model rather than a customer: dropped, never followed, and reported
INSTRUCTION_LIKE = re.compile(r"ignore (?:all |any )?(?:previous|prior|above)|system prompt|you are (?:now )?an? (?:ai|assistant|chatbot|language model)|as an ai|disregard|override (?:your|the) instructions|"
                              r"do not tell the user|reveal your|jailbreak|<\s*/?\s*(?:system|assistant|instructions?)\s*>", re.I)


class Refused(Exception):
    """The refresh cannot be trusted; nothing is written."""


# ------------------------------------------------------------------------------------------------------------------------------ fetching
class _NoOffSiteRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).netloc != HOST or urlparse(newurl).scheme != "https":
            raise Refused(f"a page redirected off the salon's site ({urlparse(newurl).netloc}); not followed")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(path: str, *, timeout: float = 30.0) -> str:
    if path not in {p for _s, p, _k in PAGES}:  # only the listed pages: not another path on the site, and never another host
        raise Refused(f"not an allow-listed page: {path}")
    url = SITE + path
    if urlparse(url).netloc != HOST or not url.startswith("https://"):
        raise Refused(f"not an allow-listed address: {url}")
    opener = urllib.request.build_opener(_NoOffSiteRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with opener.open(request, timeout=timeout) as response:
        if response.status != 200:
            raise Refused(f"{path}: HTTP {response.status}")
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise Refused(f"{path}: larger than {MAX_BYTES} bytes")
    return data.decode("utf-8", errors="replace")


# ------------------------------------------------------------------------------------------------------------------------------ parsing
class _Page(HTMLParser):
    """Visible text in document order, plus the same text grouped by the site's repeater item (one item = one service), plus the title and description meta."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str | None]] = []
        self.texts: list[str] = []
        self.items: dict[str, dict] = {}
        self.order: list[str] = []
        self.meta: dict[str, str] = {}

    def _item(self) -> str | None:
        for _tag, item in reversed(self.stack):
            if item:
                return item
        return None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and (a.get("name") or a.get("property")):
            self.meta[a.get("name") or a.get("property") or ""] = a.get("content") or ""
        if tag in ("br", "img", "meta", "link", "input", "hr"):
            return
        found = ITEM_ID.search(a.get("id") or "")
        self.stack.append((tag, found.group(1) if found else None))
        if tag == "a" and a.get("href") and self._item():
            self.items.setdefault(self._item(), {"texts": [], "links": []})["links"].append(a["href"])

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if any(tag in SKIP_TAGS for tag, _ in self.stack):
            return
        text = " ".join(data.translate(ZERO_WIDTH).split())
        if not text:
            return
        self.texts.append(text)
        item = self._item()
        if item:
            if item not in self.items:
                self.items[item] = {"texts": [], "links": []}
            if item not in self.order:
                self.order.append(item)
            self.items[item]["texts"].append(text)


def parse_page(html: str) -> dict:
    page = _Page()
    page.feed(html)
    texts = page.texts
    start = texts.index(NAV_END) + 1 if NAV_END in texts else 0
    end = texts.index(HOURS_MARK) if HOURS_MARK in texts else len(texts)
    main = [t for i, t in enumerate(texts[start:end]) if i == 0 or t != texts[start:end][i - 1]]
    footer = texts[end:] if end < len(texts) else []
    return {
        "title": page.meta.get("og:title", ""), "description": page.meta.get("description", ""), "main": main, "footer": footer,
        "items": [{"texts": page.items[k]["texts"], "links": page.items[k]["links"]} for k in page.order],
    }


def footer_facts(footer: list[str]) -> dict:
    """Hours (each day with its range, duplicates counted), address, phones, email, the text-us note, from the footer block every page repeats."""
    ranges = [t for t in footer if re.fullmatch(r"\d{1,2}:\d{2}\s*[AP]M\s*-\s*\d{1,2}:\d{2}\s*[AP]M", t)]
    days = [t for t in footer if t in DAYS]
    hours: dict[str, str] = {}
    if days == DAYS and len(ranges) == 7:
        hours = dict(zip(DAYS, ranges))
    elif days == DAYS and len(ranges) == 1:  # one range under the seven day labels: the site shows a single block for every day
        hours = {d: ranges[0] for d in DAYS}
    address = next((t for t in footer if re.search(r"\b[A-Z]{2}\s+\d{5}\b", t)), "")
    phones = [t for t in footer if re.fullmatch(r"\(\d{3}\)\s*\d{3}-\d{4}", t)]
    email = next((t for t in footer if re.fullmatch(r"[\w.+-]+@[\w-]+\.[\w.]+", t)), "")
    return {"hours": hours, "address": address, "phones": list(dict.fromkeys(phones)), "email": email.lower(),
            "text_note": next((t for t in footer if t.lower().startswith("text us at")), "")}


SAFETY = re.compile(r"trimester|pregnan|women only|men only|only for|not (?:recommended|suitable|advised)|avoid|contraindicat|allerg|sensitiv|consult|doctor|medical|\bage[sd]?\b|years? old|minors?|under \d+|downtime|numbing", re.I)


def join_first_letter(parts: list[str]) -> list[str]:
    """The site sometimes puts a paragraph's first letter in its own span ('T' + 'reat yourself'): join it back."""
    if len(parts) > 1 and len(parts[0]) == 1 and parts[0].isalpha() and parts[0] not in "AI" and parts[1][:1].islower():
        return [parts[0] + parts[1]] + parts[2:]
    return parts


LABELS = re.compile(r"(?<![A-Za-z])((?:What['\u2019]s )?Included?|Includes?|Benefits?|Ideal for|Best for|Recommended for|Please note|Notes?)\s*:", re.I)
LABEL_KEY = {"included": "included", "include": "included", "includes": "included", "what's included": "included", "benefit": "benefits", "benefits": "benefits", "ideal for": "ideal_for",
             "best for": "ideal_for", "recommended for": "ideal_for", "note": "notes", "notes": "notes", "please note": "notes"}


def split_sections(text: str) -> tuple[str, dict[str, str]]:
    """The text before its first label ("What's Included:", "Benefits:", ...) and each labelled part, in the page's order. Nothing is cut: it is the same words, just in their places."""
    text = " ".join(text.split())
    marks = list(LABELS.finditer(text))
    if not marks:
        return text, {}
    sections: dict[str, str] = {}
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        key = LABEL_KEY.get(mark.group(1).lower().replace("\u2019", "'"), "notes")
        value = text[mark.end():end].strip(" ;")
        sections[key] = (sections[key] + " " + value).strip() if key in sections else value
    return text[: marks[0].start()].strip(), sections


def first_sentences(text: str, limit: int = 260) -> str:
    """The opening of a description (about `limit` characters, whole sentences), PLUS any later sentence that states a safety or eligibility fact (a trimester, women only, ...)."""
    text = " ".join(text.split())
    parts = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    used = 0
    for part in parts:
        if out and len(out) + 1 + len(part) > limit:
            break
        out = (out + " " + part).strip()
        used += 1
        if len(out) >= limit * 0.6:
            break
    extra = [part for part in parts[used:] if SAFETY.search(part)]
    if extra:
        out = (out + " " + " ".join(extra)).strip()
    return out[: limit * 2].rstrip()


def strip_prices(line: str) -> str:
    return " ".join(PRICE.sub("", line).strip(" |").split())


def services_from_items(items: list[dict], dropped: list[str]) -> list[dict]:
    services = []
    for item in items:
        texts = [t for t in item["texts"] if t]
        if not texts or texts[0] == "Book Online":
            continue
        title_parts: list[str] = []
        rest: list[str] = []
        for t in texts:
            if not rest and t != "Book Online" and not DURATION_LINE.search(t) and not re.fullmatch(r"\$\s*\d[\d,.]*", t) and len(t) <= 60 and not t.endswith("."):
                title_parts.append(t)
            else:
                rest.append(t)
        if not title_parts:
            continue
        options = [strip_prices(t) for t in rest if DURATION_LINE.search(t)]
        text_parts = [t for t in rest if t != "Book Online" and not DURATION_LINE.search(t) and t.strip("​ ")]
        kept = []
        for t in text_parts:
            if INSTRUCTION_LIKE.search(t):
                dropped.append(t[:80])
            else:
                kept.append(t)
        name = " ".join(title_parts)
        kept = join_first_letter(kept)
        description, sections = split_sections(" ".join(kept)) if kept else ("", {})   # the WHOLE of what the page says about it: nothing is shortened (brevity is the assistant's job when it answers)
        service = {"name": name, "description": description, "options": [o for o in options if o]}
        service.update(sections)
        services.append(service)
    return services


# ------------------------------------------------------------------------------------------------------------------------------ facts
def build_facts(parsed: dict[str, dict], *, retrieved_at: str, previous: dict | None = None) -> tuple[dict, list[str]]:
    problems: list[str] = []
    dropped: list[str] = []
    for slug, _path, _kind in PAGES:
        if slug not in parsed:
            problems.append(f"{slug}: page missing")
    if problems:
        raise Refused("; ".join(problems))

    footers = {slug: footer_facts(p["footer"]) for slug, p in parsed.items()}
    business = footers["contact"]
    for field in ("address", "email", "phones", "hours"):
        if not business[field]:
            raise Refused(f"the contact page does not state {field}")
    for slug, f in footers.items():
        for field in ("address", "email", "phones", "hours"):
            if f[field] and f[field] != business[field]:
                problems.append(f"{slug}: the footer's {field} disagrees with the contact page")
    if problems:
        raise Refused("; ".join(problems))

    categories = []
    for slug, _path, kind in PAGES:
        if kind != "category":
            continue
        page = parsed[slug]
        services = services_from_items(page["items"], dropped)
        if not services:
            raise Refused(f"{slug}: no services found (the page layout may have changed)")
        # everything the page says before its service list (the introduction and notes that apply to every service in it, such as what every facial includes)
        cut = page["main"].index("Our Services") if "Our Services" in page["main"] else len(page["main"])
        intro = [t for t in page["main"][1:cut] if t.strip() and not INSTRUCTION_LIKE.search(t)]
        dropped.extend(t[:80] for t in page["main"][1:cut] if INSTRUCTION_LIKE.search(t))
        categories.append({"slug": slug, "title": CATEGORY_TITLES[slug], "page": SITE + dict((s, p) for s, p, _ in PAGES)[slug], "intro": intro, "services": services})

    memberships = [s["name"] for s in services_from_items(parsed["memberships"]["items"], dropped) if s["name"].lower().startswith("glamour")]
    gift = any("gift card" in t.lower() for t in parsed["gift-card"]["main"] + [parsed["gift-card"]["title"]])
    sources = [{"slug": s, "url": SITE + p, "sha256": hashlib.sha256("\n".join(parsed[s]["main"] + parsed[s]["footer"]).encode("utf-8")).hexdigest()} for s, p, _ in PAGES]
    facts = {
        "format": 2, "salon": "Glamour Day Spa", "retrieved_at": retrieved_at, "source_site": SITE,
        "business": {"address": business["address"], "phones": business["phones"], "email": business["email"], "text_note": business["text_note"], "hours": business["hours"],
                     "online_booking_page": SITE + "/book-online"},
        "categories": categories,
        "membership_packages": memberships,
        "gift_card": "The site sells an eGift Card online." if gift else "",
        "not_stated": ["cancellation policy", "late-arrival policy", "deposits", "age rules", "parking", "staff names", "specific new-client offers", "pregnancy guidance beyond what a service description says"],
        "sources": sources, "dropped_instruction_like_text": sorted(set(dropped)),
    }
    return facts, problems


def refresh(*, from_dir: Path | None, previous: dict | None) -> tuple[dict, list[str]]:
    parsed: dict[str, dict] = {}
    for slug, path, _kind in PAGES:
        if from_dir is not None:
            html = (from_dir / f"{slug}.html").read_text(encoding="utf-8", errors="replace")
        else:
            html = fetch(path)
        parsed[slug] = parse_page(html)
    return build_facts(parsed, retrieved_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), previous=previous)


def comparable(facts: dict) -> dict:
    return {k: v for k, v in facts.items() if k not in ("retrieved_at", "sources")}  # the page hashes alone (layout churn) are not a change in what the assistant says


def summarize_changes(old: dict | None, new: dict) -> list[str]:
    """What a reviewer needs to see: contact facts, hours, services added or removed, and descriptions or lengths that changed. No prices exist in the facts, so none appears."""
    if old is None:
        return ["there are no earlier verified facts: this is the first extraction"]
    lines: list[str] = []
    for field in ("address", "phones", "email", "text_note", "hours", "online_booking_page"):
        if old["business"].get(field) != new["business"].get(field):
            lines.append(f"business {field} changed: {old['business'].get(field)!r} -> {new['business'].get(field)!r}")
    flat = lambda facts: {(c["title"], s["name"]): s for c in facts["categories"] for s in c["services"]}
    before, after = flat(old), flat(new)
    for key in sorted(after.keys() - before.keys()):
        lines.append(f"service added: {key[0]} / {key[1]}")
    for key in sorted(before.keys() - after.keys()):
        lines.append(f"service removed: {key[0]} / {key[1]}")
    for key in sorted(before.keys() & after.keys()):
        for field in sorted((before[key].keys() | after[key].keys()) - {"name"}):
            if before[key].get(field) != after[key].get(field):
                lines.append(f"{field} changed: {key[0]} / {key[1]}")
    old_intro = {c["title"]: c["intro"] for c in old["categories"]}
    for c in new["categories"]:
        if old_intro.get(c["title"]) != c["intro"]:
            lines.append(f"introduction or notes changed: {c['title']}")
    for field in ("membership_packages", "gift_card", "not_stated"):
        if old.get(field) != new.get(field):
            lines.append(f"{field} changed")
    return lines or ["the page text changed in a way that does not alter what the assistant says"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("facts.json"))
    parser.add_argument("--from-dir", type=Path, default=None, help="saved pages named <slug>.html instead of the network")
    parser.add_argument("--check", action="store_true", help="compare with the existing facts and write nothing")
    args = parser.parse_args(argv)
    previous = json.loads(args.out.read_text(encoding="utf-8")) if args.out.is_file() else None
    try:
        facts, _ = refresh(from_dir=args.from_dir, previous=previous)
    except (Refused, OSError) as error:
        print(f"REFRESH FAILED: {error}. The last verified facts were kept.", file=sys.stderr)
        return 1
    changed = previous is None or comparable(previous) != comparable(facts)
    if args.check:
        if changed:
            print("CHANGED: the live pages differ from the verified facts")
            for line in summarize_changes(previous, facts):
                print("  - " + line)
        else:
            print("unchanged")
        return 1 if changed else 0
    if not changed:
        print("unchanged; the verified facts were left as they are")
        return 0
    args.out.write_text(json.dumps(facts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {args.out}: {sum(len(c['services']) for c in facts['categories'])} services in {len(facts['categories'])} categories")
    if facts["dropped_instruction_like_text"]:
        print("NOTE: instruction-like text on a page was dropped:", facts["dropped_instruction_like_text"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
