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

from .budget import decode_cursor, encode_cursor
from .context import ServerContext, context
from .errors import POLICY_DENIALS, Code, ToolError
from .uia.windows import WindowInfo, enumerate_windows, server_is_elevated, window_is_alive

_log = logging.getLogger(__name__)

mcp = MCPServer("windows-uia")


# Parametros que podem ir para a auditoria de uma falha. Allowlist, nao denylist:
# o que nao esta aqui pode carregar conteudo do usuario (`value` de uia_set_value,
# `keys` de uia_send_keys) e a regra da §10.3/CA-15 e que valor nunca vaza por
# acidente. Parametro novo so aparece no log depois de alguem decidir que pode.
PARAMS_AUDITAVEIS = frozenset(
    {
        "window_ref", "ref", "filter", "max_depth", "max_nodes",
        "max_children_per_node", "verbose", "cursor", "timeout_ms",
        "only_interactive", "max_results", "state", "scope", "mode", "method",
    }
)


# Campos do `target` da §10.3, colhidos do ToolError. `check_window` anexa `process` e
# `title` ao erro; sem isto a linha "denied" diria que algo foi barrado, mas nao o que.
CAMPOS_DE_TARGET = ("window_ref", "hwnd", "pid", "process", "title")


def _auditar_falha(tool: str, exc: ToolError, kwargs: dict[str, Any], inicio: float) -> None:
    """Grava a linha de falha. Nunca levanta: auditoria nao pode derrubar a chamada."""
    try:
        ctx = context()
    except RuntimeError:
        # Falhou antes do servidor subir (so acontece fora do processo real).
        return
    try:
        ctx.audit.log_call(
            tool=tool,
            result="denied" if exc.code in POLICY_DENIALS else "error",
            code=exc.code.value,
            target={k: exc.details[k] for k in CAMPOS_DE_TARGET if k in exc.details} or None,
            params={k: v for k, v in kwargs.items() if k in PARAMS_AUDITAVEIS} or None,
            duration_ms=(time.perf_counter() - inicio) * 1000,
            read_only=ctx.policy.read_only,
        )
    except Exception:  # pragma: no cover - rede de seguranca
        _log.exception("falha ao auditar erro de %s", tool)


def tool_errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Converte qualquer falha no envelope de erro da §9. Nunca deixa vazar traceback.

    Audita SO a falha: o caminho de sucesso ja e auditado dentro de cada `*_impl`,
    que e onde se sabe o processo alvo e o metodo usado. Auditar aqui tambem
    duplicaria a linha.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        inicio = time.perf_counter()
        try:
            return await fn(*args, **kwargs)
        except ToolError as exc:
            _auditar_falha(fn.__name__, exc, kwargs, inicio)
            return exc.to_dict()
        except Exception as exc:  # pragma: no cover - rede de seguranca
            _log.exception("unhandled error in %s", fn.__name__)
            erro = ToolError(
                Code.UIA_COM_ERROR, f"Unhandled server error in {fn.__name__}: {exc!r}"
            )
            _auditar_falha(fn.__name__, erro, kwargs, inicio)
            return erro.to_dict()

    return wrapper


def resolver_janela(ctx: ServerContext, window_ref: str) -> WindowInfo:
    """window_ref -> WindowInfo viva, com allowlist ja validada.

    Erros distintos de proposito: ref desconhecida, janela morta e janela negada
    exigem acoes diferentes do agente.
    """
    if not ctx.refs.known_window(window_ref):
        raise ToolError(
            Code.WINDOW_NOT_FOUND,
            f"Window ref {window_ref!r} is not known to this server.",
            window_ref=window_ref,
        )

    hwnd = ctx.refs.hwnd_for(window_ref)
    if not window_is_alive(hwnd):
        ctx.refs.invalidate_window(window_ref)
        raise ToolError(
            Code.WINDOW_CLOSED,
            f"Window {window_ref} was closed.",
            window_ref=window_ref,
        )

    janela = next((w for w in enumerate_windows(include_hidden=True) if w.hwnd == hwnd), None)
    if janela is None:
        raise ToolError(
            Code.WINDOW_CLOSED,
            f"Window {window_ref} is no longer enumerable.",
            window_ref=window_ref,
        )

    ctx.policy.check_window(janela.process, janela.title, window_ref=window_ref)
    return janela


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


# --------------------------------------------------------------------------- 8.2


def _retomar_do_cursor(ctx: ServerContext, window_ref: str, cursor: str) -> tuple[int, int]:
    """Valida um cursor de continuacao e devolve (tree_version, pular). Spec §5.2.

    O cursor carrega a janela e a versao da arvore justamente para nao continuar
    uma captura sobre outra coisa: cursor de outra janela, ou de uma versao ja
    vencida por um uia_get_tree posterior, descreveria posicoes de uma travessia
    que nao existe mais. Em qualquer um desses casos a saida e a mesma — repetir a
    chamada SEM cursor —, entao o hint diz isso explicitamente.
    """
    # decode_cursor ja levanta INVALID_ARGUMENT para corrompido e para expirado.
    janela_do_cursor, versao_do_cursor, pular = decode_cursor(cursor)

    versao_atual = ctx.refs.tree_version(window_ref)
    if janela_do_cursor != window_ref:
        raise ToolError(
            Code.INVALID_ARGUMENT,
            f"This cursor belongs to window {janela_do_cursor!r}, not {window_ref!r}.",
            hint="Call uia_get_tree again for this window without a cursor.",
            window_ref=window_ref,
        )
    if versao_do_cursor != versao_atual:
        raise ToolError(
            Code.INVALID_ARGUMENT,
            f"This cursor is for tree_version {versao_do_cursor}, "
            f"but the window is now at {versao_atual}.",
            hint="The tree was re-captured meanwhile. Call uia_get_tree again without a cursor.",
            window_ref=window_ref,
            tree_version=versao_atual,
        )
    if pular < 0:
        raise ToolError(
            Code.INVALID_ARGUMENT,
            "This cursor carries a negative position.",
            hint="Call uia_get_tree again without a cursor.",
            window_ref=window_ref,
        )
    return versao_atual, pular


def get_tree_impl(
    ctx: ServerContext,
    *,
    window_ref: str,
    filtro: str,
    max_depth: int | None,
    max_nodes: int,
    max_children_per_node: int,
    cursor: str | None,
    verbose: bool,
) -> dict[str, Any]:
    """Corpo sincrono de uia_get_tree. Roda na thread do worker."""
    from .refs import ElementIdentity
    from .uia.core import automation, control_type_name
    from .uia.nodes import _cached
    from .uia.tree import capturar_janela

    inicio = time.perf_counter()
    janela = resolver_janela(ctx, window_ref)

    # Com cursor a versao NAO incrementa: ela e o que valida a continuacao. Se cada
    # pagina bumpasse, a pagina 2 nunca casaria com o cursor emitido pela pagina 1.
    if cursor is None:
        versao = ctx.refs.bump_tree_version(window_ref)
        pular = 0
    else:
        versao, pular = _retomar_do_cursor(ctx, window_ref, cursor)

    def registrar(elem: Any, indice: int) -> str:
        """Registra o elemento no RefStore e devolve a ref estavel.

        Chamado tambem para os nos PULADOS de uma pagina de continuacao. E de
        proposito: RefStore.put deduplica por (hwnd, runtime_id) e devolve a ref ja
        existente, entao as refs ficam estaveis entre paginas e o store nao cresce.
        """
        return ctx.refs.put(
            elem,
            runtime_id=automation().runtime_id_of(elem),
            hwnd=janela.hwnd,
            window_ref=window_ref,
            identity=ElementIdentity(
                automation_id=_cached(elem, "CachedAutomationId", "") or "",
                control_type=control_type_name(_cached(elem, "CachedControlType", 0)),
                name=_cached(elem, "CachedName", "") or "",
                class_name=_cached(elem, "CachedClassName", "") or "",
                index_path=(indice,),
            ),
            tree_version=versao,
        )

    capturado = capturar_janela(
        janela.hwnd,
        filtro=filtro,
        max_nodes=max_nodes,
        max_depth=max_depth,
        max_children_per_node=max_children_per_node,
        verbose=verbose,
        pular=pular,
        atribuir_ref=registrar,
    )

    stats = capturado["stats"]
    # next_cursor so existe quando ha continuacao real.
    stats["next_cursor"] = (
        encode_cursor(
            window_ref, tree_version=versao, position=pular + len(capturado["nodes"])
        )
        if stats["truncated"]
        else None
    )

    ctx.audit.log_call(
        tool="uia_get_tree",
        result="ok",
        params={"window_ref": window_ref, "filter": filtro, "max_nodes": max_nodes},
        target={"process": janela.process, "pid": janela.pid},
        duration_ms=(time.perf_counter() - inicio) * 1000,
        read_only=ctx.policy.read_only,
    )

    return {
        "ok": True,
        "window_ref": window_ref,
        "window_title": janela.title,
        "tree_version": versao,
        "nodes": capturado["nodes"],
        "stats": stats,
    }


@mcp.tool()
@tool_errors
async def uia_get_tree(
    window_ref: Annotated[str, Field(description="Window ref from uia_list_windows.")],
    filter: Annotated[
        str,
        Field(description="One of: interactive, content, all, landmarks."),
    ] = "interactive",
    max_depth: Annotated[
        int | None,
        Field(ge=1, le=40, description="Omit to let the server deepen automatically."),
    ] = None,
    max_nodes: Annotated[int, Field(ge=1, le=1500)] = 200,
    max_children_per_node: Annotated[int, Field(ge=1, le=500)] = 30,
    cursor: Annotated[
        str | None,
        Field(description="Opaque cursor from a previous truncated call."),
    ] = None,
    verbose: Annotated[bool, Field(description="Include HelpText/FullDescription.")] = False,
) -> dict[str, Any]:
    """Capture the UI Automation tree of a window as a flat, pre-order list of nodes with
    stable refs. Defaults to interactive elements only. Omit max_depth so the server can
    go deeper automatically in deeply nested apps such as Electron or WebView2. When
    stats.truncated is true, pass stats.next_cursor back to continue where it stopped."""
    ctx = context()
    return await ctx.worker.run(
        lambda: get_tree_impl(
            ctx,
            window_ref=window_ref,
            filtro=filter,
            max_depth=max_depth,
            max_nodes=max_nodes,
            max_children_per_node=max_children_per_node,
            cursor=cursor,
            verbose=verbose,
        )
    )
