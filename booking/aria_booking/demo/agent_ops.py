"""Operator tooling for the demo's SEPARATE Retell test agent and its voice page. Portable and secret-free: nothing here is a key, a token, a machine
path or live configuration; all of that is supplied at run time (environment, an operator-named secret folder, a JSON file kept outside Git).

What each action does (and the hard limits):
  plan          offline. Shows what ``create`` would send (tool URLs, prompt hash, settings) and which secrets/state exist. No network.
  store-secret  Windows only. Encrypts one secret you type (hidden prompt) into your secret folder. Never overwrites.
  create        the ONE-TIME creation of the test agent: refuses without --confirm-create. Writes a 'pending' marker before each Retell call, so an
                uncertain outcome is never retried blindly. Ends with the same read-back as ``verify``.
  update        the ONE change in place: sends the reviewed prompt, tools and opening line to the existing LLM (refuses without --confirm-update; no new
                agent), then runs the same read-back as ``verify``.
  verify        reads the agent and its LLM back from Retell and compares every field that matters with what the reviewed source says it must be.
  preflight     signed requests to the tool server (your loopback address or your tunnel): the three tools answer, an unsigned request is 401 and a
                booking route is 404. Records seconds per call (tool latency, NOT spoken-call latency).
  prepare-web   builds the isolated voice page from the repository's website template plus a PUBLIC-only config, with a simulated-calendar banner.
  call-summary  reads the newest call's TOOL events and timings (no transcript text is kept) to help line up a supervised call.

Nothing here can place a call: the Retell client only accepts an allow-list of paths, and no path that starts, registers or schedules a call is on it.
Creating, calling and paying are the operator's separate, deliberate acts.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

from . import SALON_NAME, SPA_TIMEZONE
from ..showcase.roster import name_of
from .secret_store import DpapiSecrets, SecretMissing, provider_from_environment

BOOKING = Path(__file__).resolve().parents[2]
REPO_ROOT = BOOKING.parent
RETELL_API = "https://api.retellai.com/"
LEAD, BOOKED = name_of("910006"), name_of("910001")  # the scripted first day: LEAD is free at 4 PM, BOOKED is not (both come from the one roster)
TOOL_NAMES = frozenset({"lookup_service", "check_slot", "find_alternatives"})
LIFECYCLE_TOOL_NAMES = TOOL_NAMES | {"find_appointments", "last_completed_visit", "book_appointment", "cancel_appointment", "reschedule_appointment"}
SOURCE_PROMPT = "voice_agent_draft/prompt_demo_calendar.md"
SOURCE_TOOLS = "voice_agent_draft/tools_availability_only.json"
# The two reviewed configurations the operator can hold. 'availability' (the default) is the read-only demo; 'lifecycle' is the SIMULATED appointment lifecycle (find, book, move,
# cancel in the prototype calendar only). Neither has a function for the live Booksy calendar.
MODES = {
    "availability": {"prompt": SOURCE_PROMPT, "tools": SOURCE_TOOLS, "names": TOOL_NAMES, "label": "simulated availability only"},
    "lifecycle": {"prompt": "voice_agent_draft/prompt_demo_lifecycle.md", "tools": "voice_agent_draft/tools_demo_lifecycle.json", "names": LIFECYCLE_TOOL_NAMES,
                  "label": "simulated appointment lifecycle"},
}
LIFECYCLE_BOOKING_NOTE = "Appointments made here exist only in this prototype calendar"
CLOCK_SECTION = (
    "\n\n## Session clock\nCurrent salon date and time: {{current_time_" + SPA_TIMEZONE + "}}. "
    f"The demo uses today's actual date in {SPA_TIMEZONE}. Resolve tomorrow from this date.\n"
)
# The only Retell paths this module can ever call. Nothing that creates a call, a phone call, a batch or a schedule is here.
ALLOWED = (
    ("POST", re.compile(r"^create-retell-llm$")), ("POST", re.compile(r"^create-agent$")),
    ("GET", re.compile(r"^get-agent/[A-Za-z0-9_.-]+$")), ("GET", re.compile(r"^get-retell-llm/[A-Za-z0-9_.-]+$")),
    ("PATCH", re.compile(r"^update-retell-llm/[A-Za-z0-9_.-]+$")),  # the ONE change in place: the recorded LLM's prompt, tools and opening line (see ``update``)
    ("POST", re.compile(r"^v3/list-calls$")), ("GET", re.compile(r"^v2/get-call/[A-Za-z0-9_.-]+$")),
)
CONFIG_KEYS = frozenset({
    "tunnel_url", "agent_name", "voice_id", "model", "model_temperature", "language", "max_call_duration_ms", "data_storage_retention_days",
    "begin_message", "state_dir", "web_dir", "calendar_port", "tools_port", "page_port", "mode",
})
RETIRED_KEYS = frozenset({"template_dir"})  # the page is no longer built from a website template; config files that still name it keep loading


class OperatorError(Exception):
    """A refusal or a failed check. The message never contains a secret value."""


class RetellApiError(OperatorError):
    def __init__(self, path: str, status: Optional[int], definite: bool):
        super().__init__(f"Retell {path}: " + (f"HTTP {status}" if status else "no answer (network error or timeout)"))
        self.path, self.status = path, status
        self.definite = definite  # True: Retell ANSWERED with a client error, so nothing was created by that call


class RetellApi:
    """A minimal Retell client: allow-listed paths only, bodies never logged, errors carry a status and nothing else."""

    def __init__(self, key: Callable[[], str], *, base: str = RETELL_API, opener: Callable[..., Any] = urllib.request.urlopen, timeout: float = 45.0):
        self._key, self._base, self._open, self._timeout = key, base, opener, timeout

    def request(self, path: str, data: Optional[dict] = None, method: Optional[str] = None) -> dict:
        verb = method or ("POST" if data is not None else "GET")
        if not any(v == verb and pattern.match(path) for v, pattern in ALLOWED):
            raise OperatorError(f"this tool does not call Retell's {verb} {path.split('/')[0]}")
        request = urllib.request.Request(
            self._base + path, data=json.dumps(data).encode("utf-8") if data is not None else None, method=verb,
            headers={"Authorization": "Bearer " + self._key(), "Content-Type": "application/json"},
        )
        try:
            with self._open(request, timeout=self._timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:  # the body may echo what was sent (headers included): it is dropped, only the status survives
            raise RetellApiError(path, exc.code, definite=400 <= exc.code < 500) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise RetellApiError(path, None, definite=False) from None


# ---------------------------------------------------------------- configuration


@dataclass(frozen=True)
class OperatorConfig:
    tunnel_url: str = ""
    agent_name: str = "Aria - Simulated Calendar Demo"
    voice_id: str = "retell-Cimo"
    model: str = "gpt-4.1"
    model_temperature: float = 0.2
    language: str = "en-US"
    max_call_duration_ms: int = 300_000
    data_storage_retention_days: int = 7
    # None = UNSET: Retell's model speaks first, following the prompt's 'Your opening line' section, which picks Good morning/afternoon/evening from the spa's local hour
    # at the start of EACH call (an opening message cannot branch on a variable). A string here is a fixed opening message instead.
    begin_message: Optional[str] = None
    state_dir: Path = field(default_factory=lambda: BOOKING / ".local" / "demo-agent")
    web_dir: Optional[Path] = None
    calendar_port: int = 8790
    tools_port: int = 8787
    page_port: int = 5174
    mode: str = "availability"  # or "lifecycle": which reviewed prompt and function set this agent is held to

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise OperatorError("mode must be one of: " + ", ".join(sorted(MODES)))

    @property
    def web_out(self) -> Path:
        return self.web_dir or (self.state_dir / "web")

    @property
    def page_url(self) -> str:
        """The ONE address to open the voice page at. 'localhost', never the numeric loopback address: the Retell public key's allowed-domain list
        names localhost (and the operator's published domain), not 127.0.0.1, so a call from http://127.0.0.1:<port>/ is refused with a domain error."""
        return f"http://localhost:{self.page_port}/"


def validate_tunnel(url: str) -> str:
    """A public https address with nothing else on it. Loopback and private addresses are refused: Retell cannot reach them."""
    text = (url or "").strip()
    if not text:
        raise OperatorError("no tunnel address: set ARIA_DEMO_TUNNEL_URL or tunnel_url in the config file (a public https address of your tunnel to the tool port)")
    parts = urllib.parse.urlsplit(text)
    host = parts.hostname or ""
    if parts.scheme != "https" or not host or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise OperatorError("the tunnel address must look like https://your-host (https, no path, no credentials)")
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        raise OperatorError("the tunnel address must be public: Retell cannot reach localhost")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise OperatorError("the tunnel address must be public: Retell cannot reach a private or loopback address")
    return f"https://{host}" + (f":{parts.port}" if parts.port else "")


def load_config(env: Mapping[str, str], *, file: Optional[str] = None, overrides: Optional[Mapping[str, Any]] = None) -> OperatorConfig:
    """Defaults, then the JSON file (ARIA_DEMO_CONFIG or --config), then the environment, then command-line overrides. Unknown keys are refused."""
    values: dict[str, Any] = {}
    path = file or (env.get("ARIA_DEMO_CONFIG") or "").strip()
    if path:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise OperatorError(f"the config file could not be read ({type(exc).__name__})") from None
        if not isinstance(raw, dict):
            raise OperatorError("the config file must hold one JSON object")
        unknown = sorted(k for k in raw if k not in CONFIG_KEYS and k not in RETIRED_KEYS and not k.startswith("_"))
        if unknown:
            raise OperatorError("unknown config keys: " + ", ".join(unknown))
        values.update({k: v for k, v in raw.items() if k in CONFIG_KEYS})
    if (env.get("ARIA_DEMO_TUNNEL_URL") or "").strip():
        values["tunnel_url"] = env["ARIA_DEMO_TUNNEL_URL"].strip()
    if (env.get("ARIA_DEMO_STATE_DIR") or "").strip():
        values["state_dir"] = env["ARIA_DEMO_STATE_DIR"].strip()
    values.update({k: v for k, v in (overrides or {}).items() if v not in (None, "")})
    for key in ("state_dir", "web_dir"):
        if key in values and values[key] is not None:
            values[key] = Path(values[key])
    try:
        return replace(OperatorConfig(), **values)
    except TypeError as exc:  # a wrongly typed value
        raise OperatorError(f"the configuration is not valid ({exc})") from None


# ---------------------------------------------------------------- what the agent must be (from the reviewed source)


def source_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BOOKING, capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", out.stdout.strip()) else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def expected_prompt(root: Path = BOOKING, mode: str = "availability") -> str:
    text = (root / MODES[mode]["prompt"]).read_text(encoding="utf-8")
    if text.count("## Who you are") != 1:
        raise OperatorError("the reviewed prompt must contain the '## Who you are' section exactly once")
    return text[text.index("## Who you are"):] + CLOCK_SECTION


def expected_tools(tunnel: str, token: str, root: Path = BOOKING, mode: str = "availability") -> list[dict]:
    custom = json.loads((root / MODES[mode]["tools"]).read_text(encoding="utf-8"))["tools"]
    if {t["name"] for t in custom} != MODES[mode]["names"]:
        raise OperatorError("the reviewed tool file must hold exactly the " + ("three read-only" if mode == "availability" else "eight lifecycle") + " functions")
    for tool in custom:
        tool.update(type="custom", url=f"{tunnel}/tools/{tool['name']}", headers={"Authorization": "Bearer " + token}, speak_after_execution=True, args_at_root=False)
    return custom + [{"type": "end_call", "name": "end_call", "description": "End when the caller says goodbye or is finished."}]


def prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- local state (all of it under the git-ignored .local folder)


class State:
    def __init__(self, directory: Path):
        self.dir = Path(directory)

    def path(self, name: str) -> Path:
        return self.dir / name

    def exists(self, name: str) -> bool:
        return self.path(name).exists()

    def read(self, name: str) -> dict:
        return json.loads(self.path(name).read_text(encoding="utf-8"))

    def write(self, name: str, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path(name).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def remove(self, name: str) -> None:
        self.path(name).unlink(missing_ok=True)


# ---------------------------------------------------------------- actions


def plan(config: OperatorConfig, secrets: Any, out: Callable[[str], None]) -> int:
    """Everything ``create`` would do, with no network and no secret read."""
    state = State(config.state_dir)
    try:
        tunnel = validate_tunnel(config.tunnel_url)
    except OperatorError as exc:
        tunnel = f"(not set: {exc})"
    prompt = expected_prompt(mode=config.mode)
    tools = expected_tools(tunnel if tunnel.startswith("https://") else "https://<your-tunnel-host>", "<tool token>", mode=config.mode)
    out("PLAN (nothing is created or contacted)")
    out(f"  mode              : {config.mode} ({MODES[config.mode]['label']})")
    out(f"  agent name        : {config.agent_name}  (a SEPARATE test agent; no existing agent is changed)")
    out(f"  tunnel            : {tunnel}")
    out(f"  functions         : {', '.join(sorted(t['name'] for t in tools if t['type'] == 'custom'))} + end_call; "
        + ("no booking function" if config.mode == "availability" else "they change only the PROTOTYPE calendar; none reaches the live Booksy calendar"))
    out(f"  prompt            : {MODES[config.mode]['prompt']} from '## Who you are' + a session-clock line, {len(prompt)} characters, sha256 {prompt_digest(prompt)}")
    out(f"  settings          : model {config.model}, voice {config.voice_id}, {config.max_call_duration_ms // 1000}s call cap, {config.data_storage_retention_days}-day retention, contact memory off")
    out("  opening           : " + (f"fixed message: {config.begin_message}" if config.begin_message is not None else
                                    f"unset: the agent speaks first from the prompt, 'Good morning/afternoon/evening' by the local hour in {SPA_TIMEZONE} at the start of each call"))
    out(f"  secrets from      : {secrets.describe()}")
    for name in ("retell_api_key", "tool_token", "retell_public_key"):
        out(f"    {name:18}: " + ("available" if secrets.has(name) else "NOT available"))
    out(f"  local state       : {config.state_dir}  (" + (", ".join(sorted(p.name for p in config.state_dir.glob('*') if p.is_file())) or "empty") + ")")
    out(f"  voice page        : prepared in {config.web_out}; open it at {config.page_url} (localhost, not 127.0.0.1)")
    out(f"  source commit     : {source_commit()}")
    out("  to create it      : python -m aria_booking demo-agent create --confirm-create   (one time; this plan did not do it)")
    return 0


def store_secret(name: str, secrets: Any, prompt: Callable[[str], str], out: Callable[[str], None]) -> int:
    if not isinstance(secrets, DpapiSecrets):
        raise OperatorError("store-secret needs ARIA_SECRET_DIR (an encrypted-file folder); otherwise supply the value as an environment variable")
    from .secret_store import NAMES

    if name not in NAMES:
        raise OperatorError("name must be one of: " + ", ".join(NAMES))
    value = prompt(f"{name} (typed hidden, never echoed): ")
    path = secrets.put(name, value)
    out(f"stored {name} encrypted at {path}")
    return 0


def _marker_name(step: str) -> str:
    return f"{step}-create-pending.json"


def _create_once(state: State, step: str, make: Callable[[], dict], keep: Callable[[dict], dict], note: dict) -> dict:
    """Create something at most once. A result file means done; a pending marker without one means 'outcome uncertain': stop and look."""
    result_file = f"{step}.json"
    if state.exists(result_file):
        return state.read(result_file)
    if state.exists(_marker_name(step)):
        raise OperatorError(f"an earlier {step} create has an uncertain outcome: check the Retell account, then delete {_marker_name(step)} or write {result_file} by hand")
    state.write(_marker_name(step), {"started": time.time(), **note})
    try:
        created = make()
    except RetellApiError as exc:
        if exc.definite:
            state.remove(_marker_name(step))  # Retell refused it outright, so nothing exists: a retry is safe
        raise
    state.write(result_file, keep(created))
    return state.read(result_file)


def _check(problems: list[str], label: str, expected: Any, actual: Any) -> None:
    if expected != actual:
        problems.append(label)


def verify(config: OperatorConfig, secrets: Any, api: RetellApi, out: Callable[[str], None]) -> int:
    _read_back(config, secrets, api, out)
    return 0


# What ``update`` must leave exactly as it found it. (The agent's engine version is deliberately not here: a version change is what the binding line reports.)
def _settings(agent: dict, llm: dict) -> dict:
    return {
        "voice_id": agent.get("voice_id"), "language": agent.get("language"), "agent_name": agent.get("agent_name"),
        "max_call_duration_ms": agent.get("max_call_duration_ms"), "data_storage_retention_days": agent.get("data_storage_retention_days"),
        "contact_memory_config": agent.get("contact_memory_config"), "model": llm.get("model"), "model_temperature": llm.get("model_temperature"),
    }


def _read_back(config: OperatorConfig, secrets: Any, api: RetellApi, out: Callable[[str], None]) -> tuple[dict, dict]:
    """Read the agent and its LLM from Retell and compare what matters with the reviewed source. Returns (agent, llm), or raises listing every difference."""
    state = State(config.state_dir)
    if not state.exists("agent.json") or not state.exists("llm.json"):
        raise OperatorError("no created agent is recorded here: run `create --confirm-create` first")
    agent_state, llm_id = state.read("agent.json"), state.read("llm.json")["llm_id"]
    tunnel = validate_tunnel(config.tunnel_url)
    token = secrets.get("tool_token")
    prompt, tools = expected_prompt(mode=config.mode), expected_tools(tunnel, token, mode=config.mode)
    agent, llm = api.request("get-agent/" + agent_state["agent_id"]), api.request("get-retell-llm/" + llm_id)
    problems: list[str] = []
    _check(problems, "the agent does not use the recorded LLM", llm_id, (agent.get("response_engine") or {}).get("llm_id"))
    _check(problems, "the opening line", config.begin_message, llm.get("begin_message"))
    _check(problems, "the agent's name", config.agent_name, agent.get("agent_name"))
    _check(problems, "the call-length cap", config.max_call_duration_ms, agent.get("max_call_duration_ms"))
    _check(problems, "the retention days", config.data_storage_retention_days, agent.get("data_storage_retention_days"))
    memory = agent.get("contact_memory_config") or {}
    _check(problems, "contact memory is not off", (False, False), (memory.get("enable_read"), memory.get("enable_update")))
    _check(problems, "the prompt differs from the reviewed source", prompt, llm.get("general_prompt"))
    live = {t.get("name"): t for t in llm.get("general_tools") or [] if t.get("type") == "custom"}
    _check(problems, "the set of custom functions", sorted(MODES[config.mode]["names"]), sorted(live))
    if "book_slot" in json.dumps(llm.get("general_tools") or []) or "book_slot" in (llm.get("general_prompt") or ""):
        problems.append("a booking function or instruction is present")
    for tool in tools:
        if tool.get("type") != "custom":
            continue
        got = live.get(tool["name"], {})
        for key in ("url", "headers", "parameters", "speak_during_execution", "speak_after_execution", "args_at_root"):
            _check(problems, f"{tool['name']}.{key}", tool.get(key), got.get(key))
        if tool.get("speak_during_execution"):
            _check(problems, f"{tool['name']}.execution_message_description", tool.get("execution_message_description"), got.get("execution_message_description"))
    if not any(t.get("type") == "end_call" for t in llm.get("general_tools") or []):
        problems.append("the end_call function")
    if problems:
        state.remove("configuration-verified.json")
        raise OperatorError("read-back does NOT match the reviewed configuration: " + "; ".join(problems))
    state.write("configuration-verified.json", {
        "verified_at": time.time(), "tools": sorted(live), "booking_enabled": False, "mode": config.mode, "prompt_sha256": prompt_digest(prompt),
        "agent_name": agent.get("agent_name"), "source_commit": source_commit(),
    })
    out("Separate simulated-calendar agent and all three read-only functions verified against the reviewed source; no call was started." if config.mode == "availability" else
        "Separate simulated-calendar agent and all eight lifecycle functions verified against the reviewed source (they change only the prototype calendar); no call was started.")
    engine = agent.get("response_engine") or {}
    out(f"Binding: the agent uses the recorded LLM (engine version {engine.get('version', 'not stated, so the latest')}); that LLM is version {llm.get('version')}, "
        f"published: {llm.get('is_published')}. Retell's documentation does not say which version a web call with agent_version {agent.get('version')} uses, "
        "so only a supervised call shows the new prompt is live.")
    return agent, llm


def create(config: OperatorConfig, secrets: Any, api: RetellApi, out: Callable[[str], None], *, confirm: bool) -> int:
    if not confirm:
        raise OperatorError("creating a Retell agent needs the explicit flag --confirm-create (run `plan` first; it creates nothing)")
    tunnel = validate_tunnel(config.tunnel_url)
    token = secrets.ensure_token() if hasattr(secrets, "ensure_token") else secrets.get("tool_token")
    secrets.get("retell_api_key")  # fail early, before anything is written, if the key is missing
    state = State(config.state_dir)
    prompt, tools = expected_prompt(mode=config.mode), expected_tools(tunnel, token, mode=config.mode)
    llm = _create_once(
        state, "llm",
        lambda: api.request("create-retell-llm", {
            "model": config.model, "model_temperature": config.model_temperature, "general_prompt": prompt, "general_tools": tools,
            **({"begin_message": config.begin_message} if config.begin_message is not None else {}),  # unset: the model opens per the prompt
        }, "POST"),
        lambda made: {"llm_id": made["llm_id"]}, {"name": config.agent_name},
    )
    _create_once(
        state, "agent",
        lambda: api.request("create-agent", {
            "agent_name": config.agent_name, "voice_id": config.voice_id, "response_engine": {"type": "retell-llm", "llm_id": llm["llm_id"]},
            "language": config.language, "max_call_duration_ms": config.max_call_duration_ms,
            "data_storage_retention_days": config.data_storage_retention_days, "contact_memory_config": {"enable_read": False, "enable_update": False},
        }, "POST"),
        lambda made: {"agent_id": made["agent_id"], "agent_name": made["agent_name"], "version": made.get("version", 0), "llm_id": llm["llm_id"],
                      "tunnel": tunnel, "mode": MODES[config.mode]["label"], "source_commit": source_commit()},
        {"llm_id": llm["llm_id"]},
    )
    return verify(config, secrets, api, out)


def update(config: OperatorConfig, secrets: Any, api: RetellApi, out: Callable[[str], None], *, confirm: bool) -> int:
    """Bring the EXISTING test agent's LLM in line with the reviewed source, in place: no new agent, no new LLM, no tunnel, no call.

    Sends exactly the reviewed prompt, the three read-only functions (plus end_call) and the opening line to the one recorded LLM, then runs the same read-back as
    ``create``. It is safe to repeat. The previous 'verified' record is removed first, so a stale one can never survive an uncertain result. The tunnel address is
    the recorded one unless a new one is given, which also moves the functions to a new address. Nothing but the LLM is touched: the agent, its voice, model, call
    cap and retention are unchanged (``verify`` reports them).
    """
    if not confirm:
        raise OperatorError("updating the existing agent's LLM needs the explicit flag --confirm-update (run `verify` first: it shows what differs and changes nothing)")
    state = State(config.state_dir)
    if not state.exists("agent.json") or not state.exists("llm.json"):
        raise OperatorError("no created agent is recorded here: run `create --confirm-create` first")
    agent_state, llm_id = state.read("agent.json"), state.read("llm.json")["llm_id"]
    if agent_state.get("llm_id") != llm_id:
        raise OperatorError("the recorded agent and LLM do not belong together; refusing to update")
    tunnel = validate_tunnel(config.tunnel_url or agent_state.get("tunnel", ""))
    token = secrets.get("tool_token")
    secrets.get("retell_api_key")  # fail early if the key is missing
    prompt, tools = expected_prompt(mode=config.mode), expected_tools(tunnel, token, mode=config.mode)
    # Read first (nothing is changed yet): the agent must still point at the recorded LLM, and its other settings are noted so they can be shown unchanged.
    agent_before, llm_before = api.request("get-agent/" + agent_state["agent_id"]), api.request("get-retell-llm/" + llm_id)
    if (agent_before.get("response_engine") or {}).get("llm_id") != llm_id:
        raise OperatorError("the agent no longer uses the recorded LLM; refusing to update (nothing was changed)")
    settings_before = _settings(agent_before, llm_before)
    state.remove("configuration-verified.json")
    try:
        result = api.request(f"update-retell-llm/{llm_id}", {"general_prompt": prompt, "general_tools": tools, "begin_message": config.begin_message}, "PATCH")  # null clears an earlier fixed opening message
    except RetellApiError as exc:
        if exc.definite:
            raise
        raise OperatorError(f"{exc}; the outcome is uncertain: run `verify` to see what the account holds (an update is safe to repeat)") from None
    out(f"LLM updated in place (Retell reports version {result.get('version')}, published: {result.get('is_published')}); no new agent, LLM or tunnel was created.")
    agent, llm = _read_back(replace(config, tunnel_url=tunnel), secrets, api, out)
    changed = sorted(key for key, value in _settings(agent, llm).items() if value != settings_before[key])
    if changed:
        state.remove("configuration-verified.json")
        raise OperatorError("the update changed settings it must not touch: " + ", ".join(changed))
    out("Left unchanged, as found: " + ", ".join(sorted(settings_before)) + ".")
    state.write("agent.json", {**agent_state, "tunnel": tunnel, "source_commit": source_commit()})
    state.write("last-update.json", {"updated_at": time.time(), "prompt_sha256": prompt_digest(prompt), "source_commit": source_commit(), "tunnel": tunnel,
                                     "llm_version": result.get("version"), "llm_published": result.get("is_published")})
    return 0


def sign_body(raw: bytes, key: str, now_ms: int) -> str:
    return f"v={now_ms},d=" + hmac.new(key.encode("utf-8"), raw + str(now_ms).encode("ascii"), hashlib.sha256).hexdigest()


def preflight(config: OperatorConfig, secrets: Any, out: Callable[[str], None], *, base_url: Optional[str] = None,
              opener: Callable[..., Any] = urllib.request.urlopen, clock: Callable[[], float] = time.time) -> int:
    """Signed requests to the tool server. ``base_url`` defaults to the tunnel; use http://127.0.0.1:<port> to check the server before a tunnel exists."""
    base = (base_url or validate_tunnel(config.tunnel_url)).rstrip("/")
    if base_url and not re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$", base) and not base.startswith("https://"):
        raise OperatorError("--base-url must be a loopback address or an https address")
    key, token = secrets.get("retell_api_key"), secrets.get("tool_token")
    tomorrow = (datetime.fromtimestamp(clock(), ZoneInfo(SPA_TIMEZONE)) + timedelta(days=1)).date().isoformat()
    cases = [
        ("lookup_service", {"query": "Signature Facial"}),
        ("check_slot", {"service_id": "massage-deep-tissue-massage-60", "date": tomorrow, "time": "16:00", "staff": LEAD}),
        ("check_slot", {"service_id": "massage-deep-tissue-massage-60", "date": tomorrow, "time": "16:00", "staff": BOOKED}),
        ("check_slot", {"service_id": "facial-signature-facial-60", "date": tomorrow, "time": "16:00"}),
        ("find_alternatives", {"service_id": "massage-deep-tissue-massage-60", "date": tomorrow, "time": "16:00", "staff": BOOKED}),
    ]
    records = []
    extra = list(_lifecycle_cases(tomorrow)) if config.mode == "lifecycle" else []
    for index, (name, args) in enumerate([*cases, *extra]):
        if callable(args):  # a case that needs an earlier answer (the reference of an appointment the lookup returned)
            args = args(records)
        if args.get("confirmed") or args.get("confirmation_token"):
            raise OperatorError("a preflight case must never confirm anything: it only reads and reads back")
        raw = json.dumps({"name": name, "call": {"call_id": f"demo-preflight-{index}"}, "args": args}).encode("utf-8")
        request = urllib.request.Request(f"{base}/tools/{name}", data=raw, headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + token, "X-Retell-Signature": sign_body(raw, key, int(clock() * 1000))})
        began = time.monotonic()
        try:
            with opener(request, timeout=20) as response:
                result = json.load(response)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise OperatorError(f"{name}: the tool server did not answer ({type(exc).__name__})") from None
        records.append({"name": name, "args": args, "seconds": round(time.monotonic() - began, 3), "result": result})
    problems = []
    for record in records:
        if record["name"] != "lookup_service" and record["result"].get("source") != "demo_calendar":
            problems.append(f"a {record['name']} reply does not say it is the demo calendar")
    options = records[4]["result"].get("options")
    if records[4]["result"].get("status") != "alternatives" or not isinstance(options, list) or not options:
        problems.append("find_alternatives offered no alternatives for the busy 4 PM slot")
    elif any(str(option.get("start", "")).startswith(f"{tomorrow}T16:00") for option in options if isinstance(option, dict)):
        problems.append("find_alternatives offered the very slot that is occupied")
    lily, elva = records[1]["result"], records[2]["result"]
    if lily.get("status") != "available" or elva.get("unavailable_reason") != "occupied":
        problems.append(f"the scripted examples ({LEAD} free at 4, {BOOKED} booked at 4) did not hold: run the demo server WITHOUT --today so 'tomorrow' is the real tomorrow")
    if config.mode == "lifecycle":
        problems.extend(_lifecycle_problems(records[len(cases):]))
    for suffix, expected in (("check_slot", 401), ("book_slot", 404)):
        try:
            with opener(urllib.request.Request(f"{base}/tools/{suffix}", data=b"{}"), timeout=15):
                problems.append(f"an unsigned request to {suffix} was accepted" if suffix == "check_slot" else f"a booking route answered ({suffix})")
        except urllib.error.HTTPError as exc:
            if exc.code != expected:
                problems.append(f"{suffix}: expected HTTP {expected}, got {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError):
            raise OperatorError(f"{suffix}: the tool server did not answer") from None
    State(config.state_dir).write("preflight.json", {
        "created_at": clock(), "records": records, "problems": problems, "unsigned_rejected": not any("unsigned" in p for p in problems),
        "booking_route_absent": not any("booking route" in p for p in problems), "paid_call_started": False,
    })
    out(json.dumps({"checks": [{"name": r["name"], "status": r["result"].get("status"), "seconds": r["seconds"]} for r in records], "problems": problems}))
    if problems:
        raise OperatorError("preflight found problems: " + "; ".join(problems))
    return 0


def _lifecycle_cases(tomorrow: str):
    """Extra preflight cases for the lifecycle mode. None confirms anything: a booking or cancellation is only taken to its read-back, which saves nothing."""
    from ..appointments.seed import WILLA

    name, phone = WILLA
    yield "find_appointments", {"phone": phone, "name": name}
    yield "last_completed_visit", {"service_category": "lashes", "phone": phone, "name": name}
    yield "book_appointment", {"service_id": "massage-deep-tissue-massage-60", "date": tomorrow, "time": "16:00", "staff": LEAD, "client_name": "Preflight Check", "phone": "+1 212 555 0100"}
    yield "cancel_appointment", lambda records: {
        "reference": ((records[-3]["result"].get("appointments") or [{}])[0].get("reference") or "AAAAAA"), "phone": phone, "name": name,  # the lookup's first appointment
    }


def _lifecycle_problems(records: list) -> list[str]:
    found, last, book, cancel = (r["result"] for r in records)
    problems = []
    if found.get("status") != "found" or not found.get("appointments"):
        problems.append("the seeded fictional client was not found: start the demo server with --lifecycle so the store is seeded")
    if last.get("status") != "found":
        problems.append("the latest completed visit of the seeded client was not found")
    if book.get("status") != "confirmation_required" or not book.get("confirmation_token"):
        problems.append("the booking read-back did not come back as a read-back")
    if cancel.get("status") != "confirmation_required":
        problems.append("the cancellation read-back did not come back as a read-back")
    for reply in (found, last, book, cancel):
        if reply.get("source") != "demo_calendar":
            problems.append("a lifecycle reply does not say it is the demo calendar")
    return problems


def test_instructions(config: OperatorConfig, tomorrow: str, web_dir: Path) -> str:
    """The generated steps for one supervised voice test. They name the one address that works with the Retell public key and the scripted cases."""
    if config.mode == "lifecycle":
        return lifecycle_instructions(config, tomorrow, web_dir)
    calendar = f"http://localhost:{config.calendar_port}/pro/en-us/9000001/calendar?date={tomorrow}&view=day&staffers=working"
    return "\n".join([
        "SUPERVISED VOICE TEST of the SIMULATED calendar (invented data; booking is disabled; the call uses Retell minutes and is your decision).",
        "",
        "1. Serve the prepared page and leave it running:",
        f"     python -m http.server {config.page_port} --bind 127.0.0.1 --directory {web_dir}",
        "2. Open EXACTLY this address in the browser, then press the voice button and allow the microphone:",
        f"     {config.page_url}",
        f"   Do NOT use http://127.0.0.1:{config.page_port}/ : the Retell public key's allowed domains name localhost (and your published domain), not the numeric",
        "   address, and a call from it fails with a domain error.",
        "3. What Aria sees (the same invented data), for the day after the demo server's start date:",
        f"     {calendar}",
        "",
        f"Scripted cases for tomorrow ({tomorrow}), when the demo server was started today (see DEMO_CALENDAR.md, 'What to ask'):",
        f"  - Is {LEAD} available tomorrow at 4 for a one-hour deep tissue massage?   -> yes, with {LEAD}",
        f"  - What about {BOOKED}?                                                       -> booked at 4; offers her other times",
        f"  - Who does the Microdermabrasion Facial?                                 -> the demo's facial technicians (not {LEAD})",
        f"  - Is {LEAD} available for a Microdermabrasion Facial?                      -> {LEAD} does not do it; offers those who do",
        "  - Is this schedule real?                                                 -> truthful: a prototype with an invented calendar (never said unprompted)",
        f"  The opening is spoken by the agent, not fixed: 'Good morning/afternoon/evening! This is Aria at {SALON_NAME}. ...', the word following the spa's local hour at the start of the call. The page itself still carries the website template's own text and the simulation banner.",
        "",
        "The demo date is fixed when the demo server starts: after midnight Eastern, restart the server (not the tunnel or the agent) for these cases to hold.",
        "",
    ])


def lifecycle_instructions(config: OperatorConfig, tomorrow: str, web_dir: Path) -> str:
    from ..appointments.seed import ANSEL, CEDRIC, WILLA

    calendar = f"http://localhost:{config.calendar_port}/pro/en-us/9000001/calendar?date={tomorrow}&view=day&staffers=working"
    return "\n".join([
        "SUPERVISED VOICE TEST of the SIMULATED appointment lifecycle (invented clients and appointments; the call uses Retell minutes and is your decision).",
        "Appointments you book, move or cancel exist only in the prototype calendar's local file. NO real salon calendar is touched. No text goes to anyone unless you have set up",
        "an owner number AND an SMS provider (neither exists yet), and no text ever goes to a client.",
        "",
        "1. Start the demo server with the lifecycle mode (it seeds the fictional clients below once and keeps every appointment between restarts):",
        "     python -m aria_booking demo-serve --lifecycle",
        "2. Serve the prepared page and leave it running:",
        f"     python -m http.server {config.page_port} --bind 127.0.0.1 --directory {web_dir}",
        "3. Open EXACTLY this address, then press the voice button and allow the microphone:",
        f"     {config.page_url}",
        f"   Do NOT use http://127.0.0.1:{config.page_port}/ (the Retell public key allows localhost, not the numeric address).",
        "4. The calendar page Aria reads and writes (refresh it after each change; it must agree with what she says):",
        f"     {calendar}",
        "",
        "The fictional clients (reserved 555-01xx numbers: they reach no real phone, and nothing is ever sent to them):",
        f"  - {WILLA[0]}, number {WILLA[1][2:5]}-{WILLA[1][5:8]}-{WILLA[1][8:]} ({WILLA[1]}): upcoming waxing (a Tuesday), head spa (a Friday) and massage (a Sunday); past completed lash, massage and facial; a cancelled lash and a missed facial.",
        f"  - {ANSEL[0]}: the SAME household number, one upcoming facial (a Thursday): when you give that number, Aria must ask for the full name to tell the two apart.",
        f"  - {CEDRIC[0]}, number {CEDRIC[1][2:5]}-{CEDRIC[1][5:8]}-{CEDRIC[1][8:]}: no upcoming appointment, one past facial (the 'no upcoming' answer).",
        "  - any other number: not found. Always give the COMPLETE number with the area code.",
        "",
        "What to try (say each in your own words):",
        "  1. 'When is my upcoming waxing appointment?' -> she asks for your full phone number, gives the waxing day first, then briefly the head spa and the massage.",
        "  2. 'What appointments do I have?' -> the three in order, with the technician and local time.",
        "  3. 'When was my last lash appointment?' -> the completed one (not the cancelled one); 'last facial' -> not the missed one; 'last waxing' -> none completed.",
        f"  4. Book: 'A one-hour deep tissue massage tomorrow ({tomorrow}) at 4 with {LEAD}' -> she checks, asks your name and number, reads it back, and only after your yes books it and gives a reference.",
        "     Then refresh the calendar page: the appointment is there. Ask her again for your upcoming appointments: it is listed. Say 'yes' twice or hang up and call again: it is never booked twice.",
        "  5. Cancel: 'I need to cancel my massage' -> she may ask why (you can decline), offers another time if you say you are busy, reads back what she will cancel and cancels only after your yes. The slot is free on the page.",
        "  6. Move: 'Can I move it to Thursday at 2?' -> she reads back old and new time; the original stays if the new time is not free.",
        f"  7. A time that is taken ({BOOKED} at 4 tomorrow) -> she says so and offers real alternatives; she never books it.",
        "  8. Something she cannot answer, or a system problem -> she says what she cannot confirm and gives the salon's contact (a number only once one is configured), never saying she transferred or called back.",
        "  9. 'Is this real?' -> truthful: a prototype with an invented calendar. She never says so unprompted.",
        "",
        "The demo date is fixed when the demo server starts: after midnight Eastern, restart the server (not the tunnel or the agent). Past appointments are marked completed at each start.",
        "",
    ])


def prepare_web(config: OperatorConfig, secrets: Any, out: Callable[[str], None], *, clock: Callable[[], float] = time.time) -> int:
    """The isolated Serenity voice page: built from the showcase source (brand, fictional roster, independent menu) plus a PUBLIC-only config. The earlier salon's
    website files are not used and the repository's website template is never touched."""
    from ..showcase import SHOWCASE
    from ..showcase.site import build_site

    state = State(config.state_dir)
    if not state.exists("agent.json"):
        raise OperatorError("no created agent is recorded here: run `create --confirm-create` first (the page needs its agent id)")
    agent = state.read("agent.json")
    public_key = secrets.get("retell_public_key")
    if not public_key.startswith("public_key"):
        raise OperatorError("the browser key must be a Retell PUBLIC key (it starts with public_key); never put the private API key in a page")
    target = config.web_out
    tomorrow = (datetime.fromtimestamp(clock(), ZoneInfo(SPA_TIMEZONE)) + timedelta(days=1)).date().isoformat()
    settings = {"publicKey": public_key, "agentId": agent["agent_id"], "agentVersion": agent.get("version", 0)}
    showcase = replace(SHOWCASE, booking_note=LIFECYCLE_BOOKING_NOTE) if config.mode == "lifecycle" else SHOWCASE
    build_site(target, showcase=showcase, settings=settings, calendar_port=config.calendar_port, retention_days=config.data_storage_retention_days)
    instructions = state.dir / "test-instructions.txt"
    instructions.write_text(test_instructions(config, tomorrow, target), encoding="utf-8", newline="\n")
    out(f"Isolated {SALON_NAME} voice page prepared in {target} with the prototype label and the separate test agent.")
    out(f"Serve it:  python -m http.server {config.page_port} --bind 127.0.0.1 --directory {target}")
    out(f"Open it at {config.page_url}  (localhost, NOT 127.0.0.1: the Retell public key allows localhost, not the numeric address). Steps: {instructions}")
    return 0


def call_summary(config: OperatorConfig, secrets: Any, api: RetellApi, out: Callable[[str], None]) -> int:
    """The newest call's tool events and timings, to line up a supervised call. No transcript text is stored. (Retell's response shape here follows the
    operator's earlier draft and has not been checked against Retell's documentation by this code.)"""
    state = State(config.state_dir)
    if not state.exists("agent.json"):
        raise OperatorError("no created agent is recorded here")
    agent_id = state.read("agent.json")["agent_id"]
    listing = api.request("v3/list-calls", {"filter_criteria": {"agent": [{"agent_id": agent_id}]}, "sort_order": "descending", "limit": 3}, "POST")
    calls = listing.get("items") or []
    if not calls:
        out("No calls found for the test agent.")
        return 0
    call = api.request("v2/get-call/" + calls[0]["call_id"])
    summary = {k: call.get(k) for k in ("call_status", "start_timestamp", "end_timestamp", "duration_ms", "disconnection_reason")}
    summary["tool_events"] = [
        {k: event.get(k) for k in ("role", "name", "time_sec", "arguments", "content") if k in event}
        for event in call.get("transcript_with_tool_calls", []) if event.get("role") in ("tool_call_invocation", "tool_call_result")
    ]
    state.write("last-call-summary.json", summary)
    out(json.dumps(summary))
    return 0


ACTIONS = ("plan", "store-secret", "create", "update", "verify", "preflight", "prepare-web", "call-summary")


def run(
    action: str, env: Mapping[str, str], out: Callable[[str], None], *, config_file: Optional[str] = None, overrides: Optional[Mapping[str, Any]] = None,
    confirm_create: bool = False, confirm_update: bool = False, secret_name: str = "retell_api_key", base_url: Optional[str] = None, secrets: Any = None,
    api_factory: Optional[Callable[[Any], RetellApi]] = None, prompt: Optional[Callable[[str], str]] = None, opener: Callable[..., Any] = urllib.request.urlopen,
    clock: Callable[[], float] = time.time,
) -> int:
    """Returns 0, or 2 after printing why it refused. Never prints a secret."""
    try:
        config = load_config(env, file=config_file, overrides=overrides)
        secrets = secrets if secrets is not None else provider_from_environment(env)
        factory = api_factory or (lambda s: RetellApi(lambda: s.get("retell_api_key")))
        if action == "plan":
            return plan(config, secrets, out)
        if action == "store-secret":
            import getpass

            return store_secret(secret_name, secrets, prompt or getpass.getpass, out)
        if action == "create":
            return create(config, secrets, factory(secrets), out, confirm=confirm_create)
        if action == "update":
            return update(config, secrets, factory(secrets), out, confirm=confirm_update)
        if action == "verify":
            return verify(config, secrets, factory(secrets), out)
        if action == "preflight":
            return preflight(config, secrets, out, base_url=base_url, opener=opener, clock=clock)
        if action == "prepare-web":
            return prepare_web(config, secrets, out, clock=clock)
        if action == "call-summary":
            return call_summary(config, secrets, factory(secrets), out)
        raise OperatorError("unknown action")
    except (OperatorError, SecretMissing) as exc:
        out(f"refused: {exc}")
        return 2
