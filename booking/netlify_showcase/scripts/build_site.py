"""Build the website bundle for the free Netlify site: the SAME page the Python demo serves (brand, fictional roster, independent menu), pointed at its OWN address for the calendar and the open
times (so there is no cross-origin setup), in "call" polling mode (the page asks the calendar whether anything changed only while a call with Aria is live), with the host's `_headers` file.

It writes only into the folder you name. It never reads or writes the running local demo's web folder. The public voice settings (the Retell PUBLIC key and the agent id) are passed in; they are
public by design (they are in the page's source for anyone to read). Nothing secret is accepted here.

  python netlify_showcase/scripts/build_site.py --out <new empty folder> --origin https://<the site>.netlify.app --public-key public_key_... --agent-id agent_... [--agent-version N]
        [--avatar <approved image>] [--showcase-config <file>]

Run it from the booking folder. Then check the folder holds index.html, styles.css, voice.js, views.js, site.js, demo-config.js, _headers and assets/.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aria_booking.showcase.config import resolve_showcase  # noqa: E402
from aria_booking.showcase.site import build_site  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="a NEW folder to write the bundle into (never the running demo's web folder)")
    parser.add_argument("--origin", required=True, help="this site's own https address, no path (the calendar and open times are read from it)")
    parser.add_argument("--public-key", required=True, help="the Retell PUBLIC key (starts with public_key); never the private API key")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--agent-version", type=int, default=0)
    parser.add_argument("--avatar", default=None, help="an approved square image to use as the avatar (copied into the bundle, so it is published)")
    parser.add_argument("--showcase-config", default=None, help="a showcase config file with the salon's number and address, if one has been supplied")
    args = parser.parse_args(argv)
    if not args.public_key.startswith("public_key"):
        print("the browser key must be a Retell PUBLIC key (it starts with public_key); never put the private API key in a page", file=sys.stderr)
        return 2
    parsed = urlparse(args.origin)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/") or parsed.query or parsed.params:
        print("--origin must be an https address with no path, for example https://your-site.netlify.app", file=sys.stderr)
        return 2
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        print(f"{out} is not empty: choose a new folder so nothing existing is overwritten", file=sys.stderr)
        return 2
    showcase = resolve_showcase(path=args.showcase_config, lifecycle=True)
    written = build_site(
        out, showcase=showcase, settings={"publicKey": args.public_key, "agentId": args.agent_id, "agentVersion": args.agent_version}, avatar=args.avatar or None,
        origin=f"{parsed.scheme}://{parsed.netloc}", static_host=True, poll_mode="call",
    )
    names = sorted(p.name for p in written if p.parent == out)
    print(f"wrote {len(written)} files into {out}: {', '.join(names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
