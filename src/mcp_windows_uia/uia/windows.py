"""Enumeracao de janelas top-level. Spec §8.1, §4.1 e §4.2.

Usa EnumWindows em vez da arvore UIA: WS_VISIBLE, IsIconic e o placement da janela
saem do Win32 de graca, e a spec §8.1 exige esses campos. A arvore UIA entra depois,
nas tools de leitura (Plano 2).
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass

import psutil

from ..budget import truncate_name
from ..errors import Code, ToolError
from .dpi import get_dpi_for_window

_log = logging.getLogger(__name__)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
SW_SHOWMAXIMIZED = 3
SW_SHOWMINIMIZED = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ACCESS_DENIED = 5
MONITOR_DEFAULTTONEAREST = 2
DESKTOP_READOBJECTS = 0x0001

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.UINT),
        ("flags", wintypes.UINT),
        ("showCmd", wintypes.UINT),
        ("ptMinPosition", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("rcNormalPosition", wintypes.RECT),
    ]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


@dataclass(frozen=True, slots=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    process: str
    rect: tuple[int, int, int, int]
    visible: bool
    minimized: bool
    maximized: bool
    focused: bool
    dpi: int
    monitor: int
    monitor_device: str
    elevated: bool


# --------------------------------------------------------------------- ambiente


def is_session_interactive() -> bool:
    """Falso quando a estacao esta bloqueada ou a sessao RDP esta desconectada."""
    hdesk = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not hdesk:
        return False
    user32.CloseDesktop(hdesk)
    return True


def require_interactive_session() -> None:
    if not is_session_interactive():
        raise ToolError(
            Code.SESSION_UNAVAILABLE,
            "The desktop session is locked, disconnected or not interactive.",
        )


def server_is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def process_elevated(pid: int) -> bool:
    """Heuristica da spec §4.2: OpenProcess negado => integridade superior."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return False
    return kernel32.GetLastError() == ERROR_ACCESS_DENIED


# ---------------------------------------------------------------------- janelas


def _title(hwnd: int) -> str:
    tamanho = user32.GetWindowTextLengthW(hwnd)
    if tamanho <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(tamanho + 1)
    user32.GetWindowTextW(hwnd, buf, tamanho + 1)
    return buf.value


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return (0, 0, 0, 0)
    return (r.left, r.top, r.right, r.bottom)


def _placement(hwnd: int) -> tuple[bool, bool]:
    wp = _WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(_WINDOWPLACEMENT)
    if not user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
        return (False, False)
    return (wp.showCmd == SW_SHOWMINIMIZED, wp.showCmd == SW_SHOWMAXIMIZED)


def _monitor(hwnd: int, indices: dict[str, int]) -> tuple[int, str]:
    hmon = user32.MonitorFromWindow(ctypes.c_void_p(hwnd), MONITOR_DEFAULTTONEAREST)
    info = _MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
    if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
        return (0, "")
    device = info.szDevice
    return (indices.setdefault(device, len(indices)), device)


def _process_name(pid: int) -> str:
    try:
        return psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return ""


def _is_tool_window(hwnd: int) -> bool:
    return bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW)


def enumerate_windows(*, include_hidden: bool = False) -> list[WindowInfo]:
    """Lista janelas top-level. Sem filtro de allowlist — isso e decisao da policy."""
    require_interactive_session()

    handles: list[int] = []

    def _coletar(hwnd: int, _lparam: int) -> bool:
        handles.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(_coletar), 0)

    foreground = user32.GetForegroundWindow()
    indices_monitor: dict[str, int] = {}
    resultado: list[WindowInfo] = []

    for hwnd in handles:
        visivel = bool(user32.IsWindowVisible(hwnd))
        titulo = _title(hwnd)
        if not include_hidden:
            if not visivel or not titulo:
                continue
            if user32.GetWindow(hwnd, GW_OWNER):
                continue  # pertencente a outra janela: nao e top-level de verdade
            if _is_tool_window(hwnd):
                continue

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        minimizada, maximizada = _placement(hwnd)
        indice, device = _monitor(hwnd, indices_monitor)

        resultado.append(
            WindowInfo(
                hwnd=hwnd,
                title=truncate_name(titulo),
                pid=int(pid.value),
                process=_process_name(int(pid.value)),
                rect=_rect(hwnd),
                visible=visivel,
                minimized=minimizada,
                maximized=maximizada,
                focused=(hwnd == foreground),
                dpi=get_dpi_for_window(hwnd),
                monitor=indice,
                monitor_device=device,
                elevated=process_elevated(int(pid.value)),
            )
        )
    return resultado


def window_is_alive(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))
