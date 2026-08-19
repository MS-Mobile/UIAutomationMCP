from __future__ import annotations

import pytest

from mcp_windows_uia.errors import Code, ToolError
from mcp_windows_uia.refs import ElementIdentity, RefStore


class FakeElement:
    """Stand-in para IUIAutomationElement. O RefStore so o guarda, nunca desreferencia."""

    def __init__(self, tag: str) -> None:
        self.tag = tag


def _ident(aid: str = "SaveButton", nome: str = "Salvar") -> ElementIdentity:
    return ElementIdentity(
        automation_id=aid,
        control_type="Button",
        name=nome,
        class_name="Button",
        index_path=(0, 3, 1),
    )


def _store(**kw: object) -> RefStore:
    kw.setdefault("clock", lambda: 1000.0)
    return RefStore(**kw)  # type: ignore[arg-type]


def test_window_ref_tem_o_formato_da_spec() -> None:
    store = _store()
    assert store.window_ref(hwnd=723918) == "w1"
    assert store.window_ref(hwnd=198442) == "w2"


def test_window_ref_e_estavel_para_o_mesmo_hwnd() -> None:
    store = _store()
    assert store.window_ref(hwnd=723918) == store.window_ref(hwnd=723918)


def test_element_ref_tem_o_formato_da_spec() -> None:
    store = _store()
    wref = store.window_ref(hwnd=1)
    ref = store.put(
        FakeElement("a"), runtime_id=(42, 7), hwnd=1, window_ref=wref,
        identity=_ident(), tree_version=1,
    )
    assert ref.startswith("w1-e")


def test_put_deduplica_por_runtime_id() -> None:
    """Recapturar a arvore nao pode inflar o store nem trocar as refs ja entregues."""
    store = _store()
    wref = store.window_ref(hwnd=1)
    a = store.put(FakeElement("a"), runtime_id=(42, 7), hwnd=1, window_ref=wref,
                  identity=_ident(), tree_version=1)
    b = store.put(FakeElement("b"), runtime_id=(42, 7), hwnd=1, window_ref=wref,
                  identity=_ident(), tree_version=2)
    assert a == b
    assert len(store) == 1
    assert store.get(a).element.tag == "b"
    assert store.get(a).tree_version == 2


def test_runtime_id_igual_em_janelas_diferentes_nao_colide() -> None:
    store = _store()
    w1, w2 = store.window_ref(hwnd=1), store.window_ref(hwnd=2)
    a = store.put(FakeElement("a"), runtime_id=(42, 7), hwnd=1, window_ref=w1,
                  identity=_ident(), tree_version=1)
    b = store.put(FakeElement("b"), runtime_id=(42, 7), hwnd=2, window_ref=w2,
                  identity=_ident(), tree_version=1)
    assert a != b


def test_get_de_ref_desconhecida_e_ref_not_found() -> None:
    store = _store()
    with pytest.raises(ToolError) as exc:
        store.get("w9-e99")
    assert exc.value.code is Code.REF_NOT_FOUND


def test_ref_expirada_vira_stale_ref_com_motivo_expired() -> None:
    agora = [1000.0]
    store = RefStore(ttl_s=300, clock=lambda: agora[0])
    wref = store.window_ref(hwnd=1)
    ref = store.put(FakeElement("a"), runtime_id=(1,), hwnd=1, window_ref=wref,
                    identity=_ident(), tree_version=1)
    agora[0] += 301.0
    with pytest.raises(ToolError) as exc:
        store.get(ref)
    assert exc.value.code is Code.STALE_REF
    assert exc.value.details["reason"] == "expired"


def test_get_bem_sucedido_renova_o_ttl() -> None:
    agora = [1000.0]
    store = RefStore(ttl_s=300, clock=lambda: agora[0])
    wref = store.window_ref(hwnd=1)
    ref = store.put(FakeElement("a"), runtime_id=(1,), hwnd=1, window_ref=wref,
                    identity=_ident(), tree_version=1)
    agora[0] += 200.0
    store.get(ref)
    agora[0] += 200.0
    assert store.get(ref).ref == ref


def test_invalidate_window_derruba_todas_as_refs_da_janela() -> None:
    store = _store()
    w1, w2 = store.window_ref(hwnd=1), store.window_ref(hwnd=2)
    a = store.put(FakeElement("a"), runtime_id=(1,), hwnd=1, window_ref=w1,
                  identity=_ident(), tree_version=1)
    b = store.put(FakeElement("b"), runtime_id=(2,), hwnd=2, window_ref=w2,
                  identity=_ident(), tree_version=1)
    assert store.invalidate_window(w1) == 1
    with pytest.raises(ToolError):
        store.get(a)
    assert store.get(b).ref == b


def test_evicao_lru_respeita_a_capacidade() -> None:
    store = RefStore(max_entries=3, clock=lambda: 1000.0)
    wref = store.window_ref(hwnd=1)
    refs = [
        store.put(FakeElement(str(i)), runtime_id=(i,), hwnd=1, window_ref=wref,
                  identity=_ident(), tree_version=1)
        for i in range(4)
    ]
    assert len(store) == 3
    with pytest.raises(ToolError) as exc:
        store.get(refs[0])
    assert exc.value.code is Code.REF_NOT_FOUND
    assert store.get(refs[3]).ref == refs[3]


def test_get_promove_a_entrada_no_lru() -> None:
    store = RefStore(max_entries=2, clock=lambda: 1000.0)
    wref = store.window_ref(hwnd=1)
    a = store.put(FakeElement("a"), runtime_id=(1,), hwnd=1, window_ref=wref,
                  identity=_ident(), tree_version=1)
    b = store.put(FakeElement("b"), runtime_id=(2,), hwnd=1, window_ref=wref,
                  identity=_ident(), tree_version=1)
    store.get(a)
    c = store.put(FakeElement("c"), runtime_id=(3,), hwnd=1, window_ref=wref,
                  identity=_ident(), tree_version=1)
    assert store.get(a).ref == a
    assert store.get(c).ref == c
    with pytest.raises(ToolError):
        store.get(b)


def test_bump_tree_version_incrementa_por_janela() -> None:
    store = _store()
    w1 = store.window_ref(hwnd=1)
    assert store.bump_tree_version(w1) == 1
    assert store.bump_tree_version(w1) == 2
    assert store.tree_version(w1) == 2
    assert store.tree_version(store.window_ref(hwnd=2)) == 0


def test_hwnd_de_window_ref() -> None:
    store = _store()
    w1 = store.window_ref(hwnd=723918)
    assert store.hwnd_for(w1) == 723918
    with pytest.raises(ToolError) as exc:
        store.hwnd_for("w404")
    assert exc.value.code is Code.WINDOW_NOT_FOUND
