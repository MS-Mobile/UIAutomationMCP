from __future__ import annotations

import pytest

from mcp_windows_uia.uia.dpi import (
    VirtualDesktop,
    center_of,
    init_dpi_awareness,
    to_absolute_normalized,
)


def test_center_of_um_retangulo() -> None:
    assert center_of((100, 200, 300, 400)) == (200, 300)


def test_center_of_com_coordenadas_negativas() -> None:
    """Monitor a esquerda do primario produz X negativo. Spec §4.1."""
    assert center_of((-1920, 0, -920, 500)) == (-1420, 250)


def test_normalizacao_mapeia_canto_superior_esquerdo_para_zero() -> None:
    vd = VirtualDesktop(left=0, top=0, width=1920, height=1080)
    assert to_absolute_normalized(0, 0, vd) == (0, 0)


def test_normalizacao_mapeia_canto_inferior_direito_para_65535() -> None:
    vd = VirtualDesktop(left=0, top=0, width=1920, height=1080)
    assert to_absolute_normalized(1919, 1079, vd) == (65535, 65535)


def test_normalizacao_respeita_origem_negativa() -> None:
    """Desktop virtual comecando em -1920: esse ponto e o zero normalizado."""
    vd = VirtualDesktop(left=-1920, top=0, width=3840, height=1080)
    assert to_absolute_normalized(-1920, 0, vd) == (0, 0)
    nx, _ = to_absolute_normalized(1919, 0, vd)
    assert nx == 65535


def test_normalizacao_do_ponto_medio() -> None:
    """O meio da tela cai no meio da faixa. 65535 e impar, entao o ponto exato e
    x.5 e o arredondamento pode ir para qualquer lado — meio pixel nao importa."""
    vd = VirtualDesktop(left=0, top=0, width=1921, height=1081)
    nx, ny = to_absolute_normalized(960, 540, vd)
    assert abs(nx - 32767.5) <= 0.5
    assert abs(ny - 32767.5) <= 0.5


def test_normalizacao_e_monotonica() -> None:
    vd = VirtualDesktop(left=0, top=0, width=1920, height=1080)
    xs = [to_absolute_normalized(x, 0, vd)[0] for x in range(0, 1920, 240)]
    assert xs == sorted(xs)
    assert len(set(xs)) == len(xs)


def test_desktop_de_largura_um_nao_divide_por_zero() -> None:
    vd = VirtualDesktop(left=0, top=0, width=1, height=1)
    assert to_absolute_normalized(0, 0, vd) == (0, 0)


def test_normalizacao_prende_fora_dos_limites() -> None:
    vd = VirtualDesktop(left=0, top=0, width=1920, height=1080)
    assert to_absolute_normalized(-500, -500, vd) == (0, 0)
    assert to_absolute_normalized(99999, 99999, vd) == (65535, 65535)


@pytest.mark.e2e
def test_init_dpi_awareness_reporta_o_metodo_usado() -> None:
    assert init_dpi_awareness() in {"PerMonitorV2", "PerMonitor", "already-set", "unavailable"}


@pytest.mark.e2e
def test_virtual_desktop_real_tem_area_positiva() -> None:
    vd = VirtualDesktop.current()
    assert vd.width > 0 and vd.height > 0
