from __future__ import annotations

import pytest

from mcp_windows_uia.uia import core

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def sta():
    """Estes testes tocam COM: precisam de STA nesta thread."""
    import comtypes

    comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
    yield
    core.reset_for_tests()


def test_control_type_names_derivados_do_typelib(sta: None) -> None:
    nomes = core.control_type_names()
    assert nomes[50000] == "Button"
    assert nomes[50004] == "Edit"
    assert nomes[50011] == "MenuItem"
    assert nomes[50032] == "Window"
    assert len(nomes) >= 40


def test_control_type_name_desconhecido_nao_explode(sta: None) -> None:
    assert core.control_type_name(999999) == "Unknown"


def test_pattern_availability_props_cobre_os_patterns_da_spec(sta: None) -> None:
    props = core.pattern_availability_props()
    esperados = {
        "Invoke", "Toggle", "SelectionItem", "ExpandCollapse", "Value",
        "RangeValue", "Scroll", "ScrollItem", "Text", "Grid", "Table", "Window",
    }
    assert esperados <= set(props)


def test_cliente_e_cuiautomation8_com_timeouts(sta: None) -> None:
    """A razao de existir deste modulo: o CUIAutomation legado nao expoe isto."""
    a = core.automation()
    assert a.iuia.ConnectionTimeout == 10000
    assert a.iuia.TransactionTimeout == 10000


def test_automation_e_singleton_por_thread(sta: None) -> None:
    assert core.automation() is core.automation()


def test_root_element_e_um_pane(sta: None) -> None:
    raiz = core.automation().root
    assert core.control_type_name(raiz.CurrentControlType) == "Pane"


def test_cache_request_aceita_as_propriedades_da_spec(sta: None) -> None:
    a = core.automation()
    assert a.build_cache_request(a.tree_props()) is not None


def test_cache_request_usa_treescope_element_por_padrao(sta: None) -> None:
    """O TreeScope do CacheRequest e por-elemento, nao o escopo da busca.

    Ele significa "para CADA elemento encontrado, pre-carregue tambem esse tanto da
    subarvore DELE". Com TreeScope_Subtree, uma busca que casa N elementos pede N
    subarvores completas: a transacao estoura o TransactionTimeout de 10 s e a
    chamada aflora como E_FAIL (0x80004005). Medido no WhatsApp Desktop: cache=Element
    OK; cache=Descendants e cache=Subtree falham apos ~9,5 s.

    O escopo da BUSCA (Subtree) vai no primeiro argumento de FindAllBuildCache.
    """
    a = core.automation()
    cr = a.build_cache_request(a.tree_props())
    assert cr.TreeScope == a.UIA.TreeScope_Element


def test_findallbuildcache_com_cache_request_padrao_nao_falha(sta: None) -> None:
    """Regressao do E_FAIL 0x80004005 causado por cache TreeScope_Subtree.

    Escopo de busca Children de proposito: Subtree a partir da janela do desktop
    enumeraria a area de trabalho inteira, caro demais para teste unitario.
    """
    import ctypes

    a = core.automation()
    hwnd = ctypes.windll.user32.GetDesktopWindow()
    alvo = a.element_from_handle(hwnd)
    cr = a.build_cache_request(a.tree_props())
    achados = alvo.FindAllBuildCache(a.UIA.TreeScope_Children, a.true_condition, cr)
    assert achados.Length > 0


def test_condicoes_nativas_sao_construiveis(sta: None) -> None:
    a = core.automation()
    c1 = a.property_condition(a.UIA.UIA_ControlTypePropertyId, 50000)
    c2 = a.property_condition(a.UIA.UIA_NamePropertyId, "Salvar")
    assert a.and_conditions(c1, c2) is not None
    assert a.and_conditions(c1) is c1
    assert a.and_conditions() is a.true_condition


def test_element_from_handle_devolve_a_janela(sta: None) -> None:
    import ctypes

    hwnd = ctypes.windll.user32.GetDesktopWindow()
    assert core.automation().element_from_handle(hwnd) is not None


def test_captura_cacheada_le_propriedades_sem_rpc_extra(sta: None) -> None:
    """Regra dura da spec §3.3: FindAllBuildCache + propriedades Cached*."""
    a = core.automation()
    cr = a.build_cache_request(a.tree_props())
    filhos = a.root.FindAllBuildCache(a.UIA.TreeScope_Children, a.true_condition, cr)
    assert filhos.Length > 0
    primeiro = filhos.GetElement(0)
    _ = primeiro.CachedName
    _ = primeiro.CachedControlType
    _ = primeiro.CachedBoundingRectangle


def test_runtime_id_of_devolve_tupla(sta: None) -> None:
    a = core.automation()
    rid = a.runtime_id_of(a.root)
    assert isinstance(rid, tuple) and len(rid) >= 1
