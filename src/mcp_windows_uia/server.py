"""Definicao das tools MCP. Spec §8 e §11.1.

Padrao de toda tool:
  1. valida argumentos
  2. valida policy (allowlist -> read-only -> rate limit)
  3. executa na thread do worker
  4. audita, sempre — sucesso e falha
Nenhuma excecao sai daqui como traceback: o decorator converte tudo para o §9.

Nota de versao: no `mcp` 2.x a classe e MCPServer (o FastMCP da 1.x foi renomeado;
mcp.server.fastmcp nao existe mais). A API do decorator .tool() e de .run() e a mesma.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .context import ServerContext, context
from .errors import Code, ToolError
from .uia.windows import enumerate_windows, server_is_elevated

_log = logging.getLogger(__name__)

mcp = MCPServer("windows-uia")


def tool_errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Converte qualquer falha no envelope de erro da §9. Nunca deixa vazar traceback."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return await fn(*args, **kwargs)
        except ToolError as exc:
            return exc.to_dict()
        except Exception as exc:  # pragma: no cover - rede de seguranca
            _log.exception("unhandled error in %s", fn.__name__)
            return ToolError(
                Code.UIA_COM_ERROR, f"Unhandled server error in {fn.__name__}: {exc!r}"
            ).to_dict()

    return wrapper


# --------------------------------------------------------------------------- 8.1


def list_windows_impl(
    ctx: ServerContext,
    *,
    include_minimized: bool = True,
    include_hidden: bool = False,
    process_filter: str | None = None,
    max_results: int = 40,
) -> dict[str, Any]:
    """Corpo sincrono de uia_list_windows. Roda na thread do worker."""
    inicio = time.perf_counter()
    limite = max(1, min(200, max_results))
    janelas = enumerate_windows(include_hidden=include_hidden)

    if not include_minimized:
        janelas = [j for j in janelas if not j.minimized]

    if process_filter:
        alvo = process_filter.lower()
        janelas = [j for j in janelas if alvo in j.process.lower() or alvo in j.title.lower()]

    total = len(janelas)
    ocultas_por_policy = 0
    saida: list[dict[str, Any]] = []

    for janela in janelas:
        permitida = ctx.policy.window_allowed(janela.process, janela.title)
        if not permitida and ctx.config.server.hide_denied:
            ocultas_por_policy += 1
            continue
        if len(saida) >= limite:
            continue
        saida.append(
            {
                "ref": ctx.refs.window_ref(hwnd=janela.hwnd),
                "title": janela.title,
                "process": janela.process,
                "pid": janela.pid,
                "hwnd": janela.hwnd,
                "focused": janela.focused,
                "minimized": janela.minimized,
                "maximized": janela.maximized,
                "rect": list(janela.rect),
                "dpi": janela.dpi,
                "monitor": janela.monitor,
                "allowed": permitida,
                "elevated": janela.elevated,
            }
        )

    focada = next((j["ref"] for j in saida if j["focused"]), None)

    ctx.audit.log_call(
        tool="uia_list_windows",
        result="ok",
        params={"process_filter": process_filter, "max_results": limite},
        duration_ms=(time.perf_counter() - inicio) * 1000,
        read_only=ctx.policy.read_only,
    )

    return {
        "ok": True,
        "server_elevated": server_is_elevated(),
        "read_only": ctx.policy.read_only,
        "focused_window_ref": focada,
        "windows": saida,
        "stats": {
            "returned": len(saida),
            "total": total,
            "filtered_by_allowlist": ocultas_por_policy,
        },
    }


@mcp.tool()
@tool_errors
async def uia_list_windows(
    include_minimized: Annotated[bool, Field(description="Include minimized windows.")] = True,
    include_hidden: Annotated[
        bool, Field(description="Include windows without a title or not visible.")
    ] = False,
    process_filter: Annotated[
        str | None,
        Field(description="Case-insensitive substring of process name or window title."),
    ] = None,
    max_results: Annotated[int, Field(ge=1, le=200)] = 40,
) -> dict[str, Any]:
    """List top-level windows currently open on the desktop, with a stable window ref for each.
    Call this first to discover what applications are available before capturing a UI tree."""
    ctx = context()
    return await ctx.worker.run(
        lambda: list_windows_impl(
            ctx,
            include_minimized=include_minimized,
            include_hidden=include_hidden,
            process_filter=process_filter,
            max_results=max_results,
        )
    )
