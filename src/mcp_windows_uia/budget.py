"""Orcamento de resposta: limites, truncamento e cursores. Spec §6.1.

Principio 2 da spec: orcamento de contexto e recurso escasso. Os tetos aqui sao
duros — pedir mais do que o teto nao aumenta o teto, o servidor corta e sinaliza.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from dataclasses import dataclass

from .errors import Code, ToolError

HARD_MAX_NODES = 1500
HARD_MAX_DEPTH = 40
HARD_MAX_CHILDREN = 500
HARD_MAX_CHARS = 40000
NAME_LIMIT = 120
CURSOR_TTL_S = 120.0

DEFAULT_MAX_NODES = 200
DEFAULT_MAX_DEPTH = 12
DEFAULT_MAX_CHILDREN = 30
DEFAULT_MAX_CHARS = 6000

DEPTH_HINT = (
    "Stopped at max_depth before the tree ended. In Electron and WebView2 apps the real "
    "content usually sits deeper than the default: retry with a larger max_depth, and "
    "keep filter='interactive' to stay within budget."
)

TRUNCATION_HINT = (
    "Response truncated. Narrow with filter='interactive', a smaller max_depth, root_ref, "
    "or call uia_find_elements instead of dumping the tree."
)


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


@dataclass(frozen=True, slots=True)
class Limits:
    max_nodes: int
    max_depth: int
    max_children_per_node: int

    @classmethod
    def build(
        cls,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_children_per_node: int = DEFAULT_MAX_CHILDREN,
    ) -> Limits:
        """Aplica os tetos duros da spec. Pedir mais que o teto nao aumenta o teto."""
        return cls(
            max_nodes=clamp(max_nodes, 1, HARD_MAX_NODES),
            max_depth=clamp(max_depth, 1, HARD_MAX_DEPTH),
            max_children_per_node=clamp(max_children_per_node, 1, HARD_MAX_CHILDREN),
        )


def truncate_name(name: str | None, limit: int = NAME_LIMIT) -> str:
    if not name:
        return ""
    if len(name) <= limit:
        return name
    return name[: limit - 1] + "…"


def truncate_text(text: str, *, max_chars: int, offset: int = 0) -> tuple[str, bool, int | None]:
    """Retorna (fatia, truncado, next_offset)."""
    limite = clamp(max_chars, 1, HARD_MAX_CHARS)
    fatia = text[offset : offset + limite]
    fim = offset + len(fatia)
    truncado = fim < len(text)
    return fatia, truncado, (fim if truncado else None)


def encode_cursor(
    window_ref: str, *, tree_version: int, position: int, now: float | None = None
) -> str:
    payload = {
        "w": window_ref,
        "v": tree_version,
        "p": position,
        "t": time.time() if now is None else now,
    }
    crua = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(crua).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, *, now: float | None = None) -> tuple[str, int, int]:
    """Retorna (window_ref, tree_version, position). INVALID_ARGUMENT se invalido."""
    agora = time.time() if now is None else now
    try:
        preenchido = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(preenchido.encode("ascii")))
        window_ref = str(payload["w"])
        tree_version = int(payload["v"])
        position = int(payload["p"])
        emitido = float(payload["t"])
    except (KeyError, ValueError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise ToolError(
            Code.INVALID_ARGUMENT,
            "The cursor is malformed and cannot be decoded.",
            hint="Drop the cursor and call uia_get_tree again from the start.",
        ) from exc

    if agora - emitido > CURSOR_TTL_S:
        raise ToolError(
            Code.INVALID_ARGUMENT,
            f"The cursor has expired (valid for {int(CURSOR_TTL_S)} s).",
            hint="Call uia_get_tree again without a cursor to restart the capture.",
            age_s=round(agora - emitido, 1),
        )
    return window_ref, tree_version, position
