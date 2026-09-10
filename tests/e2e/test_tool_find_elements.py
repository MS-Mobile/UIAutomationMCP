"""uia_find_elements contra janela real. Cobre CA-24.

Reaproveita as fixtures de sessao do conftest (`bloco_de_notas`) e o `servidor` do
modulo de uia_get_tree: abrir um segundo Bloco de Notas so para este arquivo
poluiria a area de trabalho de quem roda a suite e nao acrescentaria nada.
"""

from __future__ import annotations

import collections

import pytest

from tests.e2e.test_tool_get_tree import notepad, servidor  # noqa: F401

pytestmark = pytest.mark.e2e


@pytest.fixture()
def wref(servidor, notepad) -> str:  # noqa: F811
    return servidor.refs.window_ref(hwnd=notepad.hwnd)


async def test_acha_por_control_type(servidor, wref) -> None:  # noqa: F811
    """control_type e criterio EXATO: vira condicao nativa, o provider filtra.

    Alvo `Button` e nao `Edit`: o Bloco de Notas do Windows 11 nao tem nenhum
    ControlType Edit — a area de texto e um `Document` (RichEditD2DPT) dentro de um
    `Pane` NotepadTextBox. Medido: 17 Buttons, zero Edits.
    """
    from mcp_windows_uia.server import uia_find_elements

    r = await uia_find_elements(window_ref=wref, control_type="Button")

    assert r["ok"] is True, r
    assert len(r["matches"]) >= 1
    assert all(m["type"] == "Button" for m in r["matches"])
    assert r["stats"]["returned"] == len(r["matches"])


async def test_matches_trazem_path_para_desambiguar(servidor, wref) -> None:  # noqa: F811
    """Spec §8.4: path evita uma chamada extra so para o agente escolher."""
    from mcp_windows_uia.server import uia_find_elements

    r = await uia_find_elements(window_ref=wref, control_type="Button")

    assert r["matches"]
    assert all("path" in m for m in r["matches"])
    assert all(m["path"] for m in r["matches"]), "path veio vazio"
    # A trilha termina no proprio elemento (exemplo da §8.4: "... > Button").
    assert all(m["path"].split(" > ")[-1] == m["type"] for m in r["matches"])


async def test_ca24_homonimos_sao_distinguiveis_pelo_path(servidor, wref) -> None:  # noqa: F811
    """CA-24: elementos de mesmo `name` voltam TODOS, separados pelo `path`.

    E a unica razao de `_trilha_ancestral` existir e gastar RPCs de `Current*`:
    sem ela o agente receberia N matches indistinguiveis e teria de chamar
    uia_get_tree so para escolher um.
    """
    from mcp_windows_uia.server import uia_find_elements

    r = await uia_find_elements(
        window_ref=wref, control_type="Pane", only_interactive=False, max_results=50
    )
    assert r["ok"] is True, r

    por_nome: dict[str, list[str]] = collections.defaultdict(list)
    for m in r["matches"]:
        por_nome[m["name"]].append(m["path"])

    homonimos = {nome: paths for nome, paths in por_nome.items() if len(paths) > 1}
    assert homonimos, "a janela nao trouxe dois Panes homonimos: o teste ficaria vacuo"
    assert any(len(set(paths)) > 1 for paths in homonimos.values()), (
        f"homonimos vieram com paths identicos, o path nao desambigua: {homonimos}"
    )


async def test_zero_matches_e_erro_com_hint(servidor, wref) -> None:  # noqa: F811
    """Spec §8.4: zero resultados e ELEMENT_NOT_FOUND, nao lista vazia."""
    from mcp_windows_uia.server import uia_find_elements

    r = await uia_find_elements(window_ref=wref, name="botao-que-nao-existe-xyz")

    assert r["ok"] is False
    assert r["error"]["code"] == "ELEMENT_NOT_FOUND"
    assert "uia_get_tree" in r["error"]["hint"]


async def test_sem_criterio_algum_e_invalid_argument(servidor, wref) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_find_elements

    r = await uia_find_elements(window_ref=wref)

    assert r["ok"] is False
    assert r["error"]["code"] == "INVALID_ARGUMENT"


async def test_exhaustive_verdadeiro_quando_a_busca_cobre_tudo(servidor, wref) -> None:  # noqa: F811
    """Criterio nativo numa janela pequena: nada ficou por olhar."""
    from mcp_windows_uia.server import uia_find_elements

    r = await uia_find_elements(window_ref=wref, control_type="Button", max_results=100)

    assert r["ok"] is True, r
    assert r["stats"]["exhaustive"] is True


async def test_exhaustive_e_honesto_quando_o_teto_estoura(
    servidor, notepad, wref, monkeypatch  # noqa: F811
) -> None:
    """Sem criterio nativo a busca e uma varredura com teto — e ela admite o corte.

    `name` com match='contains' nao vira condicao nativa: o ramo que sobra e a
    varredura por nivel com teto de visita. Com o teto rebaixado ela para no meio,
    e `stats.exhaustive` tem de dizer isso — travar 18 s ou mentir "exhaustive:true"
    sao as duas falhas que este teste existe para impedir.
    """
    from mcp_windows_uia.server import uia_find_elements
    from mcp_windows_uia.uia import search

    monkeypatch.setattr(search, "TETO_DE_BUSCA", 2)

    r = await uia_find_elements(
        window_ref=wref,
        name=notepad.title,
        match="contains",
        only_interactive=False,
        max_results=50,
    )

    assert r["ok"] is True, r
    assert r["stats"]["visited"] <= 2, r["stats"]
    assert r["stats"]["exhaustive"] is False, r["stats"]
