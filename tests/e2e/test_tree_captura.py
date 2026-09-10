"""Captura contra janela real. Cobre CA-02 e CA-21 da spec.

As fixtures `sta` e `bloco_de_notas` vivem em conftest.py — compartilhadas com os
demais modulos e2e para abrir uma unica janela na suite inteira.
"""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.e2e


def test_ca02_arvore_interativa_e_enxuta(bloco_de_notas) -> None:
    """CA-02: <=60 nos, uma area de edicao, sem truncar, nenhum no desabilitado."""
    from mcp_windows_uia.uia.tree import capturar_janela

    r = capturar_janela(bloco_de_notas.hwnd, filtro="interactive")

    assert len(r["nodes"]) <= 60
    assert r["stats"]["truncated"] is False

    editaveis = [n for n in r["nodes"] if n["type"] in ("Edit", "Document")]
    assert len(editaveis) >= 1

    for n in r["nodes"]:
        assert "disabled" not in n["st"]


def test_ca21_performance_da_captura(bloco_de_notas) -> None:
    """CA-21: p95 < 1200 ms em 5 execucoes."""
    from mcp_windows_uia.uia.tree import capturar_janela

    tempos = []
    for _ in range(5):
        t0 = time.perf_counter()
        capturar_janela(bloco_de_notas.hwnd, filtro="interactive", max_nodes=200)
        tempos.append((time.perf_counter() - t0) * 1000)

    assert max(tempos) < 1200, f"tempos: {tempos}"


def test_ca09_max_nodes_absurdo_e_limitado_a_1500(bloco_de_notas) -> None:
    """CA-09: pedir 99999 nunca produz mais de 1500."""
    from mcp_windows_uia.uia.tree import capturar_janela

    r = capturar_janela(bloco_de_notas.hwnd, filtro="all", max_nodes=99999)
    assert len(r["nodes"]) <= 1500


def test_captura_devolve_refs_no_formato_da_spec(bloco_de_notas) -> None:
    from mcp_windows_uia.uia.tree import capturar_janela

    r = capturar_janela(bloco_de_notas.hwnd, filtro="interactive")
    for n in r["nodes"]:
        assert n["ref"].startswith("w")
        assert "-e" in n["ref"]
