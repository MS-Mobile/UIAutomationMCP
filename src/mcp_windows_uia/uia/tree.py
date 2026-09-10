"""Captura da arvore: percurso em largura com orcamento. Spec §5.2 e §6.1.

Por que o navegador e injetado: a logica de parada (orcamento, elisao de irmaos,
aprofundamento adaptativo) e onde mora o risco, e ela precisa ser testavel sem
Windows. `percorrer` nao sabe o que e COM; quem sabe e `filhos_cacheados`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..budget import (
    DEFAULT_MAX_CHILDREN,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_NODES,
    HARD_MAX_DEPTH,
    HARD_MAX_NODES,
    TRUNCATION_HINT,
    clamp,
)

# Assinaturas do que `percorrer` recebe de fora.
FilhosDe = Callable[[Any, int], Sequence[Any]]
Aprovado = Callable[[Any, int], bool]


@dataclass(frozen=True, slots=True)
class CaptureBudget:
    max_nodes: int = DEFAULT_MAX_NODES
    max_depth: int = DEFAULT_MAX_DEPTH
    max_children_per_node: int = DEFAULT_MAX_CHILDREN
    # Teto de nos VISITADOS, nao emitidos. Sem ele, um filtro que rejeita tudo somado
    # ao aprofundamento adaptativo faz a largura explodir exponencialmente: a busca
    # segue descendo procurando algo que passe e nunca para. Mesmo principio do teto
    # interno da §8.4.
    max_visited: int = 5000
    # True quando o chamador NAO informou max_depth. So entao aprofundamos (§6.1).
    depth_is_default: bool = False


@dataclass(slots=True)
class CaptureResult:
    emitidos: list[tuple[Any, int]] = field(default_factory=list)
    elididos: dict[Any, int] = field(default_factory=dict)
    visitados: int = 0
    truncado: bool = False
    exhausted_budget: bool = False
    auto_deepened: bool = False
    depth_reached: int = 0
    fila_restante: list[tuple[Any, int]] = field(default_factory=list)


def percorrer(
    raiz: Any,
    filhos_de: FilhosDe,
    aprovado: Aprovado,
    orcamento: CaptureBudget,
) -> CaptureResult:
    """Largura por nivel, ordem de documento dentro do nivel. Spec §5.2 passo 4.

    O orcamento e aplicado DURANTE a travessia: assim que max_nodes e atingido,
    nenhuma chamada adicional a filhos_de acontece. E isso que faz a captura
    custar 159 ms em vez de 18 s numa janela grande.
    """
    r = CaptureResult()
    teto_profundidade = clamp(orcamento.max_depth, 1, HARD_MAX_DEPTH)

    fila: list[tuple[Any, int]] = [(raiz, 0)]

    while fila:
        no, nivel = fila.pop(0)

        r.visitados += 1
        r.depth_reached = max(r.depth_reached, nivel)
        if aprovado(no, nivel):
            r.emitidos.append((no, nivel))

        if len(r.emitidos) >= orcamento.max_nodes:
            r.truncado = bool(fila)
            r.fila_restante = fila
            return r

        if r.visitados >= orcamento.max_visited:
            # Gastamos o orcamento de travessia sem encher o de emissao: a arvore e
            # grande e o filtro e restritivo. Para e sinaliza, em vez de moer.
            r.truncado = True
            r.exhausted_budget = True
            r.fila_restante = fila
            return r

        if nivel >= teto_profundidade:
            # Aprofundamento adaptativo (§6.1): so continua se o chamador nao pediu
            # profundidade explicita E nada passou no filtro ate aqui.
            pode_aprofundar = (
                orcamento.depth_is_default
                and not r.emitidos
                and teto_profundidade < HARD_MAX_DEPTH
            )
            if not pode_aprofundar:
                r.fila_restante = [(no, nivel), *fila]
                r.truncado = bool(fila)
                continue
            teto_profundidade = min(teto_profundidade + 1, HARD_MAX_DEPTH)
            r.auto_deepened = True

        for filho in _filhos_limitados(no, nivel, filhos_de, orcamento, r):
            fila.append((filho, nivel + 1))

    return r


def orcamento_de_pagina(pular: int, max_nodes: int) -> tuple[int, int]:
    """(pular normalizado, teto de emissao da travessia) para uma pagina. Spec §5.2.

    O TAMANHO da pagina devolvida e limitado por HARD_MAX_NODES (CA-09), mas o teto
    de travessia soma `pular`: se ele tambem parasse em 1500, nunca daria para
    paginar alem do no 1500 — que e justamente onde a paginacao importa (o WhatsApp
    Desktop tem ~24 mil nos). O custo do percurso ja tem freio proprio, max_visited.
    """
    pular = max(0, int(pular))
    pagina = clamp(max_nodes, 1, HARD_MAX_NODES)
    return pular, pular + pagina


def _filhos_limitados(
    no: Any,
    nivel: int,
    filhos_de: FilhosDe,
    orcamento: CaptureBudget,
    r: CaptureResult,
) -> Sequence[Any]:
    """Aplica max_children_per_node fatiando; anota quantos ficaram de fora (§6.1)."""
    filhos = filhos_de(no, nivel)
    if len(filhos) > orcamento.max_children_per_node:
        r.elididos[no] = len(filhos) - orcamento.max_children_per_node
        return filhos[: orcamento.max_children_per_node]
    return filhos


# ---------------------------------------------------------------- ponte com COM


def _patterns_disponiveis(elem: Any, props_de_pattern: dict[str, int]) -> list[str]:
    """Patterns suportados, lidos do cache. Zero RPC.

    IUIAutomationElement NAO expoe `CachedIsXxxPatternAvailable` como atributo: o
    typelib so declara ~30 propriedades `Cached*` fixas, e disponibilidade de pattern
    nao esta entre elas. Um `getattr` por nome devolveria o default para todo mundo e
    `pat` sairia vazio em cada no — com o filtro "interactive" da §6.2 caindo no
    criterio de reserva ("focusable"), que deixa passar Panes e perde Buttons de
    provider que nao marca IsKeyboardFocusable.

    GetCachedPropertyValue le do CacheRequest ja materializado (as 32 propriedades
    IsXxxPatternAvailable entram via `tree_props`), portanto continua sem RPC:
    medido em 0,18 ms para as 32 propriedades de um no.
    """
    return [
        nome
        for nome, prop_id in props_de_pattern.items()
        if elem.GetCachedPropertyValue(prop_id)
    ]


def filhos_cacheados(automation: Any, cache_request: Any):
    """Navegador que fala COM: um FindAllBuildCache(Children) por no-pai (§5.2)."""
    UIA = automation.UIA

    def _filhos(elem: Any, _nivel: int) -> list[Any]:
        try:
            achados = elem.FindAllBuildCache(
                UIA.TreeScope_Children, automation.true_condition, cache_request
            )
        except Exception:
            # No que sumiu ou provider que recusou: subarvore vazia, nao aborta a captura.
            return []
        return [achados.GetElement(i) for i in range(achados.Length)]

    return _filhos


def capturar_janela(
    hwnd: int,
    *,
    filtro: str = "interactive",
    max_nodes: int = DEFAULT_MAX_NODES,
    max_depth: int | None = None,
    max_children_per_node: int = DEFAULT_MAX_CHILDREN,
    verbose: bool = False,
    pular: int = 0,
    atribuir_ref: Callable[[Any, int], str] | None = None,
) -> dict[str, Any]:
    """Captura a arvore de uma janela dentro do orcamento. Spec §5.2.

    `atribuir_ref` e injetado pelo servidor para registrar cada no no RefStore.
    Ausente (uso em teste), gera refs sinteticas.

    `pular` e a continuacao por cursor: percorre com orcamento `pular + pagina` e
    fatia `[pular:]` no FIM. Descartar dentro do filtro seria mais barato, mas
    deixaria `emitidos` vazio no comeco da pagina 2 e ligaria o aprofundamento
    adaptativo da §6.1 ("nada passou no filtro") — cada pagina desceria a uma
    profundidade diferente, com sobreposicao ou buracos entre elas. Aqui a forma da
    travessia e identica em toda pagina, que e o que o CA-08 exige (intersecao de
    `ref` vazia entre paginas consecutivas).
    """
    from . import core
    from .filters import passa_no_filtro
    from .nodes import build_node

    automation = core.automation()
    cache_request = automation.build_cache_request(automation.tree_props())
    raiz = automation.element_from_handle_build_cache(hwnd, cache_request)

    props_de_pattern = core.pattern_availability_props()
    navegador = filhos_cacheados(automation, cache_request)

    # `percorrer` chama _aprovado uma vez por no visitado e, quando True, faz append
    # em emitidos na mesma ordem. Entao aprovados[i] corresponde a r.emitidos[i] —
    # sem mapa por id(), que seria fragil com ponteiros COM.
    aprovados: list[dict[str, Any]] = []
    contador = [0]

    def _aprovado(elem: Any, nivel: int) -> bool:
        # Serializa com ref provisoria: o filtro nao olha para `ref`, e cunhar a ref
        # antes de saber se o no passa desperdicaria numeros e (pior) registraria no
        # RefStore elementos que nunca serao devolvidos ao agente.
        node = build_node(
            elem,
            ref="",
            depth=nivel,
            patterns=_patterns_disponiveis(elem, props_de_pattern),
            verbose=verbose,
        )
        if not passa_no_filtro(node, filtro):
            return False

        node["ref"] = (
            atribuir_ref(elem, contador[0])
            if atribuir_ref is not None
            else f"w{hwnd}-e{contador[0]}"
        )
        contador[0] += 1
        aprovados.append(node)
        return True

    pular, teto_de_emissao = orcamento_de_pagina(pular, max_nodes)
    orcamento = CaptureBudget(
        max_nodes=teto_de_emissao,
        max_depth=DEFAULT_MAX_DEPTH if max_depth is None else max_depth,
        max_children_per_node=max_children_per_node,
        depth_is_default=max_depth is None,
    )

    r = percorrer(raiz, navegador, _aprovado, orcamento)

    nodes: list[dict[str, Any]] = []
    for (elem, _nivel), node in zip(r.emitidos, aprovados, strict=True):
        elididos = r.elididos.get(elem)
        if elididos:
            node["n"] = elididos
        nodes.append(node)

    # A fatia e o ULTIMO passo: ate aqui a travessia foi identica a da pagina 1.
    nodes = nodes[pular:]

    stats: dict[str, Any] = {
        "returned": len(nodes),
        "visited": r.visitados,
        "truncated": r.truncado,
        "depth_reached": r.depth_reached,
    }
    if r.auto_deepened:
        stats["auto_deepened"] = True
        stats["depth_requested"] = DEFAULT_MAX_DEPTH
    if r.truncado:
        stats["hint"] = TRUNCATION_HINT

    return {"nodes": nodes, "stats": stats}
