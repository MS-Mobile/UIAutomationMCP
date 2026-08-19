"""RefStore: refs opacas e estaveis para janelas e elementos. Spec §7.1 e §7.2.

O rebind (re-resolucao por AutomationId / Name / index_path quando o RuntimeId morre)
NAO vive aqui: precisa de busca na arvore UIA e chega no Plano 2. Este modulo e puro —
guarda, expira, invalida, e nunca desreferencia o ponteiro COM que carrega.

Dedup por (hwnd, runtime_id) e obrigatorio: sem ele, cada uia_get_tree criaria refs
novas para os mesmos elementos e o LRU giraria a toa, invalidando refs que o agente
acabou de receber.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .errors import Code, ToolError

DEFAULT_TTL_S = 300
DEFAULT_MAX_ENTRIES = 5000


@dataclass(frozen=True, slots=True)
class ElementIdentity:
    """Material de re-resolucao quando o RuntimeId morre (spec §7.2, passo 5)."""

    automation_id: str = ""
    control_type: str = ""
    name: str = ""
    class_name: str = ""
    index_path: tuple[int, ...] = ()


@dataclass(slots=True)
class RefEntry:
    ref: str
    element: Any                      # IUIAutomationElement: so a UiaWorker toca
    runtime_id: tuple[int, ...]
    hwnd: int
    window_ref: str
    identity: ElementIdentity
    tree_version: int
    created_at: float
    last_ok_at: float


@dataclass(slots=True)
class _WindowEntry:
    window_ref: str
    hwnd: int
    tree_version: int = 0
    element_refs: set[str] = field(default_factory=set)


class RefStore:
    """Cache de refs por sessao do servidor. Contadores monotonicos, evicao LRU."""

    def __init__(
        self,
        *,
        ttl_s: int = DEFAULT_TTL_S,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_s
        self._max = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, RefEntry] = OrderedDict()
        self._windows: dict[str, _WindowEntry] = {}
        self._by_hwnd: dict[int, str] = {}
        self._by_runtime: dict[tuple[int, tuple[int, ...]], str] = {}
        self._window_seq = 0
        self._element_seq = 0

    def __len__(self) -> int:
        return len(self._entries)

    # ------------------------------------------------------------------ janelas

    def window_ref(self, *, hwnd: int) -> str:
        """Ref estavel para um hwnd, criada na primeira vez que a janela e vista."""
        existente = self._by_hwnd.get(hwnd)
        if existente is not None:
            return existente
        self._window_seq += 1
        ref = f"w{self._window_seq}"
        self._by_hwnd[hwnd] = ref
        self._windows[ref] = _WindowEntry(window_ref=ref, hwnd=hwnd)
        return ref

    def known_window(self, window_ref: str) -> bool:
        return window_ref in self._windows

    def hwnd_for(self, window_ref: str) -> int:
        entrada = self._windows.get(window_ref)
        if entrada is None:
            raise ToolError(
                Code.WINDOW_NOT_FOUND,
                f"Unknown window ref {window_ref!r}.",
                window_ref=window_ref,
            )
        return entrada.hwnd

    def tree_version(self, window_ref: str) -> int:
        entrada = self._windows.get(window_ref)
        return 0 if entrada is None else entrada.tree_version

    def bump_tree_version(self, window_ref: str) -> int:
        entrada = self._windows.get(window_ref)
        if entrada is None:
            raise ToolError(
                Code.WINDOW_NOT_FOUND,
                f"Unknown window ref {window_ref!r}.",
                window_ref=window_ref,
            )
        entrada.tree_version += 1
        return entrada.tree_version

    def invalidate_window(self, window_ref: str) -> int:
        """Invalida em lote todas as refs da janela. Retorna quantas cairam."""
        entrada = self._windows.get(window_ref)
        if entrada is None:
            return 0
        refs = list(entrada.element_refs)
        for ref in refs:
            self._drop(ref)
        entrada.element_refs.clear()
        return len(refs)

    # ---------------------------------------------------------------- elementos

    def put(
        self,
        element: Any,
        *,
        runtime_id: tuple[int, ...],
        hwnd: int,
        window_ref: str,
        identity: ElementIdentity,
        tree_version: int,
    ) -> str:
        """Registra (ou atualiza) um elemento e devolve sua ref."""
        agora = self._clock()
        chave = (hwnd, runtime_id)

        existente = self._by_runtime.get(chave)
        if existente is not None and existente in self._entries:
            entrada = self._entries[existente]
            entrada.element = element
            entrada.identity = identity
            entrada.tree_version = tree_version
            entrada.last_ok_at = agora
            self._entries.move_to_end(existente)
            return existente

        self._element_seq += 1
        ref = f"{window_ref}-e{self._element_seq}"
        self._entries[ref] = RefEntry(
            ref=ref,
            element=element,
            runtime_id=runtime_id,
            hwnd=hwnd,
            window_ref=window_ref,
            identity=identity,
            tree_version=tree_version,
            created_at=agora,
            last_ok_at=agora,
        )
        self._by_runtime[chave] = ref
        janela = self._windows.get(window_ref)
        if janela is not None:
            janela.element_refs.add(ref)
        self._evict()
        return ref

    def get(self, ref: str) -> RefEntry:
        """Recupera uma entrada viva, renovando seu TTL. Nao valida o elemento COM."""
        entrada = self._entries.get(ref)
        if entrada is None:
            raise ToolError(
                Code.REF_NOT_FOUND,
                f"Ref {ref!r} is not known to this server (never issued, or evicted).",
                ref=ref,
            )
        agora = self._clock()
        if agora - entrada.last_ok_at > self._ttl:
            self._drop(ref)
            raise ToolError(
                Code.STALE_REF,
                f"Ref {ref} expired after {self._ttl} s without use.",
                ref=ref,
                window_ref=entrada.window_ref,
                reason="expired",
            )
        entrada.last_ok_at = agora
        self._entries.move_to_end(ref)
        return entrada

    def touch(self, entry: RefEntry) -> None:
        entry.last_ok_at = self._clock()
        self._entries.move_to_end(entry.ref)

    # ------------------------------------------------------------------ interno

    def _drop(self, ref: str) -> None:
        entrada = self._entries.pop(ref, None)
        if entrada is None:
            return
        self._by_runtime.pop((entrada.hwnd, entrada.runtime_id), None)
        janela = self._windows.get(entrada.window_ref)
        if janela is not None:
            janela.element_refs.discard(ref)

    def _evict(self) -> None:
        while len(self._entries) > self._max:
            ref = next(iter(self._entries))
            self._drop(ref)
