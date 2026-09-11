"""Serializacao do Node contra um provider real. Guarda da regra de patterns da §5.1.

Por que este arquivo existe: as propriedades derivadas de pattern (ToggleState,
ValueValue, IsReadOnly...) nao sao atributos `Cached*` da interface, e o provider
devolve um DEFAULT — nao vazio — quando o pattern nao existe. Um fake que expunha
tudo como atributo escondeu isso por tres tasks. So um provider de verdade prova
que o portao de disponibilidade esta certo.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

# Estado que so pode aparecer se o Node declarar o pattern correspondente em `pat`.
EXIGE_PATTERN = {
    "checked": "Toggle",
    "unchecked": "Toggle",
    "indeterminate": "Toggle",
    "expanded": "ExpandCollapse",
    "collapsed": "ExpandCollapse",
    "selected": "SelectionItem",
    "readonly": "Value",
}


@pytest.fixture(scope="module")
def arvore(bloco_de_notas):
    from mcp_windows_uia.uia.tree import capturar_janela

    return capturar_janela(bloco_de_notas.hwnd, filtro="all", max_nodes=400)["nodes"]


def test_nenhum_estado_derivado_aparece_sem_o_pattern(arvore) -> None:
    """O bug real: sem o portao, todo Pane e Button saia 'indeterminate' e 'readonly'."""
    culpados = [
        (n["type"], n["name"], estado)
        for n in arvore
        for estado, pattern in EXIGE_PATTERN.items()
        if estado in n["st"] and pattern not in n["pat"]
    ]
    assert culpados == [], f"estados sem o pattern que os justifica: {culpados[:5]}"


def test_a_arvore_nao_e_uma_parede_de_readonly(arvore) -> None:
    """Guarda grosseira contra a regressao voltar em outra forma.

    Se quase todo no sair 'readonly', a leitura esta pegando o default de novo — e a
    regra da §6.2 que rebaixa Value a nao-acionavel para de valer.
    """
    readonly = sum(1 for n in arvore if "readonly" in n["st"])
    assert readonly < len(arvore) / 2, f"{readonly} de {len(arvore)} nos marcados readonly"


def test_valor_e_lido_de_verdade_quando_o_pattern_existe(arvore) -> None:
    """O outro lado: o portao nao pode ter silenciado leituras legitimas.

    A barra de titulo do Bloco de Notas suporta Value e expoe o titulo da janela.
    """
    com_value = [n for n in arvore if "Value" in n["pat"]]
    assert com_value, "nenhum no com Value pattern — a captura mudou de forma"
    assert any(n.get("val") for n in com_value), (
        "todo no com Value saiu sem 'val': a leitura da propriedade nao esta chegando"
    )


def test_patterns_nao_saem_vazios_na_arvore_toda(arvore) -> None:
    """Se `pat` for [] em todo mundo, a disponibilidade voltou a ser lida por getattr."""
    assert any(n["pat"] for n in arvore)
