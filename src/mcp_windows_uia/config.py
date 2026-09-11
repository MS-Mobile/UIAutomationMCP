"""Carregamento e validacao de config.toml. Spec §10.1.

A config e lida uma unica vez, no startup, e nunca reescrita em runtime. Isso e
deliberado: permitir recarga viva abriria um caminho de escalonamento de privilegio
(o agente escreve no arquivo e amplia a propria allowlist).
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .errors import Code, ToolError

AllowMode = Literal["allow", "deny_all"]
LogValues = Literal["full", "redacted", "none"]

_DEFAULT_DENY_PROCESSES = ("consent.exe", "credentialuibroker.exe", "logonui.exe")
_DEFAULT_BLOCKED_KEYS = ("{ALT+F4}", "{WIN+L}", "{WIN+R}", "{CTRL+SHIFT+ESC}")


@dataclass(frozen=True, slots=True)
class ServerConfig:
    read_only: bool = False
    hide_denied: bool = False
    allow_coordinate_fallback: bool = True
    max_actions_per_minute: int = 60
    settle_ms: int = 250
    ref_ttl_s: int = 300
    ref_cache_max: int = 5000
    com_timeout_ms: int = 15000


@dataclass(frozen=True, slots=True)
class AllowlistConfig:
    mode: AllowMode = "allow"
    processes: frozenset[str] = frozenset()
    title_patterns: dict[str, re.Pattern[str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DenylistConfig:
    processes: frozenset[str] = frozenset(_DEFAULT_DENY_PROCESSES)
    title_patterns: tuple[re.Pattern[str], ...] = ()


@dataclass(frozen=True, slots=True)
class AuditConfig:
    dir: Path = field(default_factory=lambda: Path.cwd() / "audit")
    retain_days: int = 30
    log_values: LogValues = "redacted"


@dataclass(frozen=True, slots=True)
class KeysConfig:
    blocked: frozenset[str] = frozenset(_DEFAULT_BLOCKED_KEYS)


@dataclass(frozen=True, slots=True)
class Config:
    server: ServerConfig
    allowlist: AllowlistConfig
    denylist: DenylistConfig
    audit: AuditConfig
    keys: KeysConfig
    source_path: Path | None = None


def _bad(msg: str) -> ToolError:
    return ToolError(Code.INVALID_ARGUMENT, msg, hint="Fix config.toml and restart the server.")


def _compile(pattern: str, where: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise _bad(f"Invalid regex in {where}: {pattern!r} ({exc}).") from exc


def _norm_processes(values: Any, where: str) -> frozenset[str]:
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise _bad(f"{where} must be a list of strings.")
    return frozenset(v.strip().lower() for v in values if v.strip())


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise _bad(f"Section [{name}] must be a table.")
    return value


def _normalize_keys(values: Any) -> frozenset[str]:
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise _bad("[keys] blocked must be a list of strings.")
    return frozenset(v.strip().upper().replace(" ", "") for v in values if v.strip())


def load_config(path: str | os.PathLike[str], *, force_read_only: bool = False) -> Config:
    """Le config.toml. Arquivo ausente => defaults com allowlist vazia (falha fechada)."""
    p = Path(path)
    raw: dict[str, Any] = {}
    source: Path | None = None

    if p.is_file():
        try:
            raw = tomllib.loads(p.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise _bad(f"config.toml is not valid TOML: {exc}") from exc
        except OSError as exc:
            raise _bad(f"Could not read config file {p}: {exc}") from exc
        source = p.resolve()

    srv = _section(raw, "server")
    server = ServerConfig(
        read_only=bool(srv.get("read_only", False)) or force_read_only,
        hide_denied=bool(srv.get("hide_denied", False)),
        allow_coordinate_fallback=bool(srv.get("allow_coordinate_fallback", True)),
        max_actions_per_minute=int(srv.get("max_actions_per_minute", 60)),
        settle_ms=int(srv.get("settle_ms", 250)),
        ref_ttl_s=int(srv.get("ref_ttl_s", 300)),
        ref_cache_max=int(srv.get("ref_cache_max", 5000)),
        com_timeout_ms=int(srv.get("com_timeout_ms", 15000)),
    )

    allow = _section(raw, "allowlist")
    mode = allow.get("mode", "allow")
    if mode not in ("allow", "deny_all"):
        raise _bad("[allowlist] mode must be 'allow' or 'deny_all'.")
    allow_titles_raw = allow.get("title_patterns", {})
    if not isinstance(allow_titles_raw, dict):
        raise _bad("[allowlist] title_patterns must be a table of process -> regex.")
    allowlist = AllowlistConfig(
        mode=mode,
        processes=_norm_processes(allow.get("processes", []), "[allowlist] processes"),
        title_patterns={
            str(k).lower(): _compile(str(v), f"[allowlist] title_patterns.{k}")
            for k, v in allow_titles_raw.items()
        },
    )

    deny = _section(raw, "denylist")
    deny_titles_raw = deny.get("title_patterns", [])
    if not isinstance(deny_titles_raw, list):
        raise _bad("[denylist] title_patterns must be a list of regex strings.")
    denylist = DenylistConfig(
        processes=_norm_processes(
            deny.get("processes", list(_DEFAULT_DENY_PROCESSES)), "[denylist] processes"
        ),
        title_patterns=tuple(
            _compile(str(t), "[denylist] title_patterns") for t in deny_titles_raw
        ),
    )

    aud = _section(raw, "audit")
    log_values = aud.get("log_values", "redacted")
    if log_values not in ("full", "redacted", "none"):
        raise _bad("[audit] log_values must be one of: 'full', 'redacted', 'none'.")
    audit_dir_raw = str(aud.get("dir", Path.cwd() / "audit"))
    audit = AuditConfig(
        dir=Path(os.path.expandvars(audit_dir_raw)).expanduser(),
        retain_days=int(aud.get("retain_days", 30)),
        log_values=log_values,
    )

    keys_section = _section(raw, "keys")
    keys = KeysConfig(
        blocked=_normalize_keys(keys_section.get("blocked", list(_DEFAULT_BLOCKED_KEYS)))
    )

    return Config(
        server=server,
        allowlist=allowlist,
        denylist=denylist,
        audit=audit,
        keys=keys,
        source_path=source,
    )
