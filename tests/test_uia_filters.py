from __future__ import annotations

import pytest

from mcp_windows_uia.errors import Code, ToolError
from mcp_windows_uia.uia.filters import FILTROS, passa_no_filtro

ACIONAVEIS = ["Invoke", "Toggle", "SelectionItem", "ExpandCollapse", "RangeValue", "Scroll"]


def no(**kw):
    base = {"ref": "w1-e1", "d": 1, "type": "Button", "name": "", "st": ["enabled"], "pat": []}
    base.update(kw)
    return base


def test_interactive_aceita_no_com_pattern_acionavel() -> None:
    assert passa_no_filtro(no(pat=["Invoke"]), "interactive") is True


@pytest.mark.parametrize("pattern", ACIONAVEIS)
def test_interactive_aceita_todos_os_patterns_acionaveis(pattern: str) -> None:
    assert passa_no_filtro(no(pat=[pattern]), "interactive") is True


def test_interactive_aceita_no_focavel_sem_pattern() -> None:
    assert passa_no_filtro(no(pat=[], st=["enabled", "focusable"]), "interactive") is True


def test_interactive_rejeita_desabilitado() -> None:
    assert passa_no_filtro(no(pat=["Invoke"], st=["disabled"]), "interactive") is False


def test_interactive_rejeita_offscreen() -> None:
    assert passa_no_filtro(no(pat=["Invoke"], st=["enabled", "offscreen"]), "interactive") is False


def test_interactive_rejeita_texto_puro() -> None:
    assert passa_no_filtro(no(type="Text", pat=[], st=["enabled"]), "interactive") is False


def test_value_somente_leitura_nao_conta_como_acionavel() -> None:
    """Spec §6.2 diz 'Value gravavel'. Um Edit readonly nao e ponto de interacao."""
    n = no(type="Edit", pat=["Value"], st=["enabled", "readonly"])
    assert passa_no_filtro(n, "interactive") is False


def test_value_gravavel_conta_como_acionavel() -> None:
    n = no(type="Edit", pat=["Value"], st=["enabled"])
    assert passa_no_filtro(n, "interactive") is True


def test_content_inclui_texto_alem_do_interativo() -> None:
    assert passa_no_filtro(no(type="Text", name="Ola", pat=[], st=["enabled"]), "content") is True
    assert passa_no_filtro(no(pat=["Invoke"]), "content") is True


def test_content_rejeita_texto_sem_nome_nem_valor() -> None:
    assert passa_no_filtro(no(type="Text", name="", pat=[], st=["enabled"]), "content") is False


def test_all_aceita_qualquer_coisa() -> None:
    assert passa_no_filtro(no(type="Pane", pat=[], st=["disabled"]), "all") is True


def test_landmarks_so_containers_estruturais() -> None:
    assert passa_no_filtro(no(type="ToolBar"), "landmarks") is True
    assert passa_no_filtro(no(type="Document"), "landmarks") is True
    assert passa_no_filtro(no(type="Button", pat=["Invoke"]), "landmarks") is False


def test_filtro_desconhecido_e_invalid_argument() -> None:
    with pytest.raises(ToolError) as exc:
        passa_no_filtro(no(), "colorido")
    assert exc.value.code is Code.INVALID_ARGUMENT
    assert "interactive" in exc.value.message


def test_conjunto_de_filtros_bate_com_a_spec() -> None:
    assert set(FILTROS) == {"interactive", "content", "all", "landmarks"}
