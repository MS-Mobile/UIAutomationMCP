"""Decisoes do adaptador COM da §8.3 que nao dao para provar contra janela real.

O Bloco de Notas da suite e2e abre vazio, entao a area de texto dele nao tem
conteudo: cortar a FONTE do TextPattern em 5 caracteres produziria exatamente o
mesmo resultado que nao cortar, e o teste de paginacao passaria com o bug dentro.
Aqui o elemento e um duble que registra com que limite o GetText foi chamado.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_windows_uia.budget import HARD_MAX_CHARS
from mcp_windows_uia.server import LeitorCOM

UIA_FALSA = SimpleNamespace(
    UIA_TextPatternId=10014,
    IUIAutomationTextPattern=object(),
    TreeScope_Children=2,
)
AUTOMATION_FALSA = SimpleNamespace(UIA=UIA_FALSA, true_condition=object())


class RangeFalso:
    def __init__(self, texto: str, registro: list[int]) -> None:
        self._texto = texto
        self._registro = registro

    def GetText(self, limite):  # noqa: N802 - assinatura do COM
        self._registro.append(limite)
        return self._texto[:limite] if limite >= 0 else self._texto


class TextPatternFalso:
    def __init__(self, texto: str, registro: list[int]) -> None:
        self.DocumentRange = RangeFalso(texto, registro)

    def QueryInterface(self, _iface):  # noqa: N802 - assinatura do COM
        return self


class ElementoComTexto:
    def __init__(self, texto: str) -> None:
        self.texto = texto
        self.limites_pedidos: list[int] = []

    def GetCurrentPattern(self, _pattern_id):  # noqa: N802 - assinatura do COM
        return TextPatternFalso(self.texto, self.limites_pedidos)


class ElementoQueMente:
    """Anuncia TextPattern e explode ao ser consultado. Acontece na pratica."""

    def GetCurrentPattern(self, _pattern_id):  # noqa: N802 - assinatura do COM
        raise RuntimeError("provider recusou o pattern")


@pytest.fixture()
def leitor() -> LeitorCOM:
    return LeitorCOM(AUTOMATION_FALSA, object(), {})


def test_limite_do_gettext_e_o_teto_duro_e_nao_a_pagina(leitor: LeitorCOM) -> None:
    """Regressao: `offset` pagina sobre um documento que tem de ser o mesmo sempre.

    Se o GetText usasse o max_chars da chamada, uma pagina de 5 caracteres leria uma
    fonte de 5 caracteres e `next_offset` apontaria para o fim de um texto mutilado —
    o agente concluiria que leu a janela inteira.
    """
    elemento = ElementoComTexto("a" * 100)

    leitor._texto_de_pattern(elemento, ["Text"])

    assert elemento.limites_pedidos == [HARD_MAX_CHARS]


def test_sem_text_pattern_nao_ha_rpc(leitor: LeitorCOM) -> None:
    """A disponibilidade vem do cache; consultar o pattern de quem nao tem custa RPC."""
    elemento = ElementoComTexto("qualquer coisa")

    assert leitor._texto_de_pattern(elemento, ["Value", "Invoke"]) is None
    assert elemento.limites_pedidos == []


def test_pattern_que_explode_devolve_none_e_deixa_descer(leitor: LeitorCOM) -> None:
    assert leitor._texto_de_pattern(ElementoQueMente(), ["Text"]) is None


def test_texto_do_pattern_chega_inteiro(leitor: LeitorCOM) -> None:
    assert leitor._texto_de_pattern(ElementoComTexto("linha um\nlinha dois"), ["Text"]) == (
        "linha um\nlinha dois"
    )
