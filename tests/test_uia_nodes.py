from __future__ import annotations

import pytest

from mcp_windows_uia.uia import nodes
from mcp_windows_uia.uia.nodes import STATE_KEYS, build_node, states_of

# Ids ficticios: mantem estes testes puros, sem carregar o typelib do Windows.
IDS_FALSOS = {chave: 900 + i for i, (chave, _p, _n) in enumerate(nodes._PROPS_DE_PATTERN)}


@pytest.fixture(autouse=True)
def _ids_sem_com(monkeypatch):
    monkeypatch.setattr(nodes, "_ids_de_prop", IDS_FALSOS)


class FakeCached:
    """Stand-in de IUIAutomationElement, fiel na forma de acesso.

    A distincao importa: as ~32 propriedades `Cached*` do typelib sao atributos, mas as
    derivadas de pattern SO existem via GetCachedPropertyValue(prop_id). O fake antigo
    expunha as duas como atributo — ficcao que escondeu por tres tasks o fato de
    `CachedToggleToggleState` nao existir na interface.

    E devolve o mesmo veneno que o provider real devolve quando o pattern nao existe,
    para que o portao de disponibilidade seja provado aqui, e nao so contra o Windows.
    """

    # Medido no Bloco de Notas: e isto que volta para quem NAO suporta o pattern.
    VENENO = {"toggle": 2, "expand": 3, "selected": True,
              "readonly": True, "value": "", "range": 0.0}

    def __init__(self, *, props: dict[str, object] | None = None, **kw):
        padrao = {
            "CachedName": "",
            "CachedAutomationId": "",
            "CachedClassName": "",
            "CachedControlType": 50000,  # Button
            "CachedIsEnabled": 1,
            "CachedIsOffscreen": 0,
            "CachedIsKeyboardFocusable": 1,
            "CachedHasKeyboardFocus": 0,
            "CachedIsPassword": 0,
            "CachedProcessId": 100,
            "CachedBoundingRectangle": (0, 0, 10, 10),
        }
        padrao.update(kw)
        for k, v in padrao.items():
            setattr(self, k, v)
        self._props = props or {}

    def GetCachedPropertyValue(self, prop_id: int):
        for chave, ident in IDS_FALSOS.items():
            if ident == prop_id:
                return self._props.get(chave, self.VENENO[chave])
        raise ValueError(f"propriedade {prop_id} fora do CacheRequest")


def test_node_tem_as_chaves_obrigatorias_da_spec() -> None:
    n = build_node(FakeCached(CachedName="Salvar"), ref="w1-e5", depth=2, patterns=["Invoke"])
    for chave in ("ref", "d", "type", "name", "st", "pat"):
        assert chave in n
    assert n["ref"] == "w1-e5"
    assert n["d"] == 2
    assert n["type"] == "Button"
    assert n["name"] == "Salvar"
    assert n["pat"] == ["Invoke"]


def test_chaves_opcionais_omitidas_quando_vazias() -> None:
    """Economia de token: aid/cls/val nao aparecem se nao houver conteudo."""
    n = build_node(FakeCached(), ref="w1-e0", depth=0, patterns=[])
    assert "aid" not in n
    assert "cls" not in n
    assert "val" not in n


def test_chaves_opcionais_presentes_quando_ha_conteudo() -> None:
    n = build_node(
        FakeCached(CachedAutomationId="SaveBtn", CachedClassName="Button"),
        ref="w1-e1", depth=1, patterns=[],
    )
    assert n["aid"] == "SaveBtn"
    assert n["cls"] == "Button"


def test_name_truncado_em_120_chars() -> None:
    n = build_node(FakeCached(CachedName="a" * 300), ref="w1-e1", depth=0, patterns=[])
    assert len(n["name"]) == 120
    assert n["name"].endswith("…")


def test_estados_refletem_as_propriedades() -> None:
    st = states_of(FakeCached(CachedIsEnabled=1, CachedIsKeyboardFocusable=1,
                              CachedHasKeyboardFocus=1))
    assert "enabled" in st and "focusable" in st and "focused" in st
    assert "disabled" not in st


def test_disabled_e_offscreen_sao_explicitos() -> None:
    st = states_of(FakeCached(CachedIsEnabled=0, CachedIsOffscreen=1))
    assert "disabled" in st and "offscreen" in st
    assert "enabled" not in st


def test_toggle_state_vira_checked_unchecked_indeterminate() -> None:
    for estado, rotulo in [(0, "unchecked"), (1, "checked"), (2, "indeterminate")]:
        assert rotulo in states_of(FakeCached(props={"toggle": estado}), ["Toggle"])


def test_sem_o_pattern_nenhum_estado_derivado_aparece() -> None:
    """O portao da §5.1: provider que nao suporta o pattern devolve default, nao vazio.

    Sem checar disponibilidade, todo Pane e todo Button sairiam "indeterminate" e
    "readonly" — medido no Bloco de Notas real.
    """
    st = states_of(FakeCached(), patterns=[])
    for fantasma in ("checked", "unchecked", "indeterminate", "expanded",
                     "collapsed", "selected", "readonly"):
        assert fantasma not in st


def test_pattern_de_um_tipo_nao_libera_propriedade_de_outro() -> None:
    st = states_of(FakeCached(), patterns=["Toggle"])
    assert "indeterminate" in st  # Toggle existe: o veneno 2 e resposta legitima
    assert "readonly" not in st   # Value nao existe: continua barrado


def test_valor_de_toggle_vai_para_val() -> None:
    n = build_node(FakeCached(props={"toggle": 1}), ref="w1-e1", depth=0,
                   patterns=["Toggle"])
    assert n["val"] == "on"


def test_campo_de_senha_nunca_expoe_o_valor() -> None:
    """Spec §5.1: regra de redacao. CA-15 depende disto."""
    n = build_node(
        FakeCached(CachedIsPassword=1, props={"value": "s3nh4-secreta"}),
        ref="w1-e9", depth=3, patterns=["Value"],
    )
    assert n["val"] == "«redacted:password»"
    assert "s3nh4-secreta" not in repr(n)
    assert "password" in n["st"]


def test_rect_omitido_quando_area_zero() -> None:
    n = build_node(FakeCached(CachedBoundingRectangle=(0, 0, 0, 0)), ref="w1-e1",
                   depth=0, patterns=[])
    assert "rect" not in n


def test_todo_estado_declarado_e_conhecido_pela_spec() -> None:
    """Guarda contra digitar um estado que a spec §5.1 nao lista."""
    da_spec = {
        "enabled", "disabled", "focused", "focusable", "offscreen", "selected",
        "expanded", "collapsed", "checked", "unchecked", "indeterminate",
        "readonly", "required", "password", "multiline",
    }
    assert set(STATE_KEYS) <= da_spec
