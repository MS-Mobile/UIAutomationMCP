"""Re-resolucao de elemento por identidade. Spec §7.2, passo 5.

Assinatura deliberada: recebe ElementIdentity, nao um ref nem o RefStore. A
camada futura de aprendizado de seletores por app persiste exatamente esta
identidade em disco e precisa re-resolve-la numa sessao nova, onde o RefStore
daquela sessao nao existe. Acoplar isto ao store exigiria refatorar depois.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from .errors import Code, ToolError
from .refs import ElementIdentity


class Estrategia(StrEnum):
    """Ordem de tentativa da spec §7.2, da mais forte para a mais fraca."""

    AUTOMATION_ID = "automation_id"
    NAME = "name"
    INDEX_PATH = "index_path"


def identity_to_dict(identity: ElementIdentity) -> dict[str, Any]:
    """Serializa para JSON puro. index_path vira lista (tupla nao e JSON)."""
    return {
        "automation_id": identity.automation_id,
        "control_type": identity.control_type,
        "name": identity.name,
        "class_name": identity.class_name,
        "index_path": list(identity.index_path),
    }


def identity_from_dict(d: dict[str, Any]) -> ElementIdentity:
    return ElementIdentity(
        automation_id=d.get("automation_id", ""),
        control_type=d.get("control_type", ""),
        name=d.get("name", ""),
        class_name=d.get("class_name", ""),
        index_path=tuple(d.get("index_path", ())),
    )


def _estrategias_aplicaveis(identity: ElementIdentity) -> list[Estrategia]:
    """Pula estrategias sem material — cada uma custa uma chamada COM."""
    ordem: list[Estrategia] = []
    if identity.automation_id:
        ordem.append(Estrategia.AUTOMATION_ID)
    if identity.name:
        ordem.append(Estrategia.NAME)
    if identity.index_path:
        ordem.append(Estrategia.INDEX_PATH)
    return ordem


def rebind(
    automation: Any, *, hwnd: int, identity: ElementIdentity
) -> tuple[Any, Estrategia]:
    """Acha o elemento de novo. Spec §7.2 passo 5.

    Devolve (elemento, estrategia_que_funcionou).
    Levanta AMBIGUOUS_MATCH se mais de um candidato; STALE_REF se nenhum.
    """
    for estrategia in _estrategias_aplicaveis(identity):
        candidatos = automation.buscar(hwnd, identity, estrategia)

        if len(candidatos) == 1:
            return candidatos[0], estrategia

        if len(candidatos) > 1:
            raise ToolError(
                Code.AMBIGUOUS_MATCH,
                f"{len(candidatos)} elements match the remembered identity "
                f"({estrategia.value}); refusing to guess.",
                strategy=estrategia.value,
                count=len(candidatos),
                automation_id=identity.automation_id,
                name=identity.name,
                control_type=identity.control_type,
            )

    raise ToolError(
        Code.STALE_REF,
        "The element no longer exists in this window and could not be found again.",
        reason="element_gone",
        window_alive=True,
        automation_id=identity.automation_id,
        name=identity.name,
        control_type=identity.control_type,
    )
