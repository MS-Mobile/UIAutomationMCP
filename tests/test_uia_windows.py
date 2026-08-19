from __future__ import annotations

import os

import pytest

from mcp_windows_uia.uia.windows import (
    enumerate_windows,
    is_session_interactive,
    process_elevated,
    server_is_elevated,
    window_is_alive,
)

pytestmark = pytest.mark.e2e


def test_sessao_interativa_e_detectada() -> None:
    assert is_session_interactive() is True


def test_elevacao_do_servidor_e_um_bool() -> None:
    assert isinstance(server_is_elevated(), bool)


def test_processo_do_proprio_servidor_nao_e_inacessivel() -> None:
    assert process_elevated(os.getpid()) is False


def test_pid_inexistente_e_tratado_sem_excecao() -> None:
    assert process_elevated(999999) in (True, False)


def test_enumerate_devolve_janelas_com_campos_preenchidos() -> None:
    janelas = enumerate_windows()
    assert janelas, "nenhuma janela visivel — a sessao esta desbloqueada?"
    for j in janelas:
        assert j.hwnd > 0
        assert j.pid > 0
        assert len(j.rect) == 4
        assert j.dpi > 0


def test_enumerate_sem_hidden_exige_titulo() -> None:
    assert all(j.title for j in enumerate_windows(include_hidden=False))


def test_include_hidden_retorna_pelo_menos_tantas_janelas() -> None:
    assert len(enumerate_windows(include_hidden=True)) >= len(enumerate_windows())


def test_window_is_alive_para_janela_real_e_para_lixo() -> None:
    janelas = enumerate_windows()
    assert window_is_alive(janelas[0].hwnd) is True
    assert window_is_alive(1) is False
