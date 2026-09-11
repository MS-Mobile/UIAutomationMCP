"""Predicados de filtragem da spec §6.2.

Puro: opera sobre o dict do Node ja serializado, sem COM. O filtro roda no
cliente, sobre propriedades ja cacheadas, a custo zero de RPC — e NAO vira
condicao nativa no percurso, porque a travessia precisa descer atraves de
containers que nao passam no filtro para alcancar os que passam (spec §5.2).
"""

from __future__ import annotations

from typing import Any

from ..errors import Code, ToolError

FILTROS = ("interactive", "content", "all", "landmarks")

# Patterns que representam uma acao que o agente pode disparar (spec §6.2).
PATTERNS_ACIONAVEIS = frozenset(
    {"Invoke", "Toggle", "SelectionItem", "ExpandCollapse", "Value", "RangeValue", "Scroll"}
)

# Tipos que carregam conteudo legivel (spec §6.2, filtro "content").
TIPOS_DE_TEXTO = frozenset({"Text", "Document", "Edit", "Image"})

# Containers estruturais (spec §6.2, filtro "landmarks").
TIPOS_ESTRUTURAIS = frozenset(
    {"Window", "Pane", "Group", "ToolBar", "MenuBar", "Tab", "Tree", "List", "Table", "Document"}
)


def _acionavel(node: dict[str, Any]) -> bool:
    patterns = set(node.get("pat", ()))
    st = set(node.get("st", ()))

    # "Value gravavel": um Edit readonly nao e ponto de interacao.
    if "Value" in patterns and "readonly" in st:
        patterns = patterns - {"Value"}

    return bool(patterns & PATTERNS_ACIONAVEIS)


def _interativo(node: dict[str, Any]) -> bool:
    st = set(node.get("st", ()))
    if "disabled" in st or "offscreen" in st:
        return False
    return _acionavel(node) or "focusable" in st


def _conteudo(node: dict[str, Any]) -> bool:
    if _interativo(node):
        return True
    if node.get("type") not in TIPOS_DE_TEXTO:
        return False
    return bool((node.get("name") or "").strip() or node.get("val") not in (None, ""))


def passa_no_filtro(node: dict[str, Any], filtro: str) -> bool:
    """True se o no deve ser emitido sob este filtro. Spec §6.2."""
    if filtro == "interactive":
        return _interativo(node)
    if filtro == "content":
        return _conteudo(node)
    if filtro == "all":
        return True
    if filtro == "landmarks":
        return node.get("type") in TIPOS_ESTRUTURAIS

    raise ToolError(
        Code.INVALID_ARGUMENT,
        f"Unknown filter {filtro!r}. Valid values: {', '.join(FILTROS)}.",
        hint=f"Call again with filter set to one of: {', '.join(FILTROS)}.",
        filter=filtro,
    )
