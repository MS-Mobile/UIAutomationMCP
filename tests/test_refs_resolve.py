"""RefStore.resolve: probe, rebind e reindexacao. Spec §7.2.

Sem COM: `automation` ja e injetado por design (o store nunca importa UIA), entao a
maquina inteira de decisao — o ponteiro ainda serve? reapontar? que ref sai no fim? —
e testavel aqui.
"""

from __future__ import annotations

import pytest

from mcp_windows_uia.errors import Code, ToolError
from mcp_windows_uia.rebind import Estrategia
from mcp_windows_uia.refs import ElementIdentity, RefStore


class ElementoFalso:
    """Duble de IUIAutomationElement. `vivo=False` simula ponteiro que morreu."""

    def __init__(self, rid: tuple[int, ...], *, vivo: bool = True) -> None:
        self.rid = rid
        self.vivo = vivo

    def GetRuntimeId(self):  # noqa: N802 - assinatura do COM
        if not self.vivo:
            raise RuntimeError("UIA_E_ELEMENTNOTAVAILABLE")
        return self.rid


class AutomationFalsa:
    def __init__(self, achar: ElementoFalso | None = None) -> None:
        self.achar = achar
        self.buscas: list[Estrategia] = []

    def buscar(self, hwnd, identity, estrategia):
        self.buscas.append(estrategia)
        return [self.achar] if self.achar is not None else []

    def runtime_id_of(self, elem):
        return elem.rid


IDENTIDADE = ElementIdentity(automation_id="btnSalvar", control_type="Button", name="Salvar")


@pytest.fixture()
def store() -> RefStore:
    s = RefStore()
    s.window_ref(hwnd=100)
    return s


def registrar(store: RefStore, elem: ElementoFalso, *, hwnd: int = 100) -> str:
    return store.put(
        elem,
        runtime_id=elem.rid,
        hwnd=hwnd,
        window_ref=store.window_ref(hwnd=hwnd),
        identity=IDENTIDADE,
        tree_version=1,
    )


# ------------------------------------------------------------------------- probe


def test_ponteiro_vivo_com_runtime_id_igual_nao_faz_rebind(store: RefStore) -> None:
    """O caminho quente: rebind custa uma busca na arvore, o probe custa um RPC."""
    elem = ElementoFalso((1, 2, 3))
    ref = registrar(store, elem)
    a = AutomationFalsa()

    achado, rebound = store.resolve(ref, automation=a)

    assert achado is elem
    assert rebound is False
    assert a.buscas == []


def test_runtime_id_que_mudou_dispara_rebind(store: RefStore) -> None:
    """Elemento recriado pelo app: o ponteiro ainda responde, mas nao e mais ele."""
    elem = ElementoFalso((1, 2, 3))
    ref = registrar(store, elem)
    elem.rid = (9, 9, 9)  # o app recriou o controle
    novo = ElementoFalso((9, 9, 9))
    a = AutomationFalsa(achar=novo)

    achado, rebound = store.resolve(ref, automation=a)

    assert achado is novo
    assert rebound is True
    assert a.buscas == [Estrategia.AUTOMATION_ID]


def test_ponteiro_morto_dispara_rebind(store: RefStore) -> None:
    ref = registrar(store, ElementoFalso((1, 2, 3), vivo=False))
    novo = ElementoFalso((4, 5, 6))

    achado, rebound = store.resolve(ref, automation=AutomationFalsa(achar=novo))

    assert achado is novo
    assert rebound is True


def test_rebind_sem_candidato_e_stale_ref(store: RefStore) -> None:
    ref = registrar(store, ElementoFalso((1, 2, 3), vivo=False))

    with pytest.raises(ToolError) as exc:
        store.resolve(ref, automation=AutomationFalsa(achar=None))

    assert exc.value.code is Code.STALE_REF


def test_ref_desconhecida_e_ref_not_found(store: RefStore) -> None:
    with pytest.raises(ToolError) as exc:
        store.resolve("w1-e999", automation=AutomationFalsa())

    assert exc.value.code is Code.REF_NOT_FOUND


# ------------------------------------------------------------------ reindexacao


def test_apos_rebind_a_mesma_ref_e_reusada_no_proximo_put(store: RefStore) -> None:
    """O indice e por (hwnd, runtime_id). Se ele nao acompanhar o rebind, a proxima
    captura da arvore cunha uma ref NOVA para o mesmo elemento — e o agente fica com
    duas refs para uma coisa so, sem saber qual continua valendo.
    """
    ref = registrar(store, ElementoFalso((1, 2, 3), vivo=False))
    novo = ElementoFalso((4, 5, 6))

    store.resolve(ref, automation=AutomationFalsa(achar=novo))
    de_novo = registrar(store, novo)

    assert de_novo == ref


def test_apos_rebind_a_chave_velha_nao_resurge(store: RefStore) -> None:
    """A entrada antiga do indice tem de sair, senao um elemento com o runtime_id
    reciclado pelo Windows herdaria a ref de um elemento que nao existe mais.
    """
    ref = registrar(store, ElementoFalso((1, 2, 3), vivo=False))
    store.resolve(ref, automation=AutomationFalsa(achar=ElementoFalso((4, 5, 6))))

    impostor = ElementoFalso((1, 2, 3))
    outra = registrar(store, impostor)

    assert outra != ref


def test_rebind_atualiza_o_runtime_id_da_entrada(store: RefStore) -> None:
    ref = registrar(store, ElementoFalso((1, 2, 3), vivo=False))
    store.resolve(ref, automation=AutomationFalsa(achar=ElementoFalso((4, 5, 6))))

    assert store.get(ref).runtime_id == (4, 5, 6)


def test_segundo_resolve_apos_rebind_usa_o_probe(store: RefStore) -> None:
    """Se a entrada ficasse com o runtime_id velho, TODA chamada seguinte rebindaria."""
    ref = registrar(store, ElementoFalso((1, 2, 3), vivo=False))
    a = AutomationFalsa(achar=ElementoFalso((4, 5, 6)))
    store.resolve(ref, automation=a)

    _, rebound = store.resolve(ref, automation=a)

    assert rebound is False
    assert len(a.buscas) == 1, "rebindou de novo o que ja estava resolvido"
