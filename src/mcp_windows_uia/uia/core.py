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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import comtypes
import comtypes.client

from ..budget import HARD_MAX_CHILDREN, HARD_MAX_DEPTH
from .search import TETO_DE_BUSCA

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


@dataclass(frozen=True, slots=True)
class Achados:
    """Resultado bruto de uma busca da §8.4.

    `exhaustive` e o compromisso honesto com o agente: False significa "sobrou
    janela que eu nao olhei", e ai um zero-match nao prova ausencia.
    """

    elementos: list[Any]
    visitados: int
    exhaustive: bool


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
        """CacheRequest para FindAllBuildCache.

        `scope` aqui NAO e o escopo da busca — esse vai no primeiro argumento de
        FindAllBuildCache. Este e por-elemento: "para CADA elemento encontrado,
        pre-carregue tambem esse tanto da subarvore DELE". Por isso o default e
        TreeScope_Element: com Subtree, uma busca que casa N elementos pede N
        subarvores completas, a transacao estoura o TransactionTimeout de 10 s e a
        chamada aflora como E_FAIL (0x80004005). Medido no WhatsApp Desktop:
        cache=Element OK em 18 s / 24409 nos; cache=Subtree falha apos ~9,5 s.
        """
        U = self.UIA
        cr = self.iuia.CreateCacheRequest()
        for prop in properties:
            cr.AddProperty(prop)
        cr.TreeScope = U.TreeScope_Element if scope is None else scope
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

    # -------------------------------------------------------------- busca (8.4)

    def condicao_de_criterios(self, criterios: Any) -> tuple[Any, bool]:
        """Traduz o que da para condicao nativa. Spec §8.4.

        So criterios EXATOS viram condicao — o provider filtra do lado dele, o que e
        muito mais barato que trazer a arvore. contains/starts_with/regex e
        text_contains ficam para o filtro no cliente.

        Devolve `(condicao, restringe)`. O segundo item existe porque a diferenca
        entre "condicao que poda" e TrueCondition e a diferenca entre um RPC e
        enumerar a janela inteira: quem chama precisa escolher a estrategia a partir
        dela. E ele nao pode ser deduzido so olhando os criterios — um
        `control_type` que nao existe no typelib nao vira condicao nenhuma.
        """
        U = self.UIA
        condicoes: list[Any] = []

        if criterios.automation_id:
            condicoes.append(
                self.property_condition(U.UIA_AutomationIdPropertyId, criterios.automation_id)
            )
        if criterios.class_name:
            condicoes.append(
                self.property_condition(U.UIA_ClassNamePropertyId, criterios.class_name)
            )
        if criterios.control_type:
            tipo_id = next(
                (
                    cid
                    for cid, nome in control_type_names().items()
                    if nome == criterios.control_type
                ),
                None,
            )
            if tipo_id is not None:
                condicoes.append(self.property_condition(U.UIA_ControlTypePropertyId, tipo_id))
        if criterios.name and criterios.match == "exact":
            condicoes.append(self.property_condition(U.UIA_NamePropertyId, criterios.name))

        return self.and_conditions(*condicoes), bool(condicoes)

    def buscar_plano(self, hwnd: int, condicao: Any, *, teto: int = TETO_DE_BUSCA) -> Achados:
        """FindAll cacheado dentro de uma janela. Busca PLANA — nao atravessa nada.

        Diferente do percurso da §5.2, aqui a condicao nativa e legitima: nao ha
        travessia a podar. Um unico RPC traz todos os candidatos ja com propriedades.

        Exige uma condicao que RESTRINJA. Com TrueCondition o FindAll enumera todos
        os descendentes antes de devolver e o `teto` nao economiza nada — o custo ja
        foi pago. Esse caso e de `varrer_descendentes`.
        """
        cr = self.build_cache_request(self.tree_props())
        raiz = self.element_from_handle(hwnd)
        # Sem try/except: um FindAll que falha num app travado precisa aflorar como
        # TIMEOUT/UIA_COM_ERROR (o UiaWorker converte), nao virar "nao encontrei".
        achados = raiz.FindAllBuildCache(self.UIA.TreeScope_Descendants, condicao, cr)
        total = achados.Length
        return Achados(
            elementos=[achados.GetElement(i) for i in range(min(total, teto))],
            visitados=total,
            exhaustive=total <= teto,
        )

    def varrer_descendentes(
        self,
        hwnd: int,
        *,
        aceita: Callable[[Any], bool],
        max_resultados: int,
        teto: int = TETO_DE_BUSCA,
        max_depth: int = HARD_MAX_DEPTH,
    ) -> Achados:
        """Varredura por nivel com teto de visita. Ramo sem criterio nativo da §8.4.

        Reusa o `percorrer` da §5.2 de proposito: o freio precisa ser aplicado
        DURANTE a descida. E tambem para assim que junta `max_resultados`, entao o
        caso comum (o alvo esta nos primeiros niveis) custa uma fracao da janela.
        """
        from .tree import CaptureBudget, filhos_cacheados, percorrer

        cr = self.build_cache_request(self.tree_props())
        raiz = self.element_from_handle_build_cache(hwnd, cr)
        r = percorrer(
            raiz,
            filhos_cacheados(self, cr),
            lambda elem, _nivel: aceita(elem),
            CaptureBudget(
                max_nodes=max_resultados,
                max_depth=max_depth,
                max_children_per_node=HARD_MAX_CHILDREN,
                max_visited=teto,
                depth_is_default=False,
            ),
        )
        return Achados(
            elementos=[elem for elem, _nivel in r.emitidos],
            visitados=r.visitados,
            # Irmaos elididos tambem sao janela nao olhada — nao da para dizer que
            # a busca foi exaustiva tendo cortado filhos.
            exhaustive=not r.truncado and not r.elididos,
        )

    # ----------------------------------------------------------------- elementos

    def element_from_handle(self, hwnd: int) -> Any:
        return self.iuia.ElementFromHandle(hwnd)

    def element_from_handle_build_cache(self, hwnd: int, cache_request: Any) -> Any:
        """Raiz JA materializada pelo cache_request.

        `ElementFromHandle` devolve um elemento com cache VAZIO. Ler dele custa um
        RPC por propriedade e, pior, `GetCachedPropertyValue` levanta E_INVALIDARG
        (0x80070057) — o que faz a raiz da captura entrar na arvore serializada como
        lixo silencioso, ja que `nodes._cached` engole a excecao e devolve o default.
        """
        return self.iuia.ElementFromHandleBuildCache(hwnd, cache_request)

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
