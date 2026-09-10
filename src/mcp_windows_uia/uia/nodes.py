"""Serializacao do Node da spec §5.1.

Le exclusivamente propriedades Cached* — o elemento chega ja materializado pelo
CacheRequest. Nenhuma chamada COM acontece aqui.

Chaves curtas de proposito: a arvore vai inteira para o contexto de um LLM.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..budget import truncate_name
from .core import control_type_name, uia_module

REDACTED = "«redacted:password»"

STATE_KEYS = (
    "enabled", "disabled", "focused", "focusable", "offscreen", "selected",
    "expanded", "collapsed", "checked", "unchecked", "indeterminate",
    "readonly", "password", "multiline",
)

_TOGGLE = {0: "unchecked", 1: "checked", 2: "indeterminate"}
_TOGGLE_VAL = {0: "off", 1: "on", 2: "indeterminate"}
_EXPAND = {0: "collapsed", 1: "expanded", 2: "collapsed", 3: "expanded"}

# Propriedades derivadas de pattern: (chave, pattern exigido, nome no typelib).
#
# Elas NAO podem ser lidas sem antes checar que o pattern existe. IUIAutomationElement
# nao as expoe como atributo `Cached*` — o typelib declara so 32 propriedades fixas, e
# nenhuma destas esta entre elas — e `GetCachedPropertyValue` devolve o DEFAULT quando o
# provider nao suporta o pattern. Medido no Bloco de Notas: ToggleState=2 e
# ValueIsReadOnly=True em Pane, Button e na propria janela. Sem o portao, a arvore
# inteira sairia marcada "indeterminate" e "readonly".
_PROPS_DE_PATTERN: tuple[tuple[str, str, str], ...] = (
    ("toggle", "Toggle", "UIA_ToggleToggleStatePropertyId"),
    ("expand", "ExpandCollapse", "UIA_ExpandCollapseExpandCollapseStatePropertyId"),
    ("selected", "SelectionItem", "UIA_SelectionItemIsSelectedPropertyId"),
    ("readonly", "Value", "UIA_ValueIsReadOnlyPropertyId"),
    ("value", "Value", "UIA_ValueValuePropertyId"),
    ("range", "RangeValue", "UIA_RangeValueValuePropertyId"),
)

_PATTERN_DE = {chave: pattern for chave, pattern, _ in _PROPS_DE_PATTERN}
_ids_de_prop: dict[str, int] | None = None


def _prop_id(chave: str) -> int:
    """Property id vindo do typelib, resolvido uma vez por processo."""
    global _ids_de_prop
    if _ids_de_prop is None:
        UIA = uia_module()
        _ids_de_prop = {c: getattr(UIA, nome) for c, _pat, nome in _PROPS_DE_PATTERN}
    return _ids_de_prop[chave]


def _de_pattern(elem: Any, chave: str, patterns: Sequence[str]) -> Any:
    """Le propriedade derivada de pattern; None quando o pattern nao existe."""
    if _PATTERN_DE[chave] not in patterns:
        return None
    try:
        valor = elem.GetCachedPropertyValue(_prop_id(chave))
    except Exception:
        return None
    # O sentinela "nao suportado" do UIA aflora como ponteiro IUnknown, nao escalar.
    return valor if isinstance(valor, (bool, int, float, str)) else None


def _cached(elem: Any, nome: str, padrao: Any = None) -> Any:
    """Le uma propriedade Cached*, devolvendo padrao se ausente do CacheRequest."""
    try:
        return getattr(elem, nome, padrao)
    except Exception:
        return padrao


def states_of(elem: Any, patterns: Sequence[str] = ()) -> list[str]:
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

    toggle = _de_pattern(elem, "toggle", patterns)
    if toggle in _TOGGLE:
        st.append(_TOGGLE[toggle])

    expand = _de_pattern(elem, "expand", patterns)
    if expand in _EXPAND:
        st.append(_EXPAND[expand])

    if _de_pattern(elem, "selected", patterns):
        st.append("selected")
    if _de_pattern(elem, "readonly", patterns):
        st.append("readonly")

    return st


def _value_of(elem: Any, st: list[str], patterns: Sequence[str] = ()) -> Any:
    """Valor atual, na ordem da spec §5.1. Senha nunca vaza."""
    if "password" in st:
        return REDACTED

    valor = _de_pattern(elem, "value", patterns)
    if valor not in (None, ""):
        return valor

    faixa = _de_pattern(elem, "range", patterns)
    if faixa is not None:
        return faixa

    toggle = _de_pattern(elem, "toggle", patterns)
    if toggle in _TOGGLE_VAL:
        return _TOGGLE_VAL[toggle]

    selecionado = _de_pattern(elem, "selected", patterns)
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
    st = states_of(elem, patterns)
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

    valor = _value_of(elem, st, patterns)
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
