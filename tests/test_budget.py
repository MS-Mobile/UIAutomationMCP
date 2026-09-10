from __future__ import annotations

import base64
import string

import pytest

from mcp_windows_uia.budget import (
    CURSOR_TTL_S,
    HARD_MAX_NODES,
    Limits,
    clamp,
    decode_cursor,
    encode_cursor,
    truncate_name,
    truncate_text,
)
from mcp_windows_uia.errors import Code, ToolError


def test_clamp_prende_nas_bordas() -> None:
    assert clamp(5, 1, 10) == 5
    assert clamp(0, 1, 10) == 1
    assert clamp(99, 1, 10) == 10


def test_limits_aplica_o_teto_absoluto_de_1500_nos() -> None:
    """CA-09: max_nodes=99999 nunca produz mais de 1500 nos."""
    lim = Limits.build(max_nodes=99999, max_depth=999, max_children_per_node=99999)
    assert lim.max_nodes == HARD_MAX_NODES == 1500
    assert lim.max_depth == 40
    assert lim.max_children_per_node == 500


def test_limits_usa_defaults_da_spec() -> None:
    lim = Limits.build()
    assert (lim.max_nodes, lim.max_depth, lim.max_children_per_node) == (200, 12, 30)


def test_truncate_name_corta_em_120_com_reticencias() -> None:
    assert truncate_name("a" * 200) == "a" * 119 + "…"
    assert truncate_name("curto") == "curto"
    assert len(truncate_name("a" * 200)) == 120


def test_truncate_name_aceita_vazio_e_none() -> None:
    assert truncate_name("") == ""
    assert truncate_name(None) == ""


def test_truncate_text_devolve_texto_e_proximo_offset() -> None:
    assert truncate_text("abcdefghij", max_chars=4, offset=0) == ("abcd", True, 4)
    assert truncate_text("abcdefghij", max_chars=4, offset=8) == ("ij", False, None)


def test_cursor_roundtrip() -> None:
    c = encode_cursor("w3", tree_version=7, position=200, now=1000.0)
    assert decode_cursor(c, now=1010.0) == ("w3", 7, 200)


def test_cursor_e_opaco() -> None:
    c = encode_cursor("w3", tree_version=7, position=200, now=1000.0)
    assert c.isascii()
    assert not c.startswith("{")


def test_cursor_expira_em_120s() -> None:
    c = encode_cursor("w3", tree_version=7, position=200, now=1000.0)
    with pytest.raises(ToolError) as exc:
        decode_cursor(c, now=1121.0)
    assert exc.value.code is Code.INVALID_ARGUMENT
    assert "expired" in exc.value.message.lower()


def test_cursor_corrompido_vira_invalid_argument() -> None:
    with pytest.raises(ToolError) as exc:
        decode_cursor("nao-e-base64-valido!!", now=1000.0)
    assert exc.value.code is Code.INVALID_ARGUMENT


def test_cursor_nao_vaza_o_window_ref_em_claro() -> None:
    """Opaco de verdade: o agente nao deve ler nem fabricar o conteudo."""
    c = encode_cursor("w3", tree_version=7, position=200, now=1000.0)
    assert "w3" not in c
    assert "tree_version" not in c and "position" not in c


def test_cursor_e_base64_url_safe_sem_padding() -> None:
    """Sem '=' nem '+/' — vai em JSON e em URL sem escapar; decode repoe o padding."""
    c = encode_cursor("w" * 13, tree_version=7, position=200, now=1000.0)
    assert "=" not in c
    assert set(c) <= set(string.ascii_letters + string.digits + "-_")
    assert decode_cursor(c, now=1000.0) == ("w" * 13, 7, 200)


def test_cursor_vazio_e_rejeitado_como_invalido() -> None:
    with pytest.raises(ToolError) as exc:
        decode_cursor("", now=1000.0)
    assert exc.value.code is Code.INVALID_ARGUMENT


def test_cursor_com_json_valido_mas_sem_as_chaves_e_rejeitado() -> None:
    """Base64 legitimo de um JSON alheio nao pode virar posicao 0 silenciosamente."""
    forjado = base64.urlsafe_b64encode(b'{"x":1}').decode("ascii").rstrip("=")
    with pytest.raises(ToolError) as exc:
        decode_cursor(forjado, now=1000.0)
    assert exc.value.code is Code.INVALID_ARGUMENT


def test_cursor_invalido_traz_hint_mandando_repetir_sem_cursor() -> None:
    """Erro e instrucao (§9): o agente tem de saber que a saida e largar o cursor."""
    for ruim in ("nao-e-base64!!", "", base64.urlsafe_b64encode(b"[]").decode()):
        with pytest.raises(ToolError) as exc:
            decode_cursor(ruim, now=1000.0)
        assert "cursor" in exc.value.hint.lower()

    expirado = encode_cursor("w3", tree_version=7, position=200, now=1000.0)
    with pytest.raises(ToolError) as exc:
        decode_cursor(expirado, now=1000.0 + 121.0)
    assert "cursor" in exc.value.hint.lower()


def test_cursor_no_limite_exato_do_ttl_ainda_vale() -> None:
    c = encode_cursor("w3", tree_version=7, position=200, now=1000.0)
    assert decode_cursor(c, now=1000.0 + CURSOR_TTL_S) == ("w3", 7, 200)
