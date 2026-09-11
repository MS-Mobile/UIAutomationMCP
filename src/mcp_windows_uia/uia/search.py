"""Busca plana de elementos. Spec §8.4.

Aqui condicao nativa E legitima, ao contrario do percurso da §5.2: a busca e
plana e nao precisa atravessar containers que nao casam. Criterios exatos viram
IUIAutomationCondition e sao resolvidos no provider; contains/starts_with/regex
e text_contains filtram no cliente sobre propriedades ja cacheadas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..errors import Code, ToolError

MATCHES = ("exact", "contains", "starts_with", "regex")

# Teto interno de elementos olhados numa busca (spec §8.4). Mesmo espirito do
# `max_visited` da §5.2: sem ele, uma busca sem criterio nativo enumera a janela
# inteira — 24409 nos e ~18 s no WhatsApp Desktop. Estourar o teto nao e erro; e
# resultado parcial com `stats.exhaustive` = false.
TETO_DE_BUSCA = 5000


@dataclass(frozen=True, slots=True)
class Criterios:
    name: str | None = None
    automation_id: str | None = None
    control_type: str | None = None
    class_name: str | None = None
    text_contains: str | None = None
    match: str = "contains"

    def validar(self) -> None:
        if self.match not in MATCHES:
            raise ToolError(
                Code.INVALID_ARGUMENT,
                f"Unknown match mode {self.match!r}. Valid values: {', '.join(MATCHES)}.",
                match=self.match,
            )
        informados = (
            self.name, self.automation_id, self.control_type,
            self.class_name, self.text_contains,
        )
        if not any(informados):
            raise ToolError(
                Code.INVALID_ARGUMENT,
                "At least one of name, automation_id, control_type, class_name or "
                "text_contains is required.",
                hint="Add a criterion, or call uia_get_tree to see what is in the window.",
            )


def exige_filtro_no_cliente(criterios: Criterios) -> bool:
    """True se algum criterio nao pode virar condicao nativa (spec §8.4)."""
    if criterios.text_contains:
        return True
    if criterios.name and criterios.match != "exact":
        return True
    return False


def _casa_texto(valor: str, alvo: str, modo: str) -> bool:
    if modo == "exact":
        return valor.casefold() == alvo.casefold()
    if modo == "contains":
        return alvo.casefold() in valor.casefold()
    if modo == "starts_with":
        return valor.casefold().startswith(alvo.casefold())

    try:
        return re.search(alvo, valor, re.IGNORECASE) is not None
    except re.error as exc:
        raise ToolError(
            Code.INVALID_ARGUMENT,
            f"Invalid regular expression {alvo!r}: {exc}.",
            hint="Fix the pattern, or use match='contains' instead.",
            pattern=alvo,
        ) from exc


def casa_no_cliente(node: dict[str, Any], criterios: Criterios) -> bool:
    """Conjuncao de todos os criterios informados, sobre um Node ja serializado."""
    if criterios.automation_id is not None:
        # Spec §8.4: automation_id e exato E case-sensitive.
        if node.get("aid", "") != criterios.automation_id:
            return False

    if criterios.control_type is not None:
        if node.get("type", "") != criterios.control_type:
            return False

    if criterios.class_name is not None:
        if node.get("cls", "") != criterios.class_name:
            return False

    if criterios.name is not None:
        if not _casa_texto(node.get("name", "") or "", criterios.name, criterios.match):
            return False

    if criterios.text_contains is not None:
        alvo = criterios.text_contains.casefold()
        campos = (str(node.get("name", "") or ""), str(node.get("val", "") or ""))
        if not any(alvo in campo.casefold() for campo in campos):
            return False

    return True
