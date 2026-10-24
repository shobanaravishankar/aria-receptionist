"""Build the Glamour Day Spa assistant page into a NEW folder for deployment: the committed page, its settings (the Retell PUBLIC key and the VOICE agent id, public by design), the approved
portrait if one is supplied, and the host's `_headers`. Python standard library only. It never touches the running Serenity demo or any other folder.

    python glamour_assistant/scripts/build_site.py --out <new empty folder> --public-key public_key_... --voice-agent-id agent_... [--voice-agent-version N] [--portrait <approved image>]

The page is relative-address only, so ONE build serves a draft address and the live one. Nothing secret is accepted here: the chat relay's secret key lives only in the hosting environment.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
PORTRAIT_TYPES = {".png", ".webp", ".jpg", ".jpeg", ".svg"}
MAX_PORTRAIT_BYTES = 600_000
DEFAULTS = {
    "chatApi": "api/chat", "salonPhone": "(908) 668-8828", "bookOnline": "https://www.warrenglamourdayspanj.com/book-online",
    "sdkUrl": "https://cdn.jsdelivr.net/npm/retell-client-js-sdk@3.0.1/+esm",
    "greeting": "Hi, I'm Aria, the virtual assistant for Glamour Day Spa in Warren, NJ. Ask me about our services, hours or location, by typing here or by pressing Talk to Aria.",
}
SALON_SITES = "https://www.warrenglamourdayspanj.com https://warrenglamourdayspanj.com"
# The content-security policy is REPORT-ONLY until the real voice SDK has been exercised in a supervised call (it may need workers or addresses a policy would block); the violations show in the browser console.
HEADERS = f"""/*
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: microphone=(self), camera=(), geolocation=()
  Content-Security-Policy-Report-Only: default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' https: wss:; worker-src 'self' blob:; frame-ancestors 'self' {SALON_SITES}; base-uri 'none'; form-action 'self'; object-src 'none'
/index.html
  Cache-Control: no-cache
/*.js
  Cache-Control: no-cache
/*.css
  Cache-Control: no-cache
/assets/*
  Cache-Control: public, max-age=3600
"""


def build(out: Path, *, public_key: str, voice_agent_id: str, voice_agent_version: int | None, portrait: Path | None) -> list[str]:
    if not public_key.startswith("public_key"):
        raise SystemExit("the browser key must be a Retell PUBLIC key (it starts with public_key); never put the private API key in a page")
    if not re.fullmatch(r"agent_[A-Za-z0-9]{8,}", voice_agent_id):
        raise SystemExit("the voice agent id looks wrong (it starts with agent_)")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty: choose a new folder so nothing existing is overwritten")
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SITE, out, dirs_exist_ok=True)
    current = dict(DEFAULTS)
    current.update(retellPublicKey=public_key, voiceAgentId=voice_agent_id, voiceAgentVersion=voice_agent_version)
    (out / "config.js").write_text(
        "/* Written by scripts/build_site.py. The Retell public key and the voice agent id are public by design; the chat relay holds the secret key on the server. */\n"
        "window.GLAMOUR = " + json.dumps(current, indent=2, ensure_ascii=False) + ";\n", encoding="utf-8", newline="\n")
    written = ["config.js"]
    if portrait is not None:
        if portrait.suffix.lower() not in PORTRAIT_TYPES or not portrait.is_file():
            raise SystemExit("the portrait must be an existing .png, .webp, .jpg or .svg file")
        if portrait.stat().st_size > MAX_PORTRAIT_BYTES:
            raise SystemExit(f"the portrait is {portrait.stat().st_size // 1024} KB: make a web-sized copy first")
        name = "portrait" + portrait.suffix.lower()
        shutil.copyfile(portrait, out / "assets" / name)
        if name != "portrait.svg":
            (out / "assets" / "portrait.svg").unlink()
            html = (out / "index.html").read_text(encoding="utf-8").replace('src="assets/portrait.svg"', f'src="assets/{name}"')
            (out / "index.html").write_text(html, encoding="utf-8", newline="\n")
        written.append(f"assets/{name}")
    (out / "_headers").write_text(HEADERS, encoding="utf-8", newline="\n")
    return written + ["_headers"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--public-key", required=True)
    parser.add_argument("--voice-agent-id", required=True)
    parser.add_argument("--voice-agent-version", type=int, default=None)
    parser.add_argument("--portrait", type=Path, default=None, help="the approved portrait (copied in, so it is published; never commit it)")
    args = parser.parse_args(argv)
    written = build(args.out, public_key=args.public_key, voice_agent_id=args.voice_agent_id, voice_agent_version=args.voice_agent_version, portrait=args.portrait)
    print(f"wrote the page into {args.out}: index.html styles.css app.js {' '.join(written)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
