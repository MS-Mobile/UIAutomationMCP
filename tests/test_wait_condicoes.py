"""A tabela de decisao da §8.6, sem COM e sem relogio.

`_satisfaz` e onde cada condicao vira um sim/nao. Testar isso so pela e2e exigiria
fabricar estados reais (um botao que desabilita, um campo que ganha foco) numa
janela de verdade — caro, lento e dependente do app.
"""

from __future__ import annotations

from mcp_windows_uia.server import SUMIU, _descricao, _satisfaz
from mcp_windows_uia.uia.search import Criterios


def no(nome="x", *, st=(), val=None):
    n = {"ref": "w1-e1", "type": "Button", "name": nome, "st": list(st), "pat": []}
    if val is not None:
        n["val"] = val
    return n


# ------------------------------------------------------------------ appears/disappears


def test_appears_devolve_o_primeiro_candidato() -> None:
    a, b = no("a"), no("b")
    assert _satisfaz("appears", [a, b], None) is a


def test_appears_sem_candidato_nao_satisfaz() -> None:
    assert _satisfaz("appears", [], None) is None


def test_disappears_satisfaz_pela_ausencia() -> None:
    assert _satisfaz("disappears", [], None) is SUMIU


def test_disappears_nao_satisfaz_enquanto_houver_candidato() -> None:
    assert _satisfaz("disappears", [no()], None) is None


def test_sentinela_de_ausencia_nao_e_um_no() -> None:
    """Se SUMIU virasse um dict, ele sairia no campo `match` como se fosse elemento."""
    assert not isinstance(SUMIU, dict)


# ------------------------------------------------------------------------ estado


def test_enabled_ignora_o_desabilitado_e_pega_o_proximo() -> None:
    desabilitado, habilitado = no("a", st=["disabled"]), no("b", st=["enabled"])
    assert _satisfaz("enabled", [desabilitado, habilitado], None) is habilitado


def test_enabled_nao_satisfaz_se_todos_desabilitados() -> None:
    assert _satisfaz("enabled", [no(st=["disabled"])], None) is None


def test_focused_exige_o_estado_focused() -> None:
    sem, com = no("a", st=["enabled"]), no("b", st=["enabled", "focused"])
    assert _satisfaz("focused", [sem, com], None) is com
    assert _satisfaz("focused", [sem], None) is None


# ------------------------------------------------------------------------- valor


def test_value_equals_exige_igualdade_e_nao_prefixo() -> None:
    assert _satisfaz("value_equals", [no(val="abc")], "abc") is not None
    assert _satisfaz("value_equals", [no(val="abcd")], "abc") is None


def test_value_contains_aceita_substring() -> None:
    assert _satisfaz("value_contains", [no(val="prefixo-abc-sufixo")], "abc") is not None


def test_comparacao_de_valor_ignora_caixa() -> None:
    assert _satisfaz("value_equals", [no(val="Salvo")], "salvo") is not None
    assert _satisfaz("value_contains", [no(val="ARQUIVO SALVO")], "salvo") is not None


def test_no_sem_valor_nao_casa_por_engano() -> None:
    """`val` ausente vira "" — e "" esta contido em qualquer alvo se nao houver cuidado."""
    assert _satisfaz("value_contains", [no()], "abc") is None


# -------------------------------------------------------------------- descricao


def test_descricao_diz_o_que_se_esperava() -> None:
    """Entra na mensagem do TIMEOUT: "nao satisfeita" sem o que falhou nao ajuda ninguem."""
    d = _descricao("appears", Criterios(name="Salvar", match="contains"), None)
    assert "appears" in d and "Salvar" in d and "contains" in d


def test_descricao_por_ref_nomeia_a_ref() -> None:
    assert "w1-e3" in _descricao("enabled", Criterios(), "w1-e3")


def test_descricao_sem_criterio_nao_explode() -> None:
    assert _descricao("window_appears", Criterios(), None) == "window_appears"
