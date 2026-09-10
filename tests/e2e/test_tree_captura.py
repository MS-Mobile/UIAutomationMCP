"""Captura contra janela real. Cobre CA-02 e CA-21 da spec."""

from __future__ import annotations

import subprocess
import time

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def sta():
    import comtypes

    from mcp_windows_uia.uia import core

    comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
    yield
    core.reset_for_tests()


def _janelas_do_bloco_de_notas() -> dict[int, object]:
    from mcp_windows_uia.uia.windows import enumerate_windows

    return {
        w.hwnd: w
        for w in enumerate_windows()
        if (w.process or "").lower() == "notepad.exe"
    }


@pytest.fixture(scope="module")
def bloco_de_notas(sta):
    """Abre um Bloco de Notas e o fecha ao fim.

    Nao da para casar a janela por `proc.pid`: no Windows 11 o `notepad.exe` do
    System32 e um alias de execucao que delega para o app empacotado
    (`Notepad.exe` de WindowsApps) e o PID da janela e outro. Alem disso o app
    restaura as janelas da sessao anterior, entao "a janela do meu PID" tambem
    seria ambigua. Casamos por HWND novo, que e exato nos dois casos.
    """
    import psutil

    antes = _janelas_do_bloco_de_notas()
    ja_havia = bool(antes)

    proc = subprocess.Popen(["notepad.exe"])
    janela = None
    for _ in range(50):
        time.sleep(0.2)
        novas = [w for h, w in _janelas_do_bloco_de_notas().items() if h not in antes and w.title]
        if novas:
            janela = novas[0]
            break
    if janela is None:
        proc.terminate()
        pytest.skip("Bloco de Notas nao abriu a tempo")

    yield janela

    proc.terminate()
    if ja_havia:
        # O usuario ja tinha Bloco de Notas aberto: fecha so a janela que abrimos.
        import ctypes

        ctypes.windll.user32.PostMessageW(janela.hwnd, 0x0010, 0, 0)  # WM_CLOSE
    else:
        # Nao havia nenhum: tudo que esta na tela veio de nos (inclusive as janelas
        # que o app restaurou da sessao anterior). Derruba o processo inteiro.
        for p in psutil.process_iter(["pid", "name"]):
            if (p.info["name"] or "").lower() == "notepad.exe":
                try:
                    p.kill()
                except psutil.Error:
                    pass


def test_ca02_arvore_interativa_e_enxuta(bloco_de_notas) -> None:
    """CA-02: <=60 nos, uma area de edicao, sem truncar, nenhum no desabilitado."""
    from mcp_windows_uia.uia.tree import capturar_janela

    r = capturar_janela(bloco_de_notas.hwnd, filtro="interactive")

    assert len(r["nodes"]) <= 60
    assert r["stats"]["truncated"] is False

    editaveis = [n for n in r["nodes"] if n["type"] in ("Edit", "Document")]
    assert len(editaveis) >= 1

    for n in r["nodes"]:
        assert "disabled" not in n["st"]


def test_ca21_performance_da_captura(bloco_de_notas) -> None:
    """CA-21: p95 < 1200 ms em 5 execucoes."""
    from mcp_windows_uia.uia.tree import capturar_janela

    tempos = []
    for _ in range(5):
        t0 = time.perf_counter()
        capturar_janela(bloco_de_notas.hwnd, filtro="interactive", max_nodes=200)
        tempos.append((time.perf_counter() - t0) * 1000)

    assert max(tempos) < 1200, f"tempos: {tempos}"


def test_ca09_max_nodes_absurdo_e_limitado_a_1500(bloco_de_notas) -> None:
    """CA-09: pedir 99999 nunca produz mais de 1500."""
    from mcp_windows_uia.uia.tree import capturar_janela

    r = capturar_janela(bloco_de_notas.hwnd, filtro="all", max_nodes=99999)
    assert len(r["nodes"]) <= 1500


def test_captura_devolve_refs_no_formato_da_spec(bloco_de_notas) -> None:
    from mcp_windows_uia.uia.tree import capturar_janela

    r = capturar_janela(bloco_de_notas.hwnd, filtro="interactive")
    for n in r["nodes"]:
        assert n["ref"].startswith("w")
        assert "-e" in n["ref"]
