"""Fixtures compartilhadas dos testes e2e.

Escopo de sessao de proposito: cada modulo com fixture propria abriria seu proprio
Bloco de Notas, poluindo a area de trabalho de quem roda a suite. Um so, aberto e
fechado uma vez, tambem deixa o COM aquecido para a medicao de tempo do CA-21.
"""

from __future__ import annotations

import subprocess
import time

import pytest


@pytest.fixture(scope="session")
def sta():
    """Todas as chamadas COM da suite acontecem nesta thread, em STA."""
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


@pytest.fixture(scope="session")
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
