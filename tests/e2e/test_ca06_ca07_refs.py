"""Ciclo de vida de refs contra janelas reais. CA-06 e CA-07.

O CA-07 nao espera um re-render acontecer por sorte. Ele envenena o `runtime_id`
guardado, que e exatamente o que o Windows faz quando o app recria o controle, e
cobra que o rebind reencontre o MESMO elemento logico na janela de verdade.
"""

from __future__ import annotations

import ctypes
import subprocess
import time

import pytest

from tests.e2e.test_tool_get_tree import notepad, servidor  # noqa: F401

pytestmark = pytest.mark.e2e

WM_CLOSE = 0x0010


def _hwnds_do_bloco_de_notas() -> set[int]:
    from mcp_windows_uia.uia.windows import enumerate_windows

    return {
        w.hwnd
        for w in enumerate_windows()
        if (w.process or "").lower() == "notepad.exe" and w.title
    }


@pytest.fixture()
def janela_descartavel():
    """Um Bloco de Notas so para ser fechado no meio do teste.

    Casado por HWND NOVO, e nao por `proc.pid`: no Windows 11 o notepad.exe do
    System32 e alias de execucao e delega para o app empacotado, entao o PID da
    janela e outro. Isso ja mordeu este projeto uma vez.
    """
    from mcp_windows_uia.uia.windows import enumerate_windows

    antes = _hwnds_do_bloco_de_notas()
    proc = subprocess.Popen(["notepad.exe"])

    janela = None
    for _ in range(50):
        time.sleep(0.2)
        novos = _hwnds_do_bloco_de_notas() - antes
        if novos:
            hwnd = next(iter(novos))
            janela = next(w for w in enumerate_windows() if w.hwnd == hwnd)
            break
    if janela is None:
        proc.terminate()
        pytest.skip("segundo Bloco de Notas nao abriu a tempo")

    yield janela

    if ctypes.windll.user32.IsWindow(janela.hwnd):
        ctypes.windll.user32.PostMessageW(janela.hwnd, WM_CLOSE, 0, 0)
    proc.terminate()


async def _ref_do_documento(wref: str) -> str:
    """Ref da area de texto, esperando a arvore existir.

    Fechar uma janela do Bloco de Notas esvazia por um instante a arvore da OUTRA —
    e o mesmo processo. Medido: buscar direto passa 2 de 3 vezes, e um teste que as
    vezes passa nao prova nada. Esperar e para o que a §8.6 existe.
    """
    from mcp_windows_uia.server import uia_wait_for

    pronto = await uia_wait_for(
        window_ref=wref, condition="appears", control_type="Document", timeout_ms=5000
    )
    assert pronto["ok"] is True, pronto
    return pronto["match"]["ref"]


async def test_ca06_ref_de_janela_fechada_e_erro_acionavel(  # noqa: F811
    servidor, janela_descartavel
) -> None:
    """CA-06: erro classificado, hint que nomeia a tool seguinte, servidor vivo."""
    from mcp_windows_uia.server import uia_get_value, uia_list_windows

    wref = servidor.refs.window_ref(hwnd=janela_descartavel.hwnd)

    # A JANELA existe antes da arvore UIA dela. Buscar direto depois de abrir devolve
    # ELEMENT_NOT_FOUND com `visited: 0` — medido: passa sozinho e falha na suite
    # inteira, que e o pior tipo de teste. Esperar e literalmente para o que a §8.6
    # existe, entao o teste usa a propria tool.
    ref = await _ref_do_documento(wref)

    ctypes.windll.user32.PostMessageW(janela_descartavel.hwnd, WM_CLOSE, 0, 0)
    for _ in range(50):
        time.sleep(0.1)
        if not ctypes.windll.user32.IsWindow(janela_descartavel.hwnd):
            break
    assert not ctypes.windll.user32.IsWindow(janela_descartavel.hwnd)

    r = await uia_get_value(ref=ref)

    assert r["ok"] is False
    assert r["error"]["code"] in ("STALE_REF", "WINDOW_CLOSED", "REF_NOT_FOUND")
    assert "uia_list_windows" in r["error"]["hint"] or "uia_get_tree" in r["error"]["hint"]

    seguinte = await uia_list_windows()
    assert seguinte["ok"] is True, "o servidor tem de sobreviver ao erro"


async def test_ca07_rebind_reencontra_o_mesmo_elemento(servidor, notepad) -> None:  # noqa: F811
    """CA-07: a ref sobrevive ao elemento ser recriado, e diz que sobreviveu.

    Envenenar o runtime_id e o que torna isto deterministico. Esperar um re-render
    espontaneo dependeria de sorte, e um teste que as vezes nao exercita o rebind e
    um teste que nao prova nada.
    """
    from mcp_windows_uia.server import uia_get_value

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    ref = await _ref_do_documento(wref)
    nome_original = (await uia_get_value(ref=ref))["name"]

    antes = await uia_get_value(ref=ref)
    assert antes["rebound"] is False, "sem mexer em nada, o probe tem de acertar"

    entrada = servidor.refs.get(ref)
    entrada.runtime_id = (0xDEAD, 0xBEEF)

    depois = await uia_get_value(ref=ref)

    assert depois["ok"] is True, depois
    assert depois["rebound"] is True, "achou pelo caminho errado: nao houve rebind"
    assert depois["name"] == nome_original
    assert depois["ref"] == ref, "a ref entregue ao agente nao pode mudar"


async def test_ca07_apos_rebind_a_ref_continua_unica(servidor, notepad) -> None:  # noqa: F811
    """O indice por (hwnd, runtime_id) tem de acompanhar o rebind.

    Se nao acompanhar, a proxima captura da arvore cunha uma ref NOVA para o mesmo
    elemento e o agente fica com duas para a mesma coisa.
    """
    from mcp_windows_uia.server import uia_find_elements, uia_get_value

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    ref = await _ref_do_documento(wref)

    servidor.refs.get(ref).runtime_id = (0xDEAD, 0xBEEF)
    await uia_get_value(ref=ref)

    de_novo = (await uia_find_elements(window_ref=wref, control_type="Document"))["matches"][0]
    assert de_novo["ref"] == ref


async def test_rebind_nao_e_usado_pelo_wait_for(servidor, notepad) -> None:  # noqa: F811
    """'disappears' espera o ponteiro morrer. Rebind acharia um substituto para sempre.

    Com o runtime_id envenenado, `uia_get_value` rebinda e responde; o `uia_wait_for`
    sobre a mesma ref tem de continuar enxergando o elemento como presente pelo estado
    dele, sem nunca dar a condicao por satisfeita.
    """
    from mcp_windows_uia.server import uia_wait_for

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    ref = await _ref_do_documento(wref)

    r = await uia_wait_for(ref=ref, condition="disappears", timeout_ms=400)

    assert r["ok"] is False
    assert r["error"]["code"] == "TIMEOUT", "o elemento esta la; sumir seria mentira"
