from __future__ import annotations

import pytest

from mcp_windows_uia.errors import Code, ToolError
from mcp_windows_uia.uia.values import ORDEM_DE_LEITURA, ler_valor, tipo_de_valor


class FonteFalsa:
    """Simula a cadeia de fontes: cada nome mapeia para um valor ou ausencia."""

    def __init__(self, **fontes):
        self.fontes = fontes
        self.consultadas: list[str] = []

    def tentar(self, nome: str):
        self.consultadas.append(nome)
        return self.fontes.get(nome)


def test_value_pattern_tem_prioridade() -> None:
    f = FonteFalsa(ValuePattern="texto", TextPattern="outro", Name="terceiro")
    valor, fonte = ler_valor(f, is_password=False)
    assert valor == "texto"
    assert fonte == "ValuePattern"


def test_cai_para_text_pattern() -> None:
    f = FonteFalsa(TextPattern="documento inteiro")
    valor, fonte = ler_valor(f, is_password=False)
    assert valor == "documento inteiro"
    assert fonte == "TextPattern"


def test_cai_para_name_em_ultimo_caso() -> None:
    f = FonteFalsa(Name="rotulo")
    valor, fonte = ler_valor(f, is_password=False)
    assert valor == "rotulo"
    assert fonte == "Name"


def test_ordem_de_consulta_e_a_da_spec() -> None:
    f = FonteFalsa()
    with pytest.raises(ToolError):
        ler_valor(f, is_password=False)
    assert f.consultadas == list(ORDEM_DE_LEITURA)


def test_senha_nunca_consulta_fonte_alguma() -> None:
    """CA-15: o valor de um campo de senha nao pode nem ser lido."""
    f = FonteFalsa(ValuePattern="s3nh4-secreta")
    valor, fonte = ler_valor(f, is_password=True)
    assert valor == "«redacted:password»"
    assert fonte == "redacted"
    assert f.consultadas == []


def test_nenhuma_fonte_disponivel_e_pattern_not_supported() -> None:
    f = FonteFalsa()
    with pytest.raises(ToolError) as exc:
        ler_valor(f, is_password=False)
    assert exc.value.code is Code.PATTERN_NOT_SUPPORTED


def test_toggle_state_vira_on_off() -> None:
    f = FonteFalsa(TogglePattern=1)
    valor, fonte = ler_valor(f, is_password=False)
    assert valor == "on"
    assert fonte == "TogglePattern"


def test_string_vazia_nao_conta_como_ausencia() -> None:
    """Um campo de texto legitimamente vazio deve devolver "", nao cair para Name."""
    f = FonteFalsa(ValuePattern="", Name="rotulo")
    valor, fonte = ler_valor(f, is_password=False)
    assert valor == ""
    assert fonte == "ValuePattern"


def test_value_type_reflete_a_fonte() -> None:
    """§8.5: value_type diz como interpretar value; "off" de toggle nao e string livre."""
    assert tipo_de_valor("TogglePattern") == "toggle"
    assert tipo_de_valor("RangeValuePattern") == "number"
    assert tipo_de_valor("SelectionPattern") == "selection"
    assert tipo_de_valor("ValuePattern") == "string"
    assert tipo_de_valor("redacted") == "string"
