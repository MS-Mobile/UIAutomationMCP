from __future__ import annotations

import re
from pathlib import Path

import pytest

from mcp_windows_uia.config import load_config
from mcp_windows_uia.context import ServerContext
from mcp_windows_uia.server import list_windows_impl

pytestmark = pytest.mark.e2e


def _contexto(tmp_path: Path, extra: str = "") -> ServerContext:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        '[allowlist]\nmode = "allow"\nprocesses = ["explorer.exe"]\n'
        f'[audit]\ndir = "{tmp_path.as_posix()}/audit"\n' + extra,
        encoding="utf-8",
    )
    ctx = ServerContext(load_config(cfg_path))
    ctx.start()
    return ctx


@pytest.fixture()
def ctx(tmp_path: Path):
    c = _contexto(tmp_path)
    yield c
    c.shutdown()


def test_retorna_estrutura_da_spec(ctx: ServerContext) -> None:
    r = list_windows_impl(ctx, max_results=40)
    assert r["ok"] is True
    assert isinstance(r["server_elevated"], bool)
    assert r["read_only"] is False
    assert isinstance(r["windows"], list)
    assert set(r["stats"]) == {"returned", "total", "filtered_by_allowlist"}


def test_refs_seguem_o_formato_w_n(ctx: ServerContext) -> None:
    for janela in list_windows_impl(ctx, max_results=40)["windows"]:
        assert re.fullmatch(r"w\d+", janela["ref"])


def test_campos_obrigatorios_por_janela(ctx: ServerContext) -> None:
    r = list_windows_impl(ctx, max_results=5)
    if not r["windows"]:
        pytest.skip("nenhuma janela visivel na sessao")
    janela = r["windows"][0]
    for campo in ("ref", "title", "process", "pid", "hwnd", "focused", "minimized",
                  "maximized", "rect", "dpi", "monitor", "allowed", "elevated"):
        assert campo in janela, campo
    assert len(janela["rect"]) == 4


def test_janela_negada_aparece_com_allowed_false(ctx: ServerContext) -> None:
    """Spec §8.1: janelas fora da allowlist sao listadas, para orientacao."""
    janelas = list_windows_impl(ctx, max_results=200)["windows"]
    assert any(j["allowed"] is False for j in janelas) or all(
        j["process"].lower() == "explorer.exe" for j in janelas
    )


def test_refs_sao_estaveis_entre_chamadas(ctx: ServerContext) -> None:
    a = {j["hwnd"]: j["ref"] for j in list_windows_impl(ctx, max_results=200)["windows"]}
    b = {j["hwnd"]: j["ref"] for j in list_windows_impl(ctx, max_results=200)["windows"]}
    comuns = set(a) & set(b)
    assert comuns
    assert all(a[h] == b[h] for h in comuns)


def test_max_results_e_respeitado(ctx: ServerContext) -> None:
    r = list_windows_impl(ctx, max_results=2)
    assert len(r["windows"]) <= 2
    assert r["stats"]["returned"] == len(r["windows"])


def test_process_filter_filtra_por_substring(ctx: ServerContext) -> None:
    for janela in list_windows_impl(ctx, process_filter="explorer", max_results=40)["windows"]:
        assert "explorer" in (janela["process"] + janela["title"]).lower()


def test_hide_denied_omite_e_contabiliza(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        '[server]\nhide_denied = true\n[allowlist]\nprocesses = []\n'
        f'[audit]\ndir = "{tmp_path.as_posix()}/audit"\n',
        encoding="utf-8",
    )
    ctx = ServerContext(load_config(cfg_path))
    ctx.start()
    try:
        r = list_windows_impl(ctx, max_results=200)
        assert r["windows"] == []
        assert r["stats"]["filtered_by_allowlist"] > 0
    finally:
        ctx.shutdown()


def test_chamada_e_auditada(ctx: ServerContext, tmp_path: Path) -> None:
    list_windows_impl(ctx, max_results=5)
    arquivos = list((tmp_path / "audit").glob("*.jsonl"))
    assert arquivos
    assert "uia_list_windows" in arquivos[0].read_text(encoding="utf-8")
