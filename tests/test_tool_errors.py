"""O decorator tool_errors audita falha. Spec §10.3 e CA-13.

A spec pede "uma linha por chamada de tool (sucesso e falha)". O caminho de sucesso e
auditado dentro de cada `*_impl`; a falha so podia ser auditada no decorator, que e o
unico ponto por onde toda excecao passa.
"""

from __future__ import annotations

import json

import pytest

from mcp_windows_uia.config import (
    AllowlistConfig,
    AuditConfig,
    Config,
    DenylistConfig,
    KeysConfig,
    ServerConfig,
)
from mcp_windows_uia.context import ServerContext, set_context
from mcp_windows_uia.errors import Code, ToolError
from mcp_windows_uia.server import tool_errors


@pytest.fixture
def ctx(tmp_path):
    """Contexto sem worker: nada aqui toca COM."""
    cfg = Config(
        server=ServerConfig(),
        allowlist=AllowlistConfig(processes=frozenset({"notepad.exe"})),
        denylist=DenylistConfig(processes=frozenset()),
        audit=AuditConfig(dir=tmp_path / "audit"),
        keys=KeysConfig(),
    )
    c = ServerContext(cfg)
    set_context(c)
    return c


def linhas(ctx) -> list[dict]:
    return [
        json.loads(linha)
        for arquivo in sorted(ctx.config.audit.dir.glob("*.jsonl"))
        for linha in arquivo.read_text(encoding="utf-8").splitlines()
        if linha.strip()
    ]


async def test_negacao_de_policy_vira_result_denied(ctx) -> None:
    @tool_errors
    async def uia_falsa(window_ref: str):
        raise ToolError(Code.APP_NOT_ALLOWED, "Process calc.exe is not allowed.")

    r = await uia_falsa(window_ref="w1")

    assert r["ok"] is False
    registro = linhas(ctx)[-1]
    assert registro["result"] == "denied"
    assert registro["code"] == "APP_NOT_ALLOWED"
    assert registro["tool"] == "uia_falsa"
    assert registro["params"] == {"window_ref": "w1"}


async def test_falha_que_nao_e_policy_vira_result_error(ctx) -> None:
    """Distinguir importa: 'denied' e o servidor recusando, 'error' e algo quebrando."""

    @tool_errors
    async def uia_falsa(ref: str):
        raise ToolError(Code.ELEMENT_NOT_FOUND, "gone")

    await uia_falsa(ref="w1-e2")
    assert linhas(ctx)[-1]["result"] == "error"


async def test_excecao_inesperada_tambem_e_auditada(ctx) -> None:
    @tool_errors
    async def uia_falsa(ref: str):
        raise ZeroDivisionError("boom")

    r = await uia_falsa(ref="w1-e2")

    assert r["error"]["code"] == "UIA_COM_ERROR"
    assert linhas(ctx)[-1]["code"] == "UIA_COM_ERROR"


async def test_parametro_fora_da_allowlist_nunca_entra_no_log(ctx) -> None:
    """CA-15: a auditoria de falha nao pode ser a porta por onde um valor vaza."""

    @tool_errors
    async def uia_falsa(ref: str, value: str, keys: str):
        raise ToolError(Code.ELEMENT_READONLY, "read only")

    await uia_falsa(ref="w1-e2", value="s3nh4-secreta", keys="^a")

    registro = linhas(ctx)[-1]
    assert registro["params"] == {"ref": "w1-e2"}
    assert "s3nh4-secreta" not in json.dumps(registro)


async def test_sucesso_nao_gera_linha_no_decorator(ctx) -> None:
    """Senao a chamada bem-sucedida sairia duas vezes: aqui e dentro do *_impl."""

    @tool_errors
    async def uia_falsa(ref: str):
        return {"ok": True}

    await uia_falsa(ref="w1-e2")
    assert linhas(ctx) == []
