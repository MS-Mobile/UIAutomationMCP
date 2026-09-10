from __future__ import annotations

from mcp_windows_uia.uia.nodes import STATE_KEYS, build_node, states_of


class FakeCached:
    """Stand-in de IUIAutomationElement com propriedades Cached*.

    build_node so le Cached*, entao qualquer objeto com esses atributos serve.
    Isso mantem a serializacao testavel sem Windows.
    """

    def __init__(self, **kw):
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
            "CachedToggleToggleState": None,
            "CachedExpandCollapseExpandCollapseState": None,
            "CachedSelectionItemIsSelected": None,
            "CachedValueValue": None,
            "CachedValueIsReadOnly": None,
            "CachedRangeValueValue": None,
        }
        padrao.update(kw)
        for k, v in padrao.items():
            setattr(self, k, v)


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
    assert "unchecked" in states_of(FakeCached(CachedToggleToggleState=0))
    assert "checked" in states_of(FakeCached(CachedToggleToggleState=1))
    assert "indeterminate" in states_of(FakeCached(CachedToggleToggleState=2))


def test_valor_de_toggle_vai_para_val() -> None:
    n = build_node(FakeCached(CachedToggleToggleState=1), ref="w1-e1", depth=0,
                   patterns=["Toggle"])
    assert n["val"] == "on"


def test_campo_de_senha_nunca_expoe_o_valor() -> None:
    """Spec §5.1: regra de redacao. CA-15 depende disto."""
    n = build_node(
        FakeCached(CachedIsPassword=1, CachedValueValue="s3nh4-secreta"),
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
