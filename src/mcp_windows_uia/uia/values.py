"""Leitura do valor de um elemento. Spec §8.5.

Current* e aceitavel aqui: elemento unico, fora de laco. A proibicao da §5.2 vale
para travessia, onde um RPC por propriedade por no multiplica por centenas.

A cadeia de fontes e consultada em ordem e a primeira que responde vence — o campo
`source` no retorno diz qual foi, para o agente saber o que esta lendo. Sem ele,
"" de um Edit vazio e "" de um Name ausente seriam indistinguiveis.
"""

from __future__ import annotations

from typing import Any

from ..errors import Code, ToolError
from .nodes import REDACTED

ORDEM_DE_LEITURA = (
    "ValuePattern",
    "TextPattern",
    "RangeValuePattern",
    "TogglePattern",
    "SelectionPattern",
    "LegacyIAccessible",
    "Name",
)

_TOGGLE_VAL = {0: "off", 1: "on", 2: "indeterminate"}

# Fontes em que "" significa AUSENCIA, e nao campo legitimamente vazio.
#
# O LegacyIAccessible e um adaptador de MSAA que quase todo elemento expoe, e ele
# devolve "" para qualquer coisa sem valor MSAA — e o default, nao um dado. Medido
# no WhatsApp Desktop: os DataItems de conversa expoem LegacyIAccessible com valor
# "", e a cadeia parava nele e devolvia vazio, escondendo o Name, que tinha a
# conversa inteira. O ValuePattern NAO entra aqui: la um "" e um campo de texto de
# verdade que esta vazio, e cair dele para o Name devolveria o rotulo do campo.
VAZIO_E_AUSENCIA = frozenset({"LegacyIAccessible"})

# `value_type` da §8.5: diz ao agente como interpretar `value` sem ele ter de
# adivinhar pelo tipo JSON. "off" de um toggle e uma string como qualquer outra.
TIPO_DA_FONTE: dict[str, str] = {
    "ValuePattern": "string",
    "TextPattern": "string",
    "RangeValuePattern": "number",
    "TogglePattern": "toggle",
    "SelectionPattern": "selection",
    "LegacyIAccessible": "string",
    "Name": "string",
    "redacted": "string",
}


def tipo_de_valor(fonte: str) -> str:
    """Nome do `value_type` da §8.5 para a fonte que venceu a cadeia."""
    return TIPO_DA_FONTE.get(fonte, "string")


def ler_valor(fonte: Any, *, is_password: bool) -> tuple[Any, str]:
    """Devolve (valor, nome_da_fonte). Spec §8.5.

    `fonte` expoe .tentar(nome) -> valor ou None. Injetado para testar a cadeia
    sem COM; em producao e o adaptador sobre IUIAutomationElement.
    """
    if is_password:
        # CA-15: nem sequer consultamos. O valor nao pode existir em memoria aqui.
        return REDACTED, "redacted"

    for nome in ORDEM_DE_LEITURA:
        valor = fonte.tentar(nome)
        # Só None e ausencia: "" de um campo legitimamente vazio e um valor, e cair
        # dele para o Name devolveria o rotulo do campo como se fosse o conteudo.
        if valor is None:
            continue
        if valor == "" and nome in VAZIO_E_AUSENCIA:
            continue
        if nome == "TogglePattern":
            return _TOGGLE_VAL.get(valor, str(valor)), nome
        return valor, nome

    raise ToolError(
        Code.PATTERN_NOT_SUPPORTED,
        "This element exposes no readable value through any UI Automation pattern.",
        hint=(
            "Use uia_get_tree(root_ref=...) to inspect it, or uia_get_text on its "
            "container to read surrounding content."
        ),
    )
