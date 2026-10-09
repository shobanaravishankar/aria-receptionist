"""Synthetic LOCAL HTML for the real-browser smoke test, rebuilt from the stored page structure the other tests already use.

The structure (classes, test ids, depth, positions, the day label and hour axis) comes from the redacted capture in tests/fixtures plus the
made-up staff added by multistaff_pages. Every node is written back as an absolutely positioned element at its recorded box, nested by its
recorded depth, so the REAL capture script run in a REAL browser reproduces the same node list and the real parser reads it. Nothing here is
from any real account; the staff names and ids are fictional.
"""

from __future__ import annotations

import html
import json
from typing import Optional

VOID = {"br", "hr", "img", "input", "meta", "link"}


def _attrs(node: dict) -> str:
    out = []
    if node.get("cls"):
        out.append(f'class="{html.escape(" ".join(node["cls"]))}"')
    if node.get("testid"):
        out.append(f'data-testid="{html.escape(node["testid"])}"')
    if node.get("res"):
        out.append(f'data-resource="{html.escape(node["res"])}"')
    if node.get("role"):
        out.append(f'role="{html.escape(node["role"])}"')
    if node.get("aria"):
        out.append(f'aria-label="{html.escape(node["aria"])}"')
    handled = {"data-testid", "data-resource", "role", "aria-label", "class", "style"}
    for name in node.get("attrs", []):
        if name not in handled and all(c.isalnum() or c in "-_:" for c in name):
            out.append(f'{name}="x"')
    return " ".join(out)


def nodes_to_html(nodes: list[dict]) -> str:
    """Stored-shape nodes (with ``box``) -> nested, absolutely positioned HTML whose captured structure equals ``nodes``."""
    parts: list[str] = []
    stack: list[tuple[int, str, tuple[int, int]]] = []  # (depth, tag, origin x/y of its box)
    for node in nodes:
        depth = node["depth"]
        while stack and stack[-1][0] >= depth:
            _d, tag, _o = stack.pop()
            if tag not in VOID:
                parts.append(f"</{tag}>")
        while (stack[-1][0] if stack else 0) < depth - 1:  # the capture skips zero-size elements, so a depth can jump: bridge it invisibly
            origin = stack[-1][2] if stack else (0, 0)
            parts.append('<div style="position:absolute;left:0;top:0;width:0;height:0">')
            stack.append(((stack[-1][0] if stack else 0) + 1, "div", origin))
        x, y, w, h = node["box"]
        ox, oy = stack[-1][2] if stack else (0, 0)
        tag = node["tag"]
        style = f"position:absolute;left:{x - ox}px;top:{y - oy}px;width:{w}px;height:{h}px;margin:0;padding:0;border:0;box-sizing:border-box"
        text = html.escape(node.get("text") or "")
        attrs = _attrs(node)
        parts.append(f'<{tag} {attrs} style="{style}">' + ("" if tag in VOID else text))
        stack.append((depth, tag, (x, y)))
    while stack:
        _d, tag, _o = stack.pop()
        if tag not in VOID:
            parts.append(f"</{tag}>")
    return "".join(parts)


def roster_panel(items: list[dict]) -> str:
    """The staff filter as the real page mounts it: present but not rendered (zero-size, so the page capture never sees it; ROSTER_JS reads it directly)."""
    rows = []
    for index, item in enumerate(items):
        box_id = f"cb{index}"
        testid = f' data-testid="{html.escape(item["testid"])}"' if item.get("testid") else ""
        label_testid = f' data-testid="{html.escape(item["label_testid"])}"' if item.get("label_testid") else ""
        checked = " checked" if item.get("checked") else ""
        label = f'<label for="{box_id}"{label_testid}>{html.escape(item["name"])}</label>' if item.get("name") is not None else ""
        rows.append(f'<li><input type="checkbox" id="{box_id}"{testid}{checked}>{label}</li>')
    return '<div data-testid="resources-filter-by-staffers" style="display:none"><ul>' + "".join(rows) + "</ul></div>"


def page(nodes: list[dict], roster: Optional[list[dict]], *, loader_ms: int = 0, loader_forever: bool = False) -> str:
    loader = ""
    script = ""
    if loader_ms or loader_forever:
        loader = '<div data-testid="app-loader" style="position:fixed;left:0;top:0;width:10px;height:10px"></div>'
        if not loader_forever:
            script = f"<script>setTimeout(function(){{var l=document.querySelector('[data-testid=\"app-loader\"]'); if(l) l.remove();}}, {int(loader_ms)});</script>"
    panel = roster_panel(roster) if roster is not None else ""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>synthetic calendar</title>"
        "<style>html,body{margin:0;padding:0;background:#fff}</style></head><body>"
        + nodes_to_html(nodes) + panel + loader + script + "</body></html>"
    )


def to_json(value) -> str:
    return json.dumps(value)
