"""DPI awareness, desktop virtual e conversao de coordenadas. Spec §4.1.

init_dpi_awareness() DEVE rodar antes de qualquer chamada Win32/UIA que envolva
coordenadas. Sem Per-Monitor V2, BoundingRectangle vem virtualizado e o clique de
fallback erra o alvo em telas com escala diferente de 100% (CA-17).
"""

from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass

_log = logging.getLogger(__name__)

# Indices de GetSystemMetrics, spec §4.1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
PROCESS_PER_MONITOR_DPI_AWARE = 2
E_ACCESSDENIED = -2147024891  # ja definido: chamada repetida


def init_dpi_awareness() -> str:
    """Declara Per-Monitor V2. Retorna qual caminho funcionou (para diagnostico)."""
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ):
            return "PerMonitorV2"
    except (AttributeError, OSError) as exc:
        _log.debug("SetProcessDpiAwarenessContext unavailable: %s", exc)

    try:
        hr = ctypes.windll.shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
        if hr == 0:
            return "PerMonitor"
        if hr == E_ACCESSDENIED:
            return "already-set"
    except (AttributeError, OSError) as exc:
        _log.debug("SetProcessDpiAwareness unavailable: %s", exc)

    _log.warning("Could not set DPI awareness; coordinates may be virtualized.")
    return "unavailable"


@dataclass(frozen=True, slots=True)
class VirtualDesktop:
    left: int
    top: int
    width: int
    height: int

    @classmethod
    def current(cls) -> VirtualDesktop:
        gsm = ctypes.windll.user32.GetSystemMetrics
        return cls(
            left=gsm(SM_XVIRTUALSCREEN),
            top=gsm(SM_YVIRTUALSCREEN),
            width=gsm(SM_CXVIRTUALSCREEN),
            height=gsm(SM_CYVIRTUALSCREEN),
        )


def center_of(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = rect
    return ((left + right) // 2, (top + bottom) // 2)


def to_absolute_normalized(
    x: int, y: int, desktop: VirtualDesktop | None = None
) -> tuple[int, int]:
    """Converte px fisicos do desktop virtual para o espaco 0..65535 do SendInput.

    Necessario com MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK. Spec §4.1.
    """
    vd = desktop if desktop is not None else VirtualDesktop.current()
    nx = round((x - vd.left) * 65535 / max(vd.width - 1, 1))
    ny = round((y - vd.top) * 65535 / max(vd.height - 1, 1))
    return (max(0, min(65535, nx)), max(0, min(65535, ny)))


def get_dpi_for_window(hwnd: int) -> int:
    """DPI efetivo da janela. 96 = 100%. Cai para 96 em builds sem a API."""
    try:
        dpi = ctypes.windll.user32.GetDpiForWindow(ctypes.c_void_p(hwnd))
        return int(dpi) if dpi else 96
    except (AttributeError, OSError):
        return 96
