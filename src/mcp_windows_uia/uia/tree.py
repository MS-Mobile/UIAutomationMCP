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
