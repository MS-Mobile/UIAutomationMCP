"""UiaWorker: thread STA unica onde toda chamada COM acontece. Spec §2.2.

Por que uma thread so: IUIAutomationElement tem afinidade de apartamento. Usar um
ponteiro obtido na thread A a partir da thread B produz RPC_E_WRONG_THREAD ou, pior,
corrupcao silenciosa. O RefStore guarda os ponteiros, mas so esta thread os toca.

Por que o timeout funciona: o cliente UIA e criado com ConnectionTimeout e
TransactionTimeout de 10 s (ver uia/core.py). Uma chamada a um app travado retorna
com erro em ~10 s, antes do nosso teto de 15 s — entao a thread se libera sozinha e
a chamada seguinte funciona (CA-23). Sem aqueles timeouts, este asyncio.wait_for
apenas devolveria o controle ao agente enquanto a thread seguiria presa para sempre.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from .errors import Code, ToolError, from_com_error

_log = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_COM_TIMEOUT_MS = 15000


class UiaWorker:
    def __init__(self, *, com_timeout_ms: int = DEFAULT_COM_TIMEOUT_MS) -> None:
        self._timeout_s = com_timeout_ms / 1000.0
        self._executor: ThreadPoolExecutor | None = None
        self._thread_id: int | None = None
        self.sta_ready = False

    # -------------------------------------------------------------------- ciclo

    def _initializer(self) -> None:
        self._thread_id = threading.get_ident()
        try:
            import comtypes

            comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
            self.sta_ready = True
        except Exception as exc:  # pragma: no cover - so falha em ambiente quebrado
            _log.error("CoInitializeEx failed on the UIA thread: %s", exc)
            self.sta_ready = False

    def start(self) -> None:
        if self._executor is not None:
            return
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="uia", initializer=self._initializer
        )

    def shutdown(self) -> None:
        if self._executor is None:
            return
        executor, self._executor = self._executor, None
        executor.shutdown(wait=False, cancel_futures=True)

    # ----------------------------------------------------------------- execucao

    async def run(self, fn: Callable[..., T], *args: Any) -> T:
        executor = self._executor
        if executor is None:
            raise ToolError(
                Code.SESSION_UNAVAILABLE,
                "The UI Automation worker thread is not running.",
                hint="This is a server bug — ask the user to restart the MCP server.",
            )

        loop = asyncio.get_running_loop()
        futuro = loop.run_in_executor(executor, lambda: self._call(fn, *args))
        try:
            return await asyncio.wait_for(futuro, timeout=self._timeout_s)
        except asyncio.TimeoutError as exc:
            raise ToolError(
                Code.TIMEOUT,
                f"The UI Automation call did not return within {self._timeout_s:.0f} s.",
                hint=(
                    "The target application is likely hung or not pumping messages. "
                    "Ask the user to check it, then retry."
                ),
                timeout_s=self._timeout_s,
            ) from exc

    @staticmethod
    def _call(fn: Callable[..., T], *args: Any) -> T:
        try:
            return fn(*args)
        except ToolError:
            raise
        except Exception as exc:
            if hasattr(exc, "hresult"):
                raise from_com_error(exc) from exc
            raise ToolError(
                Code.UIA_COM_ERROR,
                f"Unexpected failure inside the UI Automation worker: {exc!r}",
            ) from exc
