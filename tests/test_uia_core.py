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


# --------------------------------------------------- rebind por caminho de indices


class _FilhosFalsos:
    def __init__(self, elems) -> None:
        self._elems = list(elems)

    @property
    def Length(self):  # noqa: N802 - assinatura do COM
        return len(self._elems)

    def GetElement(self, i):  # noqa: N802 - assinatura do COM
        return self._elems[i]


class _ElemFalso:
    """Duble de IUIAutomationElement com filhos fixos."""

    def __init__(self, tipo: int = 50000, filhos=()) -> None:
        self.CurrentControlType = tipo
        self._filhos = list(filhos)

    def FindAll(self, _escopo, _condicao):  # noqa: N802 - assinatura do COM
        return _FilhosFalsos(self._filhos)


class _ElemQueExplode:
    CurrentControlType = 50000

    def FindAll(self, _escopo, _condicao):  # noqa: N802 - assinatura do COM
        raise RuntimeError("provider recusou")


def _identity(**kw):
    from mcp_windows_uia.refs import ElementIdentity

    return ElementIdentity(**kw)


def test_index_path_desce_ate_o_elemento(sta: None) -> None:
    alvo = _ElemFalso(50000)  # Button
    raiz = _ElemFalso(50032, [_ElemFalso(50033), _ElemFalso(50033, [_ElemFalso(), alvo])])

    achados = core.automation()._por_index_path(
        raiz, _identity(control_type="Button", index_path=(1, 1))
    )

    assert achados == [alvo]


def test_index_path_com_tipo_diferente_nao_devolve_nada(sta: None) -> None:
    """A pista mais fraca das tres: um controle inserido antes do alvo desloca tudo.

    Sem conferir o ControlType no fim, o rebind devolveria com confianca o elemento
    errado — que e pior que nao achar, porque o agente age nele.
    """
    raiz = _ElemFalso(50032, [_ElemFalso(50004)])  # Edit onde se esperava Button

    achados = core.automation()._por_index_path(
        raiz, _identity(control_type="Button", index_path=(0,))
    )

    assert achados == []


def test_index_path_fora_do_alcance_nao_explode(sta: None) -> None:
    raiz = _ElemFalso(50032, [_ElemFalso()])

    assert core.automation()._por_index_path(raiz, _identity(index_path=(5,))) == []


def test_index_path_com_findall_que_falha_devolve_vazio(sta: None) -> None:
    assert core.automation()._por_index_path(_ElemQueExplode(), _identity(index_path=(0,))) == []


# -------------------------------------- condicao nativa e caracteres fora do BMP

# Sintetico de proposito: o repositorio e publico. A unica propriedade que importa
# aqui e conter um caractere fora do BMP (par surrogate em UTF-16).
EMOJI = "Item de teste com emoji 👍"


def test_nome_fora_do_bmp_nao_e_casavel_nativamente(sta: None) -> None:
    """Medido no WhatsApp Desktop: PropertyCondition(Name) com emoji casa ZERO.

    Nomes so-BMP do mesmo provider casam normalmente — 78, 158 e 468 resultados,
    inclusive com acento e nbsp. Basta um caractere fora do BMP (par surrogate em
    UTF-16) para a condicao devolver 0 sem erro nenhum.
    """
    assert core.casavel_em_condicao_nativa("Salvar como") is True
    assert core.casavel_em_condicao_nativa("Configurações de exibição 07:22") is True
    assert core.casavel_em_condicao_nativa(EMOJI) is False


def test_name_com_emoji_cai_para_a_varredura_no_cliente(sta: None) -> None:
    """O ramo nativo mentiria: zero resultados com exhaustive=True.

    `restringe=False` e o que empurra `uia_find_elements` para a varredura, que
    compara em Python e acha.
    """
    from mcp_windows_uia.uia.search import Criterios

    _, restringe = core.automation().condicao_de_criterios(
        Criterios(name=EMOJI, match="exact")
    )
    assert restringe is False


def test_name_so_bmp_continua_no_ramo_nativo(sta: None) -> None:
    from mcp_windows_uia.uia.search import Criterios

    _, restringe = core.automation().condicao_de_criterios(
        Criterios(name="Salvar", match="exact")
    )
    assert restringe is True


def test_emoji_no_nome_nao_impede_os_outros_criterios(sta: None) -> None:
    """control_type continua restringindo; o nome e filtrado depois, no cliente."""
    from mcp_windows_uia.uia.search import Criterios

    _, restringe = core.automation().condicao_de_criterios(
        Criterios(name=EMOJI, control_type="Button", match="exact")
    )
    assert restringe is True


def test_buscar_com_estrategia_sem_material_devolve_vazio(sta: None) -> None:
    """Uma condicao AutomationId=="" casa o universo: 5000 elementos no WhatsApp.

    `rebind` ja pula estrategias vazias, mas devolver meio app para quem chamar
    `buscar` direto e um estrago esperando chamador.
    """
    from mcp_windows_uia.rebind import Estrategia
    from mcp_windows_uia.refs import ElementIdentity

    a = core.automation()
    vazia = ElementIdentity(control_type="Button")

    assert a.buscar(0, vazia, Estrategia.AUTOMATION_ID) == []
    assert a.buscar(0, vazia, Estrategia.NAME) == []
