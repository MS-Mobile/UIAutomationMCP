"""uia_get_tree contra janela real. Cobre CA-08 (paginacao) e CA-13 (allowlist).

Chama a coroutine da tool direto, e nao pelo transporte stdio: o que esta em jogo
aqui e o corpo da tool (policy + worker + captura + RefStore + cursor). Handshake,
schema e higiene de stdout ja tem cobertura propria em test_ca01_ca22.py.
"""

from __future__ import annotations

import pytest

from mcp_windows_uia.budget import encode_cursor

pytestmark = pytest.mark.e2e


def _linhas_de_auditoria(ctx) -> list[dict]:
    """Le o JSONL de auditoria do contexto de teste."""
    import json

    linhas = []
    for arquivo in sorted(ctx.config.audit.dir.glob("*.jsonl")):
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            if linha.strip():
                linhas.append(json.loads(linha))
    return linhas


@pytest.fixture(scope="module")
def servidor(sta, tmp_path_factory):
    """ServerContext com allowlist permitindo Bloco de Notas e Explorador."""
    from mcp_windows_uia.config import (
        AllowlistConfig,
        AuditConfig,
        Config,
        DenylistConfig,
        KeysConfig,
        ServerConfig,
    )
    from mcp_windows_uia.context import ServerContext, set_context

    cfg = Config(
        server=ServerConfig(),
        allowlist=AllowlistConfig(processes=frozenset({"notepad.exe", "explorer.exe"})),
        denylist=DenylistConfig(processes=frozenset()),
        audit=AuditConfig(dir=tmp_path_factory.mktemp("audit")),
        keys=KeysConfig(),
    )
    ctx = ServerContext(cfg)
    ctx.start()
    set_context(ctx)
    yield ctx
    ctx.shutdown()


@pytest.fixture(scope="session")
def notepad(bloco_de_notas):
    """Apelido de `bloco_de_notas`, para os modulos e2e das tools seguintes.

    Nao abre um Bloco de Notas proprio: e a MESMA janela de sessao do conftest. O
    apelido existe so para que `from tests.e2e.test_tool_get_tree import notepad,
    servidor` funcione nos modulos que reaproveitam a fixture `servidor` daqui.
    """
    return bloco_de_notas


@pytest.fixture()
def wref(servidor, notepad) -> str:
    return servidor.refs.window_ref(hwnd=notepad.hwnd)


async def test_get_tree_devolve_arvore_com_refs(servidor, wref) -> None:
    from mcp_windows_uia.server import uia_get_tree

    r = await uia_get_tree(window_ref=wref)

    assert r["ok"] is True, r
    assert r["window_ref"] == wref
    assert len(r["nodes"]) > 0
    assert "stats" in r
    assert isinstance(r["tree_version"], int)
    for node in r["nodes"]:
        assert node["ref"].startswith(f"{wref}-e")


async def test_ca08_truncamento_sinaliza_e_o_cursor_avanca(servidor, wref) -> None:
    """CA-08: pagina 1 trunca com cursor; pagina 2 traz nos DIFERENTES."""
    from mcp_windows_uia.server import uia_get_tree

    p1 = await uia_get_tree(window_ref=wref, filter="all", max_nodes=5)
    assert p1["ok"] is True, p1
    assert p1["stats"]["returned"] == 5
    assert p1["stats"]["truncated"] is True
    assert p1["stats"]["next_cursor"] is not None
    assert "hint" in p1["stats"]

    p2 = await uia_get_tree(
        window_ref=wref, filter="all", max_nodes=5, cursor=p1["stats"]["next_cursor"]
    )
    assert p2["ok"] is True, p2
    assert p2["nodes"], "a pagina de continuacao veio vazia"

    refs1 = {n["ref"] for n in p1["nodes"]}
    refs2 = {n["ref"] for n in p2["nodes"]}
    assert refs1 & refs2 == set(), "as paginas se sobrepoem"

    # A continuacao NAO incrementa a versao: e ela que valida o cursor.
    assert p2["tree_version"] == p1["tree_version"]


async def test_ca08_paginas_reconstroem_a_captura_sem_paginacao(servidor, wref) -> None:
    """A concatenacao das paginas bate com a captura unica de mesmo tamanho."""
    from mcp_windows_uia.server import uia_get_tree

    inteiro = await uia_get_tree(window_ref=wref, filter="all", max_nodes=10)
    esperado = [n["ref"] for n in inteiro["nodes"]]

    p1 = await uia_get_tree(window_ref=wref, filter="all", max_nodes=5)
    p2 = await uia_get_tree(
        window_ref=wref, filter="all", max_nodes=5, cursor=p1["stats"]["next_cursor"]
    )
    obtido = [n["ref"] for n in p1["nodes"]] + [n["ref"] for n in p2["nodes"]]

    assert obtido == esperado


async def test_ca13_janela_fora_da_allowlist_e_negada(servidor) -> None:
    """CA-13: APP_NOT_ALLOWED nomeia o processo e instrui sobre a config."""
    from mcp_windows_uia.server import uia_get_tree
    from mcp_windows_uia.uia.windows import enumerate_windows

    permitidos = {"notepad.exe", "explorer.exe"}
    fora = [w for w in enumerate_windows() if (w.process or "").lower() not in permitidos]
    if not fora:
        pytest.skip("nenhuma janela fora da allowlist para testar")

    alvo = fora[0]
    r = await uia_get_tree(window_ref=servidor.refs.window_ref(hwnd=alvo.hwnd))

    assert r["ok"] is False
    assert r["error"]["code"] == "APP_NOT_ALLOWED"
    assert "config.toml" in r["error"]["hint"]
    assert alvo.process in r["error"]["message"] or alvo.process in str(r["error"]["details"])

    # A terceira parte do CA-13: negacao de policy vira linha propria na auditoria.
    negadas = [
        linha for linha in _linhas_de_auditoria(servidor)
        if linha.get("result") == "denied" and linha.get("code") == "APP_NOT_ALLOWED"
    ]
    assert negadas, 'CA-13 exige uma linha "result":"denied" no log de auditoria'
    assert negadas[-1]["target"]["process"] == alvo.process
    assert "value" not in negadas[-1]


async def test_window_ref_desconhecida_e_erro_acionavel(servidor) -> None:
    from mcp_windows_uia.server import uia_get_tree

    r = await uia_get_tree(window_ref="w99999")

    assert r["ok"] is False
    assert r["error"]["code"] in ("WINDOW_NOT_FOUND", "REF_NOT_FOUND")
    assert "uia_list_windows" in r["error"]["hint"]


async def test_cursor_corrompido_vira_invalid_argument(servidor, wref) -> None:
    from mcp_windows_uia.server import uia_get_tree

    r = await uia_get_tree(window_ref=wref, cursor="isto-nao-e-um-cursor!!")

    assert r["ok"] is False
    assert r["error"]["code"] == "INVALID_ARGUMENT"
    assert "cursor" in r["error"]["hint"].lower()


async def test_cursor_de_outra_janela_e_recusado(servidor, wref) -> None:
    """Continuar com o cursor da janela errada descreveria uma travessia que nao existe."""
    from mcp_windows_uia.server import uia_get_tree

    alheio = encode_cursor("w99999", tree_version=1, position=3)
    r = await uia_get_tree(window_ref=wref, cursor=alheio)

    assert r["ok"] is False
    assert r["error"]["code"] == "INVALID_ARGUMENT"
    assert "cursor" in r["error"]["hint"].lower()


async def test_cursor_de_versao_vencida_e_recusado(servidor, wref) -> None:
    """Um uia_get_tree novo invalida os cursores em voo da captura anterior."""
    from mcp_windows_uia.server import uia_get_tree

    p1 = await uia_get_tree(window_ref=wref, filter="all", max_nodes=5)
    assert p1["stats"]["next_cursor"] is not None

    await uia_get_tree(window_ref=wref, filter="all", max_nodes=5)  # bump da versao

    r = await uia_get_tree(
        window_ref=wref, filter="all", max_nodes=5, cursor=p1["stats"]["next_cursor"]
    )
    assert r["ok"] is False
    assert r["error"]["code"] == "INVALID_ARGUMENT"
    assert "cursor" in r["error"]["hint"].lower()
