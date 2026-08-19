"""Cliente UI Automation cru sobre comtypes. Spec §2.2 e §3.3.

Decisao de projeto (spec §3.3): NAO usamos a lib `uiautomation`. Ela instancia o
coclass CUIAutomation legado, que nao expoe ConnectionTimeout/TransactionTimeout —
sem os quais um app travado pendura a thread do worker e o CA-23 e impossivel.
Aqui criamos CUIAutomation8/IUIAutomation6 diretamente.

As tabelas de nomes sao derivadas por reflexao do modulo gerado pelo typelib, e nao
escritas a mao: se a Microsoft acrescentar um ControlType, ele aparece sozinho.
"""

from __future__ import annotations

import re
import threading
from typing import Any

import comtypes
import comtypes.client

CONNECTION_TIMEOUT_MS = 10000
TRANSACTION_TIMEOUT_MS = 10000

_local = threading.local()
_uia_module: Any = None
_module_lock = threading.Lock()

_RE_CONTROL_TYPE = re.compile(r"^UIA_(\w+)ControlTypeId$")
_RE_PATTERN_AVAIL = re.compile(r"^UIA_Is(\w+)PatternAvailablePropertyId$")


def uia_module() -> Any:
    """Gera (uma vez) e devolve o modulo comtypes do typelib do UIAutomationCore."""
    global _uia_module
    if _uia_module is None:
        with _module_lock:
            if _uia_module is None:
                _uia_module = comtypes.client.GetModule("UIAutomationCore.dll")
    return _uia_module


def control_type_names() -> dict[int, str]:
    """{50000: 'Button', ...} derivado do typelib."""
    cache = getattr(_local, "control_type_names", None)
    if cache is None:
        UIA = uia_module()
        cache = {
            getattr(UIA, nome): m.group(1)
            for nome in dir(UIA)
            if (m := _RE_CONTROL_TYPE.match(nome))
        }
        _local.control_type_names = cache
    return cache


def control_type_name(control_type_id: int) -> str:
    return control_type_names().get(control_type_id, "Unknown")


def pattern_availability_props() -> dict[str, int]:
    """{'Invoke': UIA_IsInvokePatternAvailablePropertyId, ...} derivado do typelib."""
    cache = getattr(_local, "pattern_props", None)
    if cache is None:
        UIA = uia_module()
        cache = {
            m.group(1): getattr(UIA, nome)
            for nome in dir(UIA)
            if (m := _RE_PATTERN_AVAIL.match(nome))
        }
        _local.pattern_props = cache
    return cache


class Automation:
    """Fachada do IUIAutomation6. Instancia por thread — nunca compartilhar."""

    def __init__(self) -> None:
        self.UIA = uia_module()
        self.iuia = comtypes.client.CreateObject(
            self.UIA.CUIAutomation8, interface=self.UIA.IUIAutomation6
        )
        # Sem isto, uma chamada a um app travado nunca retorna. Spec §2.2 / CA-23.
        self.iuia.ConnectionTimeout = CONNECTION_TIMEOUT_MS
        self.iuia.TransactionTimeout = TRANSACTION_TIMEOUT_MS

        self.root = self.iuia.GetRootElement()
        self.true_condition = self.iuia.CreateTrueCondition()
        self.control_walker = self.iuia.ControlViewWalker
        self.raw_walker = self.iuia.RawViewWalker

    # ------------------------------------------------------------ cache request

    def tree_props(self) -> tuple[int, ...]:
        """Propriedades exigidas pela spec §5.2 para a captura de arvore."""
        U = self.UIA
        base = (
            U.UIA_NamePropertyId,
            U.UIA_AutomationIdPropertyId,
            U.UIA_ClassNamePropertyId,
            U.UIA_ControlTypePropertyId,
            U.UIA_IsEnabledPropertyId,
            U.UIA_IsOffscreenPropertyId,
            U.UIA_IsKeyboardFocusablePropertyId,
            U.UIA_HasKeyboardFocusPropertyId,
            U.UIA_BoundingRectanglePropertyId,
            U.UIA_RuntimeIdPropertyId,
            U.UIA_ProcessIdPropertyId,
            U.UIA_IsPasswordPropertyId,
            U.UIA_ToggleToggleStatePropertyId,
            U.UIA_ExpandCollapseExpandCollapseStatePropertyId,
            U.UIA_SelectionItemIsSelectedPropertyId,
            U.UIA_ValueValuePropertyId,
            U.UIA_ValueIsReadOnlyPropertyId,
            U.UIA_RangeValueValuePropertyId,
        )
        return base + tuple(pattern_availability_props().values())

    def build_cache_request(
        self,
        properties: tuple[int, ...],
        *,
        scope: int | None = None,
        element_mode: int | None = None,
    ) -> Any:
        U = self.UIA
        cr = self.iuia.CreateCacheRequest()
        for prop in properties:
            cr.AddProperty(prop)
        cr.TreeScope = U.TreeScope_Subtree if scope is None else scope
        # ControlView, nao RawView: RawView infla a arvore com nos irrelevantes (§5.2).
        cr.TreeFilter = self.iuia.ControlViewCondition
        cr.AutomationElementMode = (
            U.AutomationElementMode_Full if element_mode is None else element_mode
        )
        return cr

    # ----------------------------------------------------------------- condicoes

    def property_condition(self, property_id: int, value: Any) -> Any:
        return self.iuia.CreatePropertyCondition(property_id, value)

    def and_conditions(self, *conditions: Any) -> Any:
        if not conditions:
            return self.true_condition
        resultado = conditions[0]
        for cond in conditions[1:]:
            resultado = self.iuia.CreateAndCondition(resultado, cond)
        return resultado

    # ----------------------------------------------------------------- elementos

    def element_from_handle(self, hwnd: int) -> Any:
        return self.iuia.ElementFromHandle(hwnd)

    def runtime_id_of(self, element: Any) -> tuple[int, ...]:
        try:
            return tuple(element.GetRuntimeId())
        except Exception:
            return ()


def automation() -> Automation:
    """Instancia por thread. Chamar so de dentro da thread do UiaWorker."""
    inst = getattr(_local, "automation", None)
    if inst is None:
        inst = Automation()
        _local.automation = inst
    return inst


def reset_for_tests() -> None:
    """Limpa o estado thread-local. So para testes."""
    for attr in ("automation", "control_type_names", "pattern_props"):
        if hasattr(_local, attr):
            delattr(_local, attr)
