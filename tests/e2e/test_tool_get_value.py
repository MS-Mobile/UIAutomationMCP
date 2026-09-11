"""uia_get_value contra janela real. Cobre §8.5 e o CA-15 (redacao de senha).

O Bloco de Notas do Windows 11 nao tem nenhum ControlType `Edit`: a area de texto e
um `Document` (classe `RichEditD2DPT`) dentro de um `Pane` `NotepadTextBox`. Medido
nesta maquina — a arvore inteira da janela tem 47 nos e nenhum `Edit`. Por isso os
testes buscam `control_type="Document"`.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e.test_tool_get_tree import notepad, servidor  # noqa: F401

pytestmark = pytest.mark.e2e

SENHA = "s3nh4-secreta"


def _linhas_de_auditoria(ctx) -> list[dict]:
    linhas = []
    for arquivo in sorted(ctx.config.audit.dir.glob("*.jsonl")):
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            if linha.strip():
                linhas.append(json.loads(linha))
    return linhas


def _texto_bruto_da_auditoria(ctx) -> str:
    """O arquivo inteiro, sem parse: e nele que a busca por CA-15 tem de ser feita."""
    return "".join(
        arquivo.read_text(encoding="utf-8")
        for arquivo in sorted(ctx.config.audit.dir.glob("*.jsonl"))
    )


async def test_le_o_valor_da_area_de_edicao(servidor, notepad) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_find_elements, uia_get_value

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    achados = await uia_find_elements(window_ref=wref, control_type="Document")
    assert achados["ok"] is True, achados
    ref = achados["matches"][0]["ref"]

    r = await uia_get_value(ref=ref)
    assert r["ok"] is True, r
    assert r["ref"] == ref
    assert r["rebound"] is False
    assert r["type"] == "Document"
    assert isinstance(r["value"], str)
    assert r["source"] in ("ValuePattern", "TextPattern", "LegacyIAccessible", "Name")
    assert r["value_type"] == "string"
    assert r["truncated"] is False
    assert "enabled" in r["st"]
    assert "Value" in r["pat"] or "Text" in r["pat"]


async def test_toggle_button_devolve_on_ou_off(servidor, notepad) -> None:  # noqa: F811
    """A barra de formatacao do Bloco de Notas tem ToggleButtons (Negrito, Italico)."""
    from mcp_windows_uia.server import uia_find_elements, uia_get_value

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    achados = await uia_find_elements(window_ref=wref, class_name="ToggleButton", max_results=1)
    if not achados.get("ok"):
        pytest.skip("esta build do Bloco de Notas nao expoe a barra de formatacao")

    r = await uia_get_value(ref=achados["matches"][0]["ref"])
    assert r["ok"] is True, r
    assert r["source"] == "TogglePattern"
    assert r["value"] in ("on", "off", "indeterminate")
    assert r["value_type"] == "toggle"


async def test_max_chars_trunca_e_sinaliza(servidor, notepad) -> None:  # noqa: F811
    """Valor mais longo que max_chars sai cortado com truncated=true (§6.1)."""
    from mcp_windows_uia.server import uia_find_elements, uia_get_value

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    achados = await uia_find_elements(
        window_ref=wref, control_type="TitleBar", only_interactive=False
    )
    if not achados.get("ok"):
        pytest.skip("janela sem TitleBar")
    ref = achados["matches"][0]["ref"]

    inteiro = await uia_get_value(ref=ref)
    if not isinstance(inteiro.get("value"), str) or len(inteiro["value"]) < 2:
        pytest.skip("TitleBar sem valor longo o bastante para truncar")

    cortado = await uia_get_value(ref=ref, max_chars=1)
    assert cortado["ok"] is True, cortado
    assert cortado["value"] == inteiro["value"][:1]
    assert cortado["truncated"] is True
    assert inteiro["truncated"] is False


class _ValuePatternFalso:
    """Devolve a senha se alguem chegar a consultar. E o que o CA-15 proibe."""

    CurrentValue = SENHA

    def QueryInterface(self, _iface):  # noqa: N802 - assinatura do COM
        return self


class ElementoDeSenha:
    """Duble de IUIAutomationElement de um campo de senha PREENCHIDO.

    Ele guarda a senha de verdade e a entregaria a quem pedisse: e assim que o teste
    consegue falhar se a porta da §8.5 for removida. Um duble sem senha nenhuma
    tornaria a busca no arquivo de auditoria vazia por construcao.
    """

    CurrentIsPassword = 1
    CachedIsPassword = 1
    CachedName = "Password"
    CachedControlType = 50004  # UIA_EditControlTypeId
    CachedAutomationId = "PasswordBox"
    CachedIsEnabled = 1
    CurrentName = SENHA

    def __init__(self) -> None:
        self.patterns_consultados: list[int] = []

    def BuildUpdatedCache(self, _cr):  # noqa: N802 - assinatura do COM
        return self

    def GetRuntimeId(self):  # noqa: N802 - assinatura do COM
        # O mesmo com que foi registrado: assim o probe da §7.2 acerta e o duble nao
        # passa pelo rebind, que procuraria um PasswordBox no Bloco de Notas real.
        return (7, 7, 7, 7)

    def GetCachedPropertyValue(self, _prop_id):  # noqa: N802 - assinatura do COM
        return 0

    def GetCurrentPattern(self, pattern_id):  # noqa: N802 - assinatura do COM
        self.patterns_consultados.append(pattern_id)
        return _ValuePatternFalso()


async def test_senha_nao_vaza_nem_na_resposta_nem_na_auditoria(  # noqa: F811
    servidor, notepad
) -> None:
    """CA-15: IsPassword=true redige em qualquer modo de log_values.

    O Bloco de Notas nao tem campo de senha, e abrir um app de terceiros que tenha
    tornaria o teste dependente de software externo. O que precisa ser provado e a
    decisao do servidor diante de um elemento que se declara senha, entao o elemento
    e um duble registrado no RefStore da janela real: policy, worker, auditoria e
    serializacao sao todos os de producao.
    """
    from mcp_windows_uia.refs import ElementIdentity
    from mcp_windows_uia.server import uia_get_value

    elemento = ElementoDeSenha()
    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    ref = servidor.refs.put(
        elemento,
        runtime_id=(7, 7, 7, 7),
        hwnd=notepad.hwnd,
        window_ref=wref,
        identity=ElementIdentity(automation_id="PasswordBox", control_type="Edit"),
        tree_version=servidor.refs.tree_version(wref),
    )

    # log_values='full' e o modo mais permissivo que existe: se a senha sobrevive a
    # redacao, e aqui que ela aparece. AuditConfig e frozen de proposito (config nao
    # muda em runtime), entao o teste force a troca e a desfaz.
    anterior = servidor.config.audit.log_values
    object.__setattr__(servidor.config.audit, "log_values", "full")
    try:
        r = await uia_get_value(ref=ref)
    finally:
        object.__setattr__(servidor.config.audit, "log_values", anterior)

    assert r["ok"] is True, r
    assert r["value"] == "«redacted:password»"
    assert r["source"] == "redacted"
    assert r["value_type"] == "string"
    assert "password" in r["st"]
    assert SENHA not in json.dumps(r, ensure_ascii=False)
    # Nem consultada: a §8.5 nao le a fonte de um campo de senha.
    assert elemento.patterns_consultados == []

    assert SENHA not in _texto_bruto_da_auditoria(servidor)
    linha = [x for x in _linhas_de_auditoria(servidor) if x["tool"] == "uia_get_value"][-1]
    assert linha["result"] == "ok"
    assert linha["value"] == "«redacted:password»"
    assert "value_sha256" not in linha


async def test_audita_sucesso_uma_vez_so(servidor, notepad) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_find_elements, uia_get_value

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    achados = await uia_find_elements(window_ref=wref, control_type="Document")
    ref = achados["matches"][0]["ref"]

    antes = len([x for x in _linhas_de_auditoria(servidor) if x["tool"] == "uia_get_value"])
    await uia_get_value(ref=ref)
    depois = [x for x in _linhas_de_auditoria(servidor) if x["tool"] == "uia_get_value"]

    assert len(depois) == antes + 1
    assert depois[-1]["result"] == "ok"
    assert depois[-1]["params"]["ref"] == ref
    assert depois[-1]["target"]["process"].lower() == "notepad.exe"


async def test_ref_desconhecida_e_erro_acionavel(servidor) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_get_value

    r = await uia_get_value(ref="w1-e999999")
    assert r["ok"] is False
    assert r["error"]["code"] in ("REF_NOT_FOUND", "STALE_REF")
    assert "uia_get_tree" in r["error"]["hint"] or "uia_find_elements" in r["error"]["hint"]

    linha = [x for x in _linhas_de_auditoria(servidor) if x["tool"] == "uia_get_value"][-1]
    assert linha["result"] == "error"
    assert linha["code"] == r["error"]["code"]
