"""uia_get_text contra janela real. Cobre CA-12 e a paginacao por offset da §8.3.

O Bloco de Notas da sessao abre vazio, entao o que se le aqui e a moldura da janela
(titulo, aba, menu, barra de status). Isso e suficiente e ate preferivel: os testes
nao dependem de conteudo digitado nem do idioma do Windows.
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


async def test_le_texto_da_janela(servidor, wref) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_get_text

    r = await uia_get_text(window_ref=wref)

    assert r["ok"] is True, r
    assert r["window_ref"] == wref
    assert r["chars"] == len(r["text"])
    assert r["text"].strip(), "janela real sem texto algum e sinal de extracao quebrada"


async def test_ca12_sem_bloco_duplicado_em_sequencia(servidor, wref) -> None:  # noqa: F811
    """CA-12: o padrao ancestral/descendente sai como duas linhas iguais seguidas."""
    from mcp_windows_uia.server import uia_get_text

    r = await uia_get_text(window_ref=wref)

    linhas = [linha for linha in r["text"].split("\n") if linha.strip()]
    for i in range(1, len(linhas)):
        assert linhas[i] != linhas[i - 1], f"bloco duplicado: {linhas[i]!r}"


async def test_offset_pagina_sobre_o_mesmo_documento(servidor, wref) -> None:  # noqa: F811
    """As duas paginas juntas tem de dar o inicio do texto inteiro.

    Este teste cobre a FATIA, nao a fonte: a area de texto do Bloco de Notas abre
    vazia, entao cortar o GetText do TextPattern nao mudaria nada aqui e o bug
    passaria batido. Quem prova o limite da fonte e
    tests/test_leitor_com.py::test_limite_do_gettext_e_o_teto_duro_e_nao_a_pagina —
    medido: com o limite sabotado, este teste continuou verde.
    """
    from mcp_windows_uia.server import uia_get_text

    inteiro = await uia_get_text(window_ref=wref)
    if len(inteiro["text"]) < 12:
        pytest.skip("texto curto demais para paginar")

    p1 = await uia_get_text(window_ref=wref, max_chars=5)
    assert p1["truncated"] is True
    assert p1["next_offset"] == 5

    p2 = await uia_get_text(window_ref=wref, max_chars=5, offset=p1["next_offset"])
    assert p1["text"] + p2["text"] == inteiro["text"][:10]


async def test_offset_no_fim_nao_devolve_next_offset(servidor, wref) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_get_text

    inteiro = await uia_get_text(window_ref=wref)
    fim = await uia_get_text(window_ref=wref, offset=max(0, len(inteiro["text"]) - 1))

    assert fim["truncated"] is False
    assert fim["next_offset"] is None


async def test_include_refs_prefixa_e_as_refs_resolvem(servidor, wref) -> None:  # noqa: F811
    """§8.3: include_refs existe para casar texto -> elemento. Ref que nao resolve nao serve."""
    from mcp_windows_uia.server import uia_get_text, uia_get_value

    r = await uia_get_text(window_ref=wref, include_refs=True, max_chars=40000)
    assert r["ok"] is True, r

    linhas = [linha for linha in r["text"].split("\n") if linha.strip()]
    assert linhas, "sem blocos para verificar"
    assert all(linha.startswith("[") for linha in linhas), linhas[:5]

    ref = linhas[0][1 : linhas[0].index("]")]
    lido = await uia_get_value(ref=ref)
    assert lido["ok"] is True, lido


async def test_sem_include_refs_o_texto_sai_limpo(servidor, wref) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_get_text

    r = await uia_get_text(window_ref=wref)
    assert not any(linha.startswith("[") for linha in r["text"].split("\n") if linha.strip())


async def test_root_ref_le_so_a_subarvore(servidor, wref) -> None:  # noqa: F811
    """A subarvore tem de ser subconjunto da janela, e derivar o window_ref sozinha."""
    from mcp_windows_uia.server import uia_find_elements, uia_get_text

    achados = await uia_find_elements(window_ref=wref, control_type="Document")
    assert achados["ok"] is True, achados
    ref = achados["matches"][0]["ref"]

    janela = await uia_get_text(window_ref=wref, max_chars=40000)
    sub = await uia_get_text(root_ref=ref, max_chars=40000)

    assert sub["ok"] is True, sub
    assert sub["window_ref"] == wref
    assert len(sub["text"]) <= len(janela["text"])


async def test_sem_window_ref_nem_root_ref_e_invalid_argument(servidor) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_get_text

    r = await uia_get_text()
    assert r["ok"] is False
    assert r["error"]["code"] == "INVALID_ARGUMENT"


async def test_root_ref_desconhecida_e_erro_acionavel(servidor) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_get_text

    r = await uia_get_text(root_ref="w1-e999999")
    assert r["ok"] is False
    assert r["error"]["code"] in ("REF_NOT_FOUND", "STALE_REF")


async def test_auditoria_registra_o_tamanho_e_nunca_o_texto(servidor, wref) -> None:  # noqa: F811
    """O texto e conteudo da tela do usuario e o JSONL fica em disco por dias."""
    from mcp_windows_uia.server import uia_get_text

    r = await uia_get_text(window_ref=wref)
    linha = [x for x in _linhas_de_auditoria(servidor) if x["tool"] == "uia_get_text"][-1]

    assert linha["result"] == "ok"
    assert linha["params"]["chars"] == r["chars"]
    assert linha["target"]["process"].lower() == "notepad.exe"
    assert "value" not in linha
    assert r["text"][:30].strip() not in json.dumps(linha, ensure_ascii=False)
