"""Codigos de erro, hints e serializacao. Spec §9.

Principio 3 da spec: erros sao instrucoes. `message` descreve o fato, `hint`
prescreve a proxima acao concreta nomeando a tool a chamar, e `details.retryable`
diz se repetir a mesma chamada pode funcionar.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class Code(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    SESSION_UNAVAILABLE = "SESSION_UNAVAILABLE"
    WINDOW_NOT_FOUND = "WINDOW_NOT_FOUND"
    WINDOW_CLOSED = "WINDOW_CLOSED"
    APP_NOT_ALLOWED = "APP_NOT_ALLOWED"
    READ_ONLY_MODE = "READ_ONLY_MODE"
    BLOCKED_ACTION = "BLOCKED_ACTION"
    ELEVATION_REQUIRED = "ELEVATION_REQUIRED"
    REF_NOT_FOUND = "REF_NOT_FOUND"
    STALE_REF = "STALE_REF"
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
    ELEMENT_DISABLED = "ELEMENT_DISABLED"
    ELEMENT_READONLY = "ELEMENT_READONLY"
    ELEMENT_OFFSCREEN = "ELEMENT_OFFSCREEN"
    PATTERN_NOT_SUPPORTED = "PATTERN_NOT_SUPPORTED"
    NOT_SCROLLABLE = "NOT_SCROLLABLE"
    VERIFY_FAILED = "VERIFY_FAILED"
    FOCUS_FAILED = "FOCUS_FAILED"
    TIMEOUT = "TIMEOUT"
    ACTION_RATE_LIMITED = "ACTION_RATE_LIMITED"
    UIA_COM_ERROR = "UIA_COM_ERROR"


DEFAULT_HINTS: dict[Code, str] = {
    Code.INVALID_ARGUMENT: (
        "Check the parameter names, types and allowed values in the tool schema, "
        "then call again."
    ),
    Code.SESSION_UNAVAILABLE: (
        "The desktop session is locked or not interactive. Ask the user to unlock the machine."
    ),
    Code.WINDOW_NOT_FOUND: "Call uia_list_windows to get current window refs.",
    Code.WINDOW_CLOSED: (
        "The window was closed. Call uia_list_windows; the app may need to be reopened."
    ),
    Code.APP_NOT_ALLOWED: (
        "This application is not in the allowlist. Ask the user to add its executable name "
        "to config.toml under [allowlist] processes and restart the server."
    ),
    Code.READ_ONLY_MODE: (
        "Server is in read-only mode; only inspection tools work. "
        "Ask the user to restart it without --read-only."
    ),
    Code.BLOCKED_ACTION: (
        "This shortcut is blocked by policy. Achieve the goal through the UI "
        "(uia_find_elements + uia_click)."
    ),
    Code.ELEVATION_REQUIRED: (
        "The target app runs elevated and is invisible to this server (UIPI). Ask the user to "
        "run the app non-elevated, or to run the server as administrator."
    ),
    Code.REF_NOT_FOUND: (
        "Unknown ref. Call uia_get_tree or uia_find_elements to obtain fresh refs."
    ),
    Code.STALE_REF: (
        "The UI changed. Re-capture with uia_get_tree(window_ref=..., filter='interactive') "
        "and use the new ref."
    ),
    Code.AMBIGUOUS_MATCH: (
        "Multiple elements match. Pick one of details.candidates and call again with that ref."
    ),
    Code.ELEMENT_NOT_FOUND: (
        "No element matched. Try match='contains' with a shorter name, drop control_type, "
        "or call uia_get_tree(filter='interactive') to see what is actually there."
    ),
    Code.ELEMENT_DISABLED: (
        "Something upstream is blocking it — a required field may be empty. Inspect the form "
        "with uia_get_tree(root_ref=..., filter='interactive'), fill the missing input with "
        "uia_set_value, then retry."
    ),
    Code.ELEMENT_READONLY: (
        "This field is read-only. Use uia_get_value to read it; it cannot be written."
    ),
    Code.ELEMENT_OFFSCREEN: "Call uia_scroll(ref=..., direction='into_view') first, then retry.",
    Code.PATTERN_NOT_SUPPORTED: (
        "This control does not support the requested pattern. Try uia_click(action='mouse'), "
        "or uia_send_keys with the application's own shortcut."
    ),
    Code.NOT_SCROLLABLE: "Content fits; nothing to scroll on this axis.",
    Code.VERIFY_FAILED: (
        "The control rejected or reformatted the input. Check details.actual and adjust "
        "(input masks, max length, allowed characters)."
    ),
    Code.FOCUS_FAILED: (
        "Windows refused the focus change. Ask the user to click the window once, then retry."
    ),
    Code.TIMEOUT: (
        "Increase timeout_ms, or inspect the current state with uia_get_tree before retrying."
    ),
    Code.ACTION_RATE_LIMITED: (
        "Too many mutating actions. Wait a few seconds and retry; batch your work."
    ),
    Code.UIA_COM_ERROR: (
        "Unexpected UI Automation failure (see details.hresult). The app may be busy or hung. "
        "Retry once; if it persists, report it to the user."
    ),
}

RETRYABLE: frozenset[Code] = frozenset(
    {
        Code.SESSION_UNAVAILABLE,
        Code.ELEMENT_NOT_FOUND,
        Code.ELEMENT_DISABLED,
        Code.ELEMENT_OFFSCREEN,
        Code.VERIFY_FAILED,
        Code.FOCUS_FAILED,
        Code.TIMEOUT,
        Code.ACTION_RATE_LIMITED,
        Code.UIA_COM_ERROR,
    }
)

# Spec §9.2, mapeamento obrigatorio.
_HRESULT_MAP: dict[int, Code] = {
    0x80040201: Code.STALE_REF,              # UIA_E_ELEMENTNOTAVAILABLE
    0x80040200: Code.PATTERN_NOT_SUPPORTED,  # UIA_E_INVALIDOPERATION
    0x80131505: Code.TIMEOUT,                # UIA_E_TIMEOUT
    0x80070005: Code.ELEVATION_REQUIRED,     # E_ACCESSDENIED
    0x800706BA: Code.WINDOW_CLOSED,          # RPC_S_SERVER_UNAVAILABLE
}


def normalize_hresult(hresult: int) -> int:
    """comtypes entrega HRESULT com sinal. Normaliza para unsigned 32 bits."""
    return hresult & 0xFFFFFFFF


def hresult_to_code(hresult: int) -> Code:
    return _HRESULT_MAP.get(normalize_hresult(hresult), Code.UIA_COM_ERROR)


class ToolError(Exception):
    """Erro estruturado. Toda tool converte qualquer falha nisso."""

    def __init__(
        self,
        code: Code,
        message: str,
        *,
        hint: str | None = None,
        **details: Any,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint if hint is not None else DEFAULT_HINTS[code]
        self.details: dict[str, Any] = dict(details)
        self.details.setdefault("retryable", code in RETRYABLE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code.value,
                "message": self.message,
                "hint": self.hint,
                "details": self.details,
            },
        }

    def __repr__(self) -> str:
        return f"ToolError({self.code.value}: {self.message})"


def from_com_error(exc: Exception, **details: Any) -> ToolError:
    """Converte um comtypes.COMError (ou qualquer coisa com .hresult) em ToolError."""
    raw = getattr(exc, "hresult", None)
    if raw is None:
        return ToolError(Code.UIA_COM_ERROR, f"Unexpected COM failure: {exc}", **details)

    code = hresult_to_code(raw)
    hexed = f"0x{normalize_hresult(raw):08X}"
    text = getattr(exc, "text", None) or str(exc)
    return ToolError(
        code,
        f"UI Automation call failed with {hexed}: {text}",
        hresult=hexed,
        **details,
    )
