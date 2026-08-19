from __future__ import annotations

import pytest

from mcp_windows_uia.errors import (
    DEFAULT_HINTS,
    Code,
    ToolError,
    from_com_error,
    hresult_to_code,
)


def test_tool_error_serializa_no_formato_da_spec() -> None:
    err = ToolError(
        Code.ELEMENT_DISABLED,
        "Element w4-e12 ('Salvar', Button) is disabled and cannot be invoked.",
        ref="w4-e12",
        name="Salvar",
        type="Button",
        window_ref="w4",
    )
    d = err.to_dict()

    assert d["ok"] is False
    assert d["error"]["code"] == "ELEMENT_DISABLED"
    assert d["error"]["message"].startswith("Element w4-e12")
    assert d["error"]["details"]["ref"] == "w4-e12"
    assert d["error"]["details"]["retryable"] is True


def test_hint_padrao_nomeia_a_proxima_tool() -> None:
    err = ToolError(Code.WINDOW_NOT_FOUND, "Window w9 does not exist.")
    assert "uia_list_windows" in err.to_dict()["error"]["hint"]


def test_hint_explicito_sobrepoe_o_padrao() -> None:
    err = ToolError(Code.TIMEOUT, "Nope.", hint="Custom next step.")
    assert err.to_dict()["error"]["hint"] == "Custom next step."


def test_todo_codigo_tem_hint_padrao() -> None:
    """Principio 3 da spec: erros sao instrucoes. Nenhum codigo fica sem hint."""
    faltando = [c.value for c in Code if c not in DEFAULT_HINTS]
    assert faltando == []


@pytest.mark.parametrize(
    ("hresult", "esperado"),
    [
        (0x80040201, Code.STALE_REF),
        (0x80040200, Code.PATTERN_NOT_SUPPORTED),
        (0x80131505, Code.TIMEOUT),
        (0x80070005, Code.ELEVATION_REQUIRED),
        (0x800706BA, Code.WINDOW_CLOSED),
        (0x8000FFFF, Code.UIA_COM_ERROR),
    ],
)
def test_mapeamento_de_hresult(hresult: int, esperado: Code) -> None:
    assert hresult_to_code(hresult) is esperado


def test_hresult_negativo_e_normalizado() -> None:
    """comtypes entrega HRESULT com sinal; 0x80040201 chega como -2147220991."""
    assert hresult_to_code(-2147220991) is Code.STALE_REF


class _FakeCOMError(Exception):
    def __init__(self, hresult: int) -> None:
        super().__init__(hresult, "fake", None)
        self.hresult = hresult
        self.text = "fake com failure"


def test_from_com_error_preserva_hresult_nos_details() -> None:
    err = from_com_error(_FakeCOMError(-2147220991), ref="w3-e142")
    assert err.code is Code.STALE_REF
    assert err.details["ref"] == "w3-e142"
    assert err.details["hresult"] == "0x80040201"


def test_from_com_error_sem_hresult_cai_no_catch_all() -> None:
    err = from_com_error(ValueError("algo estranho"))
    assert err.code is Code.UIA_COM_ERROR
