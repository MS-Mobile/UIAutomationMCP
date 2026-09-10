"""Serializacao do Node da spec §5.1.

Le exclusivamente propriedades Cached* — o elemento chega ja materializado pelo
CacheRequest. Nenhuma chamada COM acontece aqui.

Chaves curtas de proposito: a arvore vai inteira para o contexto de um LLM.
"""

from __future__ import annotations

from typing import Any

from ..budget import truncate_name
from .core import control_type_name

REDACTED = "«redacted:password»"

STATE_KEYS = (
    "enabled", "disabled", "focused", "focusable", "offscreen", "selected",
    "expanded", "collapsed", "checked", "unchecked", "indeterminate",
    "readonly", "password", "multiline",
)

_TOGGLE = {0: "unchecked", 1: "checked", 2: "indeterminate"}
_TOGGLE_VAL = {0: "off", 1: "on", 2: "indeterminate"}
_EXPAND = {0: "collapsed", 1: "expanded", 2: "collapsed", 3: "expanded"}


def _cached(elem: Any, nome: str, padrao: Any = None) -> Any:
    """Le uma propriedade Cached*, devolvendo padrao se ausente do CacheRequest."""
    try:
        return getattr(elem, nome, padrao)
    except Exception:
        return padrao


def states_of(elem: Any) -> list[str]:
    """Lista de estados presentes. Ausencia significa falso (spec §5.1)."""
    st: list[str] = []

    st.append("enabled" if _cached(elem, "CachedIsEnabled", 1) else "disabled")
    if _cached(elem, "CachedIsOffscreen", 0):
        st.append("offscreen")
    if _cached(elem, "CachedIsKeyboardFocusable", 0):
        st.append("focusable")
    if _cached(elem, "CachedHasKeyboardFocus", 0):
        st.append("focused")
    if _cached(elem, "CachedIsPassword", 0):
        st.append("password")

    toggle = _cached(elem, "CachedToggleToggleState")
    if toggle in _TOGGLE:
        st.append(_TOGGLE[toggle])

    expand = _cached(elem, "CachedExpandCollapseExpandCollapseState")
    if expand in _EXPAND:
        st.append(_EXPAND[expand])

    if _cached(elem, "CachedSelectionItemIsSelected"):
        st.append("selected")
    if _cached(elem, "CachedValueIsReadOnly"):
        st.append("readonly")

    return st


def _value_of(elem: Any, st: list[str]) -> Any:
    """Valor atual, na ordem da spec §5.1. Senha nunca vaza."""
    if "password" in st:
        return REDACTED

    valor = _cached(elem, "CachedValueValue")
    if valor not in (None, ""):
        return valor

    faixa = _cached(elem, "CachedRangeValueValue")
    if faixa is not None:
        return faixa

    toggle = _cached(elem, "CachedToggleToggleState")
    if toggle in _TOGGLE_VAL:
        return _TOGGLE_VAL[toggle]

    selecionado = _cached(elem, "CachedSelectionItemIsSelected")
    if selecionado is not None:
        return bool(selecionado)

    return None


def _rect_of(elem: Any) -> list[int] | None:
    """[left, top, right, bottom] em px fisicos. None se area zero (§4.1)."""
    bruto = _cached(elem, "CachedBoundingRectangle")
    if bruto is None:
        return None
    try:
        if hasattr(bruto, "left"):
            left, top, right, bottom = bruto.left, bruto.top, bruto.right, bruto.bottom
        else:
            left, top, right, bottom = tuple(bruto)
    except Exception:
        return None
    if right - left <= 0 or bottom - top <= 0:
        return None
    return [int(left), int(top), int(right), int(bottom)]


def build_node(
    elem: Any,
    *,
    ref: str,
    depth: int,
    patterns: list[str],
    verbose: bool = False,
) -> dict[str, Any]:
    """Monta o dict do Node. Chaves opcionais sao omitidas quando vazias."""
    st = states_of(elem)
    node: dict[str, Any] = {
        "ref": ref,
        "d": depth,
        "type": control_type_name(_cached(elem, "CachedControlType", 0)),
        "name": truncate_name(_cached(elem, "CachedName", "") or ""),
        "st": st,
        "pat": patterns,
    }

    aid = _cached(elem, "CachedAutomationId", "") or ""
    if aid:
        node["aid"] = aid

    cls = _cached(elem, "CachedClassName", "") or ""
    if cls:
        node["cls"] = cls

    valor = _value_of(elem, st)
    if valor is not None:
        node["val"] = valor

    rect = _rect_of(elem)
    if rect is not None:
        node["rect"] = rect

    if verbose:
        ajuda = _cached(elem, "CachedHelpText", "") or ""
        if ajuda:
            node["help"] = truncate_name(ajuda)

    return node
