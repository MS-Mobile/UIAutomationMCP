from __future__ import annotations

import pytest

from mcp_windows_uia.errors import Code, ToolError
from mcp_windows_uia.uia.waits import BACKOFF_TETO_MS, esperar_por, proximo_intervalo


def test_backoff_sobe_e_estabiliza_no_teto() -> None:
    """Spec §7.3: 50 -> 100 -> 200, teto 250."""
    assert proximo_intervalo(50) == 100
    assert proximo_intervalo(100) == 200
    assert proximo_intervalo(200) == BACKOFF_TETO_MS
    assert proximo_intervalo(BACKOFF_TETO_MS) == BACKOFF_TETO_MS


def test_satisfeito_na_primeira_tentativa_nao_dorme() -> None:
    dormidas = []
    r = esperar_por(lambda: "achei", timeout_ms=5000, poll_ms=50,
                    dormir=dormidas.append, agora=iter([0.0, 0.0]).__next__)
    assert r.satisfeito is True
    assert r.resultado == "achei"
    assert r.polls == 1
    assert dormidas == []


def test_satisfeito_depois_de_algumas_tentativas() -> None:
    tentativas = [None, None, "achei"]
    relogio = [0.0]

    def condicao():
        return tentativas.pop(0)

    def dormir(ms):
        relogio[0] += ms / 1000.0

    r = esperar_por(condicao, timeout_ms=5000, poll_ms=50,
                    dormir=dormir, agora=lambda: relogio[0])
    assert r.satisfeito is True
    assert r.polls == 3


def test_timeout_levanta_com_contagem_de_polls() -> None:
    relogio = [0.0]

    def dormir(ms):
        relogio[0] += ms / 1000.0

    with pytest.raises(ToolError) as exc:
        esperar_por(lambda: None, timeout_ms=1500, poll_ms=50,
                    dormir=dormir, agora=lambda: relogio[0])

    assert exc.value.code is Code.TIMEOUT
    assert exc.value.details["polls"] > 5  # CA-11
    assert exc.value.details["waited_ms"] >= 1500


def test_timeout_traz_hint_acionavel() -> None:
    """CA-11: o hint precisa nomear a proxima tool concreta."""
    relogio = [0.0]

    with pytest.raises(ToolError) as exc:
        esperar_por(lambda: None, timeout_ms=200, poll_ms=50,
                    dormir=lambda ms: relogio.__setitem__(0, relogio[0] + ms / 1000.0),
                    agora=lambda: relogio[0])

    assert "uia_get_tree" in exc.value.hint


def test_condicao_que_levanta_toolerror_propaga() -> None:
    """Erro real (ex.: WINDOW_CLOSED) nao deve virar TIMEOUT silencioso."""
    def condicao():
        raise ToolError(Code.WINDOW_CLOSED, "sumiu")

    with pytest.raises(ToolError) as exc:
        esperar_por(condicao, timeout_ms=1000, poll_ms=50,
                    dormir=lambda _ms: None, agora=lambda: 0.0)
    assert exc.value.code is Code.WINDOW_CLOSED
