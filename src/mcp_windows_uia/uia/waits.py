"""Espera por condicao, com backoff. Spec §7.3.

`dormir` e `agora` sao injetados para o teste nao gastar segundos de relogio real.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..errors import Code, ToolError

BACKOFF_TETO_MS = 250
TIMEOUT_PADRAO_MS = 5000
TIMEOUT_MAX_MS = 60000


def proximo_intervalo(atual_ms: int) -> int:
    """50 -> 100 -> 200 -> 250 (teto). Spec §7.3."""
    return min(atual_ms * 2, BACKOFF_TETO_MS)


@dataclass(slots=True)
class EsperaResult:
    satisfeito: bool
    resultado: Any
    waited_ms: float
    polls: int


def esperar_por(
    condicao: Callable[[], Any],
    *,
    timeout_ms: int = TIMEOUT_PADRAO_MS,
    poll_ms: int = 100,
    dormir: Callable[[int], None] | None = None,
    agora: Callable[[], float] | None = None,
    descricao: str = "condition",
) -> EsperaResult:
    """Chama `condicao` ate ela devolver algo truthy, ou estourar o timeout.

    Uma ToolError levantada pela condicao propaga imediatamente: um WINDOW_CLOSED
    e um fato, nao uma condicao que ainda pode virar verdadeira.
    """
    _dormir = dormir if dormir is not None else (lambda ms: time.sleep(ms / 1000.0))
    _agora = agora if agora is not None else time.monotonic

    inicio = _agora()
    intervalo = max(50, min(poll_ms, BACKOFF_TETO_MS))
    polls = 0

    while True:
        polls += 1
        resultado = condicao()
        decorrido_ms = (_agora() - inicio) * 1000.0

        if resultado:
            return EsperaResult(True, resultado, decorrido_ms, polls)

        if decorrido_ms >= timeout_ms:
            raise ToolError(
                Code.TIMEOUT,
                f"Condition {descricao!r} not satisfied within {timeout_ms} ms.",
                hint=(
                    "Inspect the current state with uia_get_tree before retrying, "
                    "or raise timeout_ms."
                ),
                waited_ms=round(decorrido_ms, 1),
                polls=polls,
            )

        _dormir(intervalo)
        intervalo = proximo_intervalo(intervalo)
