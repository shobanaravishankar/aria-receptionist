"""Where the demo operator gets its three secrets. None of them is ever stored in, or read from, this repository.

  retell_api_key     the Retell private API key (also the signing key the tool server verifies)
  tool_token         a bearer token the tool server additionally requires from Retell
  retell_public_key  the browser-safe Retell PUBLIC key the voice page needs (kept out of git anyway)

Two providers, chosen by configuration, never by guessing:
  * ``EnvSecrets``   (default, portable): ARIA_RETELL_API_KEY, ARIA_RETELL_TOOL_TOKEN, ARIA_RETELL_PUBLIC_KEY in the process environment.
  * ``DpapiSecrets`` (Windows, optional): files ``<name>.dpapi`` in a directory YOU name (ARIA_SECRET_DIR), each encrypted with Windows' per-user
    credential protection, so a key is never on disk in plaintext. The directory is outside the repository or under the git-ignored ``.local``.

Values are returned to the caller and never printed, logged or placed in an exception message.
"""

from __future__ import annotations

import os
import secrets as _secrets
import sys
from pathlib import Path
from typing import Mapping, Optional

NAMES = {
    "retell_api_key": "ARIA_RETELL_API_KEY",
    "tool_token": "ARIA_RETELL_TOOL_TOKEN",
    "retell_public_key": "ARIA_RETELL_PUBLIC_KEY",
}
FILES = {"retell_api_key": "retell-api-key.dpapi", "tool_token": "retell-tool-token.dpapi", "retell_public_key": "retell-public-key.dpapi"}


class SecretMissing(Exception):
    """A required secret has not been supplied. The message names WHERE to supply it, never a value."""


class EnvSecrets:
    def __init__(self, environ: Optional[Mapping[str, str]] = None):
        self._env = os.environ if environ is None else environ

    def get(self, name: str) -> str:
        value = (self._env.get(NAMES[name]) or "").strip()
        if not value:
            raise SecretMissing(f"{NAMES[name]} is not set in the environment")
        return value

    def has(self, name: str) -> bool:
        return bool((self._env.get(NAMES[name]) or "").strip())

    def describe(self) -> str:
        return "environment variables " + ", ".join(NAMES.values())


def _blob():
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    return ctypes, wintypes, Blob


def dpapi_crypt(raw: bytes, encrypt: bool) -> bytes:
    """Windows per-user credential protection (CryptProtectData / CryptUnprotectData). Windows only."""
    if sys.platform != "win32":
        raise SecretMissing("Windows credential protection is only available on Windows; use the environment-variable provider")
    ctypes, wintypes, Blob = _blob()
    buf = ctypes.create_string_buffer(raw)
    src = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    dst = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if encrypt:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        label = "Aria demo secret"
    else:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        label = None
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(src), label, None, None, None, 1, ctypes.byref(dst)):
        raise SecretMissing("Windows credential protection could not process that file (wrong user or a damaged file)")
    try:
        return ctypes.string_at(dst.data, dst.size)
    finally:
        ctypes.memset(dst.data, 0, dst.size)
        kernel.LocalFree(dst.data)
        ctypes.memset(buf, 0, len(raw))


class DpapiSecrets:
    def __init__(self, directory: Path, *, crypt=dpapi_crypt):
        self._dir, self._crypt = Path(directory), crypt

    def _path(self, name: str) -> Path:
        return self._dir / FILES[name]

    def get(self, name: str) -> str:
        path = self._path(name)
        if not path.exists():
            raise SecretMissing(f"{FILES[name]} is not in the secret directory (store it with: python -m aria_booking demo-agent store-secret {name})")
        return self._crypt(path.read_bytes(), False).decode("utf-8").strip()

    def has(self, name: str) -> bool:
        return self._path(name).exists()

    def put(self, name: str, value: str, *, overwrite: bool = False) -> Path:
        """Encrypt and store a secret. Refuses to replace an existing one unless asked."""
        if not value.strip():
            raise SecretMissing("an empty secret was not stored")
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(name)
        with path.open("wb" if overwrite else "xb") as target:  # 'x': an existing secret is never silently replaced
            target.write(self._crypt(value.strip().encode("utf-8"), True))
        return path

    def ensure_token(self, name: str = "tool_token") -> str:
        """The tool token, generated once (random, 256 bits) if it does not exist yet."""
        if not self.has(name):
            self.put(name, _secrets.token_urlsafe(32))
        return self.get(name)

    def describe(self) -> str:
        return f"encrypted files in {self._dir}"


def provider_from_environment(environ: Optional[Mapping[str, str]] = None):
    """DPAPI files when ARIA_SECRET_DIR is set, otherwise environment variables. Never a silent mix."""
    env = os.environ if environ is None else environ
    folder = (env.get("ARIA_SECRET_DIR") or "").strip()
    return DpapiSecrets(Path(folder)) if folder else EnvSecrets(env)


def serve_credentials(environ: Optional[Mapping[str, str]] = None, provider=None) -> tuple[str, str]:
    """(signing key, bearer token) for ``demo-serve``, from the same provider the operator uses, so the server and the agent always agree. Either is ''
    when not available. With encrypted files the token is generated once if it does not exist yet; with environment variables nothing is ever created."""
    provider = provider if provider is not None else provider_from_environment(environ)
    try:
        key = provider.get("retell_api_key")
    except SecretMissing:
        return "", ""  # no key, so the command refuses: nothing is generated or written on the way to a refusal
    try:
        token = provider.ensure_token() if hasattr(provider, "ensure_token") else provider.get("tool_token")
    except SecretMissing:
        token = ""
    return key, token
