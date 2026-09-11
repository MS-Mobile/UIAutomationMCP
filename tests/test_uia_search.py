from __future__ import annotations

import pytest

from mcp_windows_uia.errors import Code, ToolError
from mcp_windows_uia.uia.search import Criterios, casa_no_cliente, exige_filtro_no_cliente


def crit(**kw) -> Criterios:
    base = {"name": None, "automation_id": None, "control_type": None,
            "class_name": None, "text_contains": None, "match": "contains"}
    base.update(kw)
    return Criterios(**base)


def test_criterios_vazios_sao_invalid_argument() -> None:
    with pytest.raises(ToolError) as exc:
        crit().validar()
    assert exc.value.code is Code.INVALID_ARGUMENT


def test_match_exact_nao_precisa_de_filtro_no_cliente() -> None:
    assert exige_filtro_no_cliente(crit(name="Salvar", match="exact")) is False


def test_match_contains_precisa_de_filtro_no_cliente() -> None:
    assert exige_filtro_no_cliente(crit(name="Salv", match="contains")) is True


def test_text_contains_sempre_precisa_de_filtro_no_cliente() -> None:
    assert exige_filtro_no_cliente(crit(text_contains="olá", match="exact")) is True


def test_automation_id_sozinho_e_resolvido_no_provider() -> None:
    assert exige_filtro_no_cliente(crit(automation_id="SaveBtn")) is False


def test_casa_contains_e_case_insensitive() -> None:
    no = {"name": "Salvar Como", "type": "MenuItem", "aid": "SaveAs"}
    assert casa_no_cliente(no, crit(name="salvar", match="contains")) is True


def test_casa_exact_e_case_insensitive_no_name() -> None:
    no = {"name": "Salvar", "type": "Button"}
    assert casa_no_cliente(no, crit(name="SALVAR", match="exact")) is True
    assert casa_no_cliente(no, crit(name="Salv", match="exact")) is False


def test_automation_id_e_case_sensitive() -> None:
    """Spec §8.4: automation_id e exato e case-sensitive."""
    no = {"name": "x", "type": "Button", "aid": "SaveBtn"}
    assert casa_no_cliente(no, crit(automation_id="SaveBtn")) is True
    assert casa_no_cliente(no, crit(automation_id="savebtn")) is False


def test_casa_starts_with() -> None:
    no = {"name": "Salvar Como", "type": "MenuItem"}
    assert casa_no_cliente(no, crit(name="Salvar", match="starts_with")) is True
    assert casa_no_cliente(no, crit(name="Como", match="starts_with")) is False


def test_casa_regex() -> None:
    no = {"name": "Arquivo 42", "type": "Text"}
    assert casa_no_cliente(no, crit(name=r"Arquivo \d+", match="regex")) is True
    assert casa_no_cliente(no, crit(name=r"^\d+$", match="regex")) is False


def test_regex_invalida_vira_invalid_argument() -> None:
    with pytest.raises(ToolError) as exc:
        casa_no_cliente({"name": "x"}, crit(name="(nao fecha", match="regex"))
    assert exc.value.code is Code.INVALID_ARGUMENT


def test_criterios_combinados_sao_conjuncao() -> None:
    no = {"name": "Salvar", "type": "Button", "aid": "SaveBtn"}
    assert casa_no_cliente(no, crit(name="Salvar", control_type="Button")) is True
    assert casa_no_cliente(no, crit(name="Salvar", control_type="MenuItem")) is False


def test_text_contains_procura_no_valor_tambem() -> None:
    no = {"name": "Campo", "type": "Edit", "val": "conteudo escondido"}
    assert casa_no_cliente(no, crit(text_contains="escondido")) is True


def test_match_invalido_vira_invalid_argument() -> None:
    with pytest.raises(ToolError) as exc:
        crit(name="x", match="parecido").validar()
    assert exc.value.code is Code.INVALID_ARGUMENT
