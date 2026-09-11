"""uia_wait_for contra janela real. Cobre CA-11.

O Bloco de Notas do Windows 11 nao tem ControlType `Edit`: a area de texto e um
`Document`. Por isso os testes de presenca buscam `Document`.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e.test_tool_get_tree import notepad, servidor  # noqa: F401

pytestmark = pytest.mark.e2e


def _linhas_de_auditoria(ctx) -> list[dict]:
    linhas = []
    for arquivo in sorted(ctx.config.audit.dir.glob("*.jsonl")):
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            if linha.strip():
                linhas.append(json.loads(linha))
    return linhas


@pytest.fixture()
def wref(servidor, notepad) -> str:  # noqa: F811
    return servidor.refs.window_ref(hwnd=notepad.hwnd)


async def test_elemento_ja_presente_satisfaz_rapido(servidor, wref) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_wait_for

    r = await uia_wait_for(
        window_ref=wref, condition="appears", control_type="Document", timeout_ms=3000
    )

    assert r["ok"] is True, r
    assert r["satisfied"] is True
    assert r["waited_ms"] < 3000
    assert r["polls"] == 1, "o que ja esta la nao precisa de segunda sondagem"
    assert "ref" in r["match"]


async def test_ca11_timeout_acionavel(servidor, wref) -> None:  # noqa: F811
    """CA-11: TIMEOUT em 1500-2500 ms, hint sugerindo uia_get_tree, polls > 5."""
    from mcp_windows_uia.server import uia_wait_for

    r = await uia_wait_for(
        window_ref=wref,
        condition="appears",
        name="elemento-inexistente-xyz",
        timeout_ms=1500,
    )

    assert r["ok"] is False
    assert r["error"]["code"] == "TIMEOUT"
    assert "uia_get_tree" in r["error"]["hint"]
    assert r["error"]["details"]["polls"] > 5
    assert 1500 <= r["error"]["details"]["waited_ms"] < 2500


async def test_timeout_diz_quantos_candidatos_viu(servidor, wref) -> None:  # noqa: F811
    """A diferenca entre "nunca apareceu" e "apareceu e nunca ficou pronto".

    As duas pedem correcoes opostas do agente: mudar o criterio, ou esperar mais.
    Sem este numero ele nao tem como escolher.
    """
    from mcp_windows_uia.server import uia_wait_for

    nenhum = await uia_wait_for(
        window_ref=wref, condition="appears", name="nao-existe-xyz", timeout_ms=300
    )
    presente = await uia_wait_for(
        window_ref=wref,
        condition="value_equals",
        control_type="Document",
        expected="valor-que-nunca-vai-existir",
        timeout_ms=300,
    )

    assert nenhum["error"]["details"]["last_seen_candidates"] == 0
    assert presente["error"]["details"]["last_seen_candidates"] >= 1
    assert presente["error"]["details"]["window_alive"] is True


async def test_auditoria_registra_a_chamada_e_nao_cada_sondagem(  # noqa: F811
    servidor, wref
) -> None:
    """Uma espera de 1,5 s sonda ~8 vezes. O JSONL nao pode virar 8 linhas.

    E por isso que `buscar_elementos` foi separado de `find_elements_impl`: com a
    auditoria dentro do nucleo, cada sondagem escreveria um `not_found` e o registro
    mentiria sobre quantas chamadas o agente fez.
    """
    from mcp_windows_uia.server import uia_wait_for

    antes = len([x for x in _linhas_de_auditoria(servidor) if x["tool"] == "uia_find_elements"])

    r = await uia_wait_for(
        window_ref=wref, condition="appears", name="nao-existe-xyz", timeout_ms=1500
    )
    assert r["error"]["details"]["polls"] > 5

    depois = [x for x in _linhas_de_auditoria(servidor) if x["tool"] == "uia_find_elements"]
    assert len(depois) == antes, "sondagem nao pode aparecer como chamada do agente"

    espera = [x for x in _linhas_de_auditoria(servidor) if x["tool"] == "uia_wait_for"][-1]
    assert espera["result"] == "error"
    assert espera["code"] == "TIMEOUT"


async def test_disappears_satisfaz_quando_nada_casa(servidor, wref) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_wait_for

    r = await uia_wait_for(
        window_ref=wref, condition="disappears", name="nunca-existiu-xyz", timeout_ms=1000
    )

    assert r["ok"] is True, r
    assert r["satisfied"] is True
    assert r["match"] is None, "ausencia nao tem elemento para devolver"


async def test_espera_sobre_uma_ref_especifica(servidor, wref) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_find_elements, uia_wait_for

    achados = await uia_find_elements(window_ref=wref, control_type="Document")
    ref = achados["matches"][0]["ref"]

    r = await uia_wait_for(ref=ref, condition="enabled", timeout_ms=2000)

    assert r["ok"] is True, r
    assert r["match"]["ref"] == ref


async def test_window_appears_acha_o_bloco_de_notas(servidor, notepad) -> None:  # noqa: F811
    """O titulo vem de agora, nao da fixture.

    `notepad.title` e um retrato do instante em que a janela nasceu: medido nesta
    maquina, ele vale 'Bloco de notas' e um segundo depois a janela ja se chama
    'Sem titulo - Bloco de notas'. Titulo de janela muda — que e justamente por que
    esperar por janela existe.
    """
    from mcp_windows_uia.server import uia_wait_for
    from mcp_windows_uia.uia.windows import enumerate_windows

    titulo = next(w.title for w in enumerate_windows() if w.hwnd == notepad.hwnd)

    r = await uia_wait_for(
        condition="window_appears", name=titulo, match="exact", timeout_ms=2000
    )

    assert r["ok"] is True, r
    assert r["match"]["name"] == titulo
    assert r["match"]["ref"].startswith("w")


async def test_window_appears_nao_precisa_de_window_ref(servidor) -> None:  # noqa: F811
    """A janela que ainda nao abriu nao tem ref: exigir uma seria impossivel de usar."""
    from mcp_windows_uia.server import uia_wait_for

    r = await uia_wait_for(
        condition="window_appears", name="janela-que-nao-existe-xyz", timeout_ms=300
    )

    assert r["ok"] is False
    assert r["error"]["code"] == "TIMEOUT"


async def test_condicao_desconhecida_e_invalid_argument(servidor, wref) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_wait_for

    r = await uia_wait_for(window_ref=wref, condition="faz_cafe", timeout_ms=500)

    assert r["ok"] is False
    assert r["error"]["code"] == "INVALID_ARGUMENT"
    assert "appears" in r["error"]["message"]


async def test_value_contains_sem_expected_e_invalid_argument(servidor, wref) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_wait_for

    r = await uia_wait_for(
        window_ref=wref, condition="value_contains", control_type="Document", timeout_ms=500
    )

    assert r["ok"] is False
    assert r["error"]["code"] == "INVALID_ARGUMENT"
    assert "expected" in r["error"]["message"]


async def test_sem_window_ref_nem_ref_e_invalid_argument(servidor) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_wait_for

    r = await uia_wait_for(condition="appears", control_type="Document", timeout_ms=500)

    assert r["ok"] is False
    assert r["error"]["code"] == "INVALID_ARGUMENT"


async def test_ref_desconhecida_falha_na_hora_e_nao_no_timeout(servidor) -> None:  # noqa: F811
    """Ref invalida e um fato, nao uma condicao que ainda pode virar verdadeira."""
    import time

    from mcp_windows_uia.server import uia_wait_for

    inicio = time.perf_counter()
    r = await uia_wait_for(ref="w1-e999999", condition="appears", timeout_ms=5000)
    decorrido = (time.perf_counter() - inicio) * 1000

    assert r["ok"] is False
    assert r["error"]["code"] in ("REF_NOT_FOUND", "STALE_REF")
    assert decorrido < 1000, "esperou o timeout inteiro por uma ref que nunca existiu"
