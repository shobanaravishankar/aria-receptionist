"""Settings for the PUBLIC (hosted) backend of the Serenity demo.

Everything comes from the environment (host secrets and variables), is checked once at start, and every refusal names the setting to change. Nothing secret is ever put in a
message, a log line, a health answer or a file in this repository: the Retell key and the tool token are held in memory only and are never part of ``repr``.

Three kinds of value:
  * secrets (host secrets, never in Git or in the page): ARIA_RETELL_API_KEY (Retell signs every tool call with it) and ARIA_RETELL_TOOL_TOKEN (the bearer token the agent's tools send);
  * public settings the page needs (they are visible in any browser by design, and Retell restricts the public key to its allowed domains): ARIA_RETELL_PUBLIC_KEY,
    ARIA_RETELL_AGENT_ID, ARIA_RETELL_AGENT_VERSION;
  * where things are: ARIA_PUBLIC_ORIGIN (this backend's https address), ARIA_SITE_ORIGINS (the websites allowed to read from it and to show its staff calendar in a frame),
    ARIA_STORE_PATH (the appointment file, on a volume that survives restarts), PORT / ARIA_PORT and ARIA_HOST.
"""

from __future__ import annotations

import ipaddress
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Mapping, Optional

from ..demo.secret_store import EnvSecrets, SecretMissing

DEFAULT_PORT = 8080
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}


class HostingError(Exception):
    """A setting is missing or wrong. The message says which, and what to change; it never contains a secret."""


def parse_origin(text: str, *, setting: str, dev: bool = False) -> str:
    """``https://host[:port]`` and nothing else (no path, query, credentials). Plain http and loopback names only in development."""
    value = (text or "").strip().rstrip("/")
    parts = urllib.parse.urlsplit(value)
    host = parts.hostname or ""
    loopback = host in LOOPBACK_NAMES
    if not value or not host or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise HostingError(f"{setting}: '{value}' is not an origin. Use the form https://your-host (no path, no credentials).")
    if parts.scheme not in ("https", "http") or (parts.scheme == "http" and not (dev and loopback)):
        raise HostingError(f"{setting}: '{value}' must be https. A page served over https cannot use an http address (browsers block it), and Retell only calls https tools.")
    if (loopback or host.endswith((".local", ".internal"))) and not dev:
        raise HostingError(f"{setting}: '{value}' is not public. Use the https address the host gave you; this machine's own name cannot be reached from the internet.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved) and not (dev and loopback):
        raise HostingError(f"{setting}: '{value}' is a private address. Use the public https hostname.")
    return f"{parts.scheme}://{host}" + (f":{parts.port}" if parts.port else "")


@dataclass(frozen=True)
class PublicConfig:
    public_origin: str
    site_origins: tuple[str, ...]
    store_path: Path
    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    api_key: str = field(default="", repr=False)
    tool_token: str = field(default="", repr=False)
    public_key: str = field(default="", repr=False)  # public by design, but kept out of logs anyway
    agent_id: str = ""
    agent_version: Optional[int] = None
    avatar_image: str = ""
    showcase_file: str = ""
    serve_site: bool = True
    dev: bool = False

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        """The pages allowed to read the calendar's JSON and to frame its page: the configured websites, and this backend itself (it serves the site too)."""
        return tuple(dict.fromkeys((*self.site_origins, self.public_origin)))

    @property
    def voice_settings(self) -> Optional[dict]:
        """The public settings for the page, or None when the page should say voice is not set up."""
        if not (self.public_key and self.agent_id):
            return None
        settings = {"publicKey": self.public_key, "agentId": self.agent_id}
        settings["agentVersion"] = self.agent_version if self.agent_version is not None else 0
        return settings


def _int(text: str, setting: str, low: int, high: int) -> int:
    try:
        value = int(text)
    except ValueError:
        raise HostingError(f"{setting}: '{text}' is not a number.") from None
    if not low <= value <= high:
        raise HostingError(f"{setting}: {value} is outside {low}-{high}.")
    return value


def load_public_config(env: Mapping[str, str], *, dev: bool = False, overrides: Optional[Mapping[str, object]] = None) -> PublicConfig:
    """Read and check everything. ``overrides`` are command-line values (None means 'not given')."""
    given = {k: v for k, v in (overrides or {}).items() if v not in (None, "", ())}

    def setting(name: str, key: str = "") -> str:
        return str(given.get(key) if key and key in given else (env.get(name) or "")).strip()

    secrets = EnvSecrets(env)
    problems: list[str] = []
    try:
        api_key = secrets.get("retell_api_key")
    except SecretMissing:
        api_key = ""
        problems.append("ARIA_RETELL_API_KEY is not set. Add it as a host SECRET: it is the Retell key that owns the agent, and the server uses it to check that each tool call really came from Retell.")
    try:
        tool_token = secrets.get("tool_token")
    except SecretMissing:
        tool_token = ""
        if not dev:
            problems.append("ARIA_RETELL_TOOL_TOKEN is not set. Add it as a host SECRET: the same bearer token the agent's tools send (see `demo-agent`), so a stranger who finds this address cannot call the tools.")
    try:
        public_origin = parse_origin(setting("ARIA_PUBLIC_ORIGIN", "public_origin"), setting="ARIA_PUBLIC_ORIGIN", dev=dev)
    except HostingError as exc:
        public_origin = ""
        problems.append(str(exc) if setting("ARIA_PUBLIC_ORIGIN", "public_origin") else
                        "ARIA_PUBLIC_ORIGIN is not set. Set it to this backend's own https address (for example https://your-app.fly.dev): the page and the calendar frame use it.")
    sites: list[str] = []
    raw_sites = given.get("site_origins") or [s for s in (env.get("ARIA_SITE_ORIGINS") or "").replace(";", ",").split(",")]
    for item in raw_sites:
        if str(item).strip():
            try:
                sites.append(parse_origin(str(item), setting="ARIA_SITE_ORIGINS", dev=dev))
            except HostingError as exc:
                problems.append(str(exc))
    raw_store = setting("ARIA_STORE_PATH", "store")
    if not raw_store:
        problems.append("ARIA_STORE_PATH is not set. Set it to a file on the host's persistent volume (for example /data/appointments.sqlite3): anywhere else is erased on every restart.")
        store = Path("appointments.sqlite3")
    else:
        store = Path(raw_store)
        if not dev and not (raw_store.startswith("/") or PureWindowsPath(raw_store).is_absolute()):  # a Linux host's path or a Windows one: decided by the text, not by this machine
            problems.append(f"ARIA_STORE_PATH: '{raw_store}' is not an absolute path. Use the full path of a file on the persistent volume, for example /data/appointments.sqlite3.")
    try:
        port = _int(setting("PORT", "port") or setting("ARIA_PORT") or str(DEFAULT_PORT), "PORT", 1, 65535)
    except HostingError as exc:
        port = DEFAULT_PORT
        problems.append(str(exc))
    version_text = setting("ARIA_RETELL_AGENT_VERSION")
    version: Optional[int] = None
    if version_text:
        try:
            version = _int(version_text, "ARIA_RETELL_AGENT_VERSION", 0, 100000)
        except HostingError as exc:
            problems.append(str(exc))
    public_key = setting("ARIA_RETELL_PUBLIC_KEY")
    if public_key and not public_key.startswith("public_key"):
        problems.append("ARIA_RETELL_PUBLIC_KEY must be a Retell PUBLIC key (it starts with public_key). Never put the private API key where a browser can read it.")
        public_key = ""
    if problems:
        raise HostingError("The public backend cannot start:\n  - " + "\n  - ".join(problems))
    return PublicConfig(
        public_origin=public_origin, site_origins=tuple(sites), store_path=store, host=setting("ARIA_HOST", "host") or "0.0.0.0", port=port,
        api_key=api_key, tool_token=tool_token, public_key=public_key, agent_id=setting("ARIA_RETELL_AGENT_ID"), agent_version=version,
        avatar_image=setting("ARIA_AVATAR_IMAGE", "avatar"), showcase_file=setting("ARIA_SHOWCASE_CONFIG", "showcase"),
        serve_site=str(given.get("serve_site", (env.get("ARIA_SERVE_SITE") or "1"))).strip().lower() not in ("0", "false", "no", "off"), dev=dev,
    )
