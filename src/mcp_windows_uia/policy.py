"""Decisoes de politica: allowlist, read-only e rate limit. Spec §10.1 e §10.2.

O match de aplicacao e sempre pelo nome do executavel, nunca so pelo titulo: o
titulo da janela e forjavel pelo conteudo do documento aberto, entao confiar nele
sozinho permitiria que uma pagina web escolhesse a propria classificacao.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from .config import Config
from .errors import Code, ToolError


class Policy:
    """Guarda de acesso. Ordem de avaliacao: denylist -> allowlist -> decisao."""

    def __init__(self, config: Config, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._cfg = config
        self._clock = clock
        self._actions: deque[float] = deque()

    @property
    def read_only(self) -> bool:
        return self._cfg.server.read_only

    # ----------------------------------------------------------------- allowlist

    def denied_reason(self, process_name: str, title: str) -> str | None:
        """Motivo da negacao, ou None se a janela e permitida."""
        proc = (process_name or "").strip().lower()
        titulo = title or ""

        if proc in self._cfg.denylist.processes:
            return f"process '{process_name}' is in the denylist"
        for pattern in self._cfg.denylist.title_patterns:
            if pattern.search(titulo):
                return f"window title matches a denylist pattern ({pattern.pattern!r})"

        if self._cfg.allowlist.mode == "deny_all":
            return "server is in deny_all mode (kill switch)"
        if proc not in self._cfg.allowlist.processes:
            return f"process '{process_name}' is not in the allowlist"

        restricao = self._cfg.allowlist.title_patterns.get(proc)
        if restricao is not None and not restricao.search(titulo):
            return (
                f"window title does not match the allowlist restriction for "
                f"'{process_name}' ({restricao.pattern!r})"
            )
        return None

    def window_allowed(self, process_name: str, title: str) -> bool:
        return self.denied_reason(process_name, title) is None

    def check_window(self, process_name: str, title: str, **details: object) -> None:
        motivo = self.denied_reason(process_name, title)
        if motivo is None:
            return
        raise ToolError(
            Code.APP_NOT_ALLOWED,
            f"Access to this window was denied: {motivo}.",
            process=process_name,
            title=title,
            **details,
        )

    # ----------------------------------------------------------------- read-only

    def check_mutating(self, tool_name: str) -> None:
        if self._cfg.server.read_only:
            raise ToolError(
                Code.READ_ONLY_MODE,
                f"{tool_name} mutates UI state and the server is running in read-only mode.",
                tool=tool_name,
            )

    # ---------------------------------------------------------------- rate limit

    def check_rate(self) -> None:
        teto = self._cfg.server.max_actions_per_minute
        if teto <= 0:
            return
        agora = self._clock()
        while self._actions and agora - self._actions[0] >= 60.0:
            self._actions.popleft()
        if len(self._actions) >= teto:
            espera = 60.0 - (agora - self._actions[0])
            raise ToolError(
                Code.ACTION_RATE_LIMITED,
                f"Rate limit reached: {teto} mutating actions per minute.",
                hint=f"Too many mutating actions. Wait {espera:.0f} s and retry; batch your work.",
                retry_after_s=round(max(espera, 0.1), 1),
                limit=teto,
            )
        self._actions.append(agora)
