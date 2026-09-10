from __future__ import annotations

import pytest

from mcp_windows_uia.errors import Code, ToolError
from mcp_windows_uia.rebind import Estrategia, rebind
from mcp_windows_uia.refs import ElementIdentity


class FakeAutomation:
    """Automation falso: registra as buscas pedidas e devolve o que foi programado."""

    def __init__(self, por_estrategia=None):
        self.por_estrategia = por_estrategia or {}
        self.buscas: list[Estrategia] = []

    def buscar(self, _hwnd, _identity, estrategia):
        self.buscas.append(estrategia)
        return self.por_estrategia.get(estrategia, [])


def ident(**kw) -> ElementIdentity:
    base = {
        "automation_id": "SaveBtn",
        "control_type": "Button",
        "name": "Salvar",
        "class_name": "Button",
        "index_path": (0, 3, 1),
    }
    base.update(kw)
    return ElementIdentity(**base)


def test_acha_por_automation_id_primeiro() -> None:
    a = FakeAutomation({Estrategia.AUTOMATION_ID: ["elem-a"]})
    achado, estrategia = rebind(a, hwnd=1, identity=ident())
    assert achado == "elem-a"
    assert estrategia is Estrategia.AUTOMATION_ID
    assert a.buscas == [Estrategia.AUTOMATION_ID]


def test_cai_para_name_quando_automation_id_nao_acha() -> None:
    a = FakeAutomation({Estrategia.NAME: ["elem-b"]})
    achado, estrategia = rebind(a, hwnd=1, identity=ident())
    assert achado == "elem-b"
    assert estrategia is Estrategia.NAME
    assert a.buscas == [Estrategia.AUTOMATION_ID, Estrategia.NAME]


def test_cai_para_index_path_em_ultimo_caso() -> None:
    a = FakeAutomation({Estrategia.INDEX_PATH: ["elem-c"]})
    achado, estrategia = rebind(a, hwnd=1, identity=ident())
    assert achado == "elem-c"
    assert estrategia is Estrategia.INDEX_PATH
    assert a.buscas == [Estrategia.AUTOMATION_ID, Estrategia.NAME, Estrategia.INDEX_PATH]


def test_pula_automation_id_quando_vazio() -> None:
    """Sem AutomationId nao ha o que buscar; nao desperdica uma chamada COM."""
    a = FakeAutomation({Estrategia.NAME: ["elem-b"]})
    rebind(a, hwnd=1, identity=ident(automation_id=""))
    assert Estrategia.AUTOMATION_ID not in a.buscas


def test_multiplos_candidatos_viram_ambiguous_match() -> None:
    """Spec principio 4: nada de best match silencioso."""
    a = FakeAutomation({Estrategia.AUTOMATION_ID: ["elem-a", "elem-b"]})
    with pytest.raises(ToolError) as exc:
        rebind(a, hwnd=1, identity=ident())
    assert exc.value.code is Code.AMBIGUOUS_MATCH
    assert exc.value.details["count"] == 2


def test_zero_candidatos_em_tudo_vira_stale_ref() -> None:
    a = FakeAutomation({})
    with pytest.raises(ToolError) as exc:
        rebind(a, hwnd=1, identity=ident())
    assert exc.value.code is Code.STALE_REF
    assert exc.value.details["reason"] == "element_gone"


def test_identity_e_serializavel_para_dict_e_de_volta() -> None:
    """Gancho da camada futura de aprendizado: identidade precisa persistir em disco."""
    from mcp_windows_uia.rebind import identity_from_dict, identity_to_dict

    original = ident()
    redonda = identity_from_dict(identity_to_dict(original))
    assert redonda == original


def test_identity_serializada_e_json_puro() -> None:
    import json

    from mcp_windows_uia.rebind import identity_to_dict

    d = identity_to_dict(ident())
    assert json.loads(json.dumps(d)) == d
