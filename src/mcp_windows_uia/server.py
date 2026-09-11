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
from typing import Annotated, Any, NamedTuple

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .budget import decode_cursor, encode_cursor
from .context import ServerContext, context
from .errors import POLICY_DENIALS, Code, ToolError
from .uia.search import Criterios
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
        "only_interactive", "max_results", "max_chars", "offset", "include_refs",
        "root_ref", "state", "scope", "mode", "method",
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


# --------------------------------------------------------------------------- 8.4


def _trilha_ancestral(automation: Any, elem: Any, *, proprio: str, niveis: int = 5) -> str:
    """Trilha legivel dos ancestrais mais o proprio elemento, max 5 niveis (§8.4).

    Sobe pelo ControlViewWalker. Custa ate 5 RPCs de `Current*` por match, o que so
    vale porque max_results e pequeno — e evita uma segunda chamada do agente so
    para desambiguar dois botoes de mesmo nome (CA-24). Medido no Bloco de Notas:
    ~5 ms por elemento.

    Para no elemento raiz do desktop: prefixar todo path com o Pane da area de
    trabalho gastaria um segmento em todo match sem distinguir nada.
    """
    from .uia.core import control_type_name

    trilha: list[str] = [proprio]
    atual = elem
    for _ in range(niveis):
        try:
            pai = automation.control_walker.GetParentElement(atual)
        except Exception:
            break
        # Ponteiro COM pode ser NULL e falsy: testar antes de desreferenciar.
        if not pai:
            break
        try:
            if automation.iuia.CompareElements(pai, automation.root):
                break
            rotulo = control_type_name(pai.CurrentControlType)
            nome = pai.CurrentName or ""
        except Exception:
            break
        trilha.append(f"{rotulo}[{nome}]" if nome else rotulo)
        atual = pai
    return " > ".join(reversed(trilha))


class Busca(NamedTuple):
    """Resultado cru de uma busca: sem auditoria e sem erro quando nada casa."""

    achados: list[dict[str, Any]]
    janela: WindowInfo
    visitados: int
    exaustiva: bool


def buscar_elementos(
    ctx: ServerContext,
    *,
    window_ref: str,
    criterios: Criterios,
    only_interactive: bool,
    max_results: int,
) -> Busca:
    """Nucleo da busca da §8.4, sem auditoria e sem levantar ELEMENT_NOT_FOUND.

    Separado de `find_elements_impl` porque o `uia_wait_for` sonda isto dezenas de
    vezes dentro de UMA chamada do agente: auditar cada sondagem encheria o JSONL de
    `not_found` e faria o registro mentir sobre quantas chamadas houve.

    Duas estrategias, escolhidas pelo que os criterios permitem (§8.4):

    - ha criterio EXATO -> condicao nativa + FindAllBuildCache(Descendants). Um RPC,
      o provider filtra. E o que torna o poll do uia_wait_for (Task 11) barato o
      bastante para o CA-11.
    - so criterios de cliente (contains/starts_with/regex/text_contains) -> a
      condicao seria TrueCondition, e FindAll com TrueCondition enumera a janela
      inteira antes de devolver (24409 nos / ~18 s no WhatsApp Desktop, medido).
      Nesse ramo a busca vira varredura por nivel com teto de visita, que freia
      DURANTE a descida e para assim que junta max_results.
    """
    from .refs import ElementIdentity
    from .uia import search as busca
    from .uia.core import automation, control_type_name, pattern_availability_props
    from .uia.filters import passa_no_filtro
    from .uia.nodes import _cached, build_node
    from .uia.search import casa_no_cliente
    from .uia.tree import _patterns_disponiveis

    criterios.validar()

    janela = resolver_janela(ctx, window_ref)
    a = automation()
    versao = ctx.refs.tree_version(window_ref)
    props_de_pattern = pattern_availability_props()
    teto = busca.TETO_DE_BUSCA

    def _no_de(elem: Any) -> dict[str, Any] | None:
        """Serializa e aplica os filtros de cliente. None = nao e um match."""
        node = build_node(
            elem, ref="", depth=0,
            patterns=_patterns_disponiveis(elem, props_de_pattern),
        )
        if only_interactive and not passa_no_filtro(node, "interactive"):
            return None
        if not casa_no_cliente(node, criterios):
            return None
        return node

    condicao, restringe = a.condicao_de_criterios(criterios)
    pares: list[tuple[Any, dict[str, Any]]] = []

    if restringe:
        bruto = a.buscar_plano(janela.hwnd, condicao, teto=teto)
        parou_cedo = False
        for i, elem in enumerate(bruto.elementos):
            node = _no_de(elem)
            if node is None:
                continue
            pares.append((elem, node))
            if len(pares) >= max_results:
                parou_cedo = i + 1 < len(bruto.elementos)
                break
        exaustiva = bruto.exhaustive and not parou_cedo
    else:
        # Mesmo truque de tree.capturar_janela: `percorrer` chama o predicado uma vez
        # por no e faz append em `emitidos` na mesma ordem quando ele diz True, entao
        # aceitos[i] corresponde a bruto.elementos[i] — sem mapa por id() de ponteiro.
        aceitos: list[dict[str, Any]] = []

        def _predicado(elem: Any) -> bool:
            node = _no_de(elem)
            if node is None:
                return False
            aceitos.append(node)
            return True

        bruto = a.varrer_descendentes(
            janela.hwnd, aceita=_predicado, max_resultados=max_results, teto=teto
        )
        pares = list(zip(bruto.elementos, aceitos, strict=True))
        exaustiva = bruto.exhaustive

    achados: list[dict[str, Any]] = []
    for elem, node in pares:
        node["ref"] = ctx.refs.put(
            elem,
            runtime_id=a.runtime_id_of(elem),
            hwnd=janela.hwnd,
            window_ref=window_ref,
            identity=ElementIdentity(
                automation_id=_cached(elem, "CachedAutomationId", "") or "",
                control_type=control_type_name(_cached(elem, "CachedControlType", 0)),
                name=_cached(elem, "CachedName", "") or "",
                class_name=_cached(elem, "CachedClassName", "") or "",
            ),
            tree_version=versao,
        )
        node["path"] = _trilha_ancestral(a, elem, proprio=node["type"])
        # `d` e profundidade de captura de arvore; numa busca plana nao diz nada.
        node.pop("d", None)
        achados.append(node)

    return Busca(achados, janela, bruto.visitados, exaustiva)


def find_elements_impl(
    ctx: ServerContext,
    *,
    window_ref: str,
    criterios: Criterios,
    only_interactive: bool,
    max_results: int,
) -> dict[str, Any]:
    """Corpo sincrono de uia_find_elements: busca + auditoria + erro. Spec §8.4."""
    inicio = time.perf_counter()
    achados, janela, visitados, exaustiva = buscar_elementos(
        ctx,
        window_ref=window_ref,
        criterios=criterios,
        only_interactive=only_interactive,
        max_results=max_results,
    )
    duracao = (time.perf_counter() - inicio) * 1000

    if not achados:
        ctx.audit.log_call(
            tool="uia_find_elements", result="not_found",
            params={"window_ref": window_ref}, duration_ms=duracao,
            target={"process": janela.process, "pid": janela.pid},
            read_only=ctx.policy.read_only,
        )
        raise ToolError(
            Code.ELEMENT_NOT_FOUND,
            "No element matched the given criteria in this window.",
            window_ref=window_ref,
            visited=visitados,
            exhaustive=exaustiva,
        )

    ctx.audit.log_call(
        tool="uia_find_elements", result="ok",
        params={"window_ref": window_ref, "max_results": max_results,
                "only_interactive": only_interactive},
        target={"process": janela.process, "pid": janela.pid},
        duration_ms=duracao, read_only=ctx.policy.read_only,
    )

    return {
        "ok": True,
        "window_ref": window_ref,
        "matches": achados,
        "stats": {
            "returned": len(achados),
            # No ramo nativo isto e o numero de candidatos que o provider devolveu,
            # nao de nos que ele varreu — quem varreu foi ele, e nao conta.
            "visited": visitados,
            "exhaustive": exaustiva,
        },
    }


@mcp.tool()
@tool_errors
async def uia_find_elements(
    window_ref: Annotated[str, Field(description="Window ref from uia_list_windows.")],
    name: Annotated[str | None, Field(description="Matched according to `match`.")] = None,
    automation_id: Annotated[str | None, Field(description="Exact, case-sensitive.")] = None,
    control_type: Annotated[str | None, Field(description="Button, Edit, MenuItem, …")] = None,
    class_name: Annotated[str | None, Field(description="Exact.")] = None,
    text_contains: Annotated[str | None, Field(description="Substring of name or value.")] = None,
    match: Annotated[str, Field(description="exact | contains | starts_with | regex")] = "contains",
    only_interactive: Annotated[bool, Field()] = True,
    max_results: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Search a window for elements matching a criterion (name, automation id, control type,
    partial text) and return candidate refs. Cheaper and more precise than dumping the tree
    when you already know what you are looking for. An exact automation_id, class_name,
    control_type or name (match='exact') is resolved by the provider and covers the whole
    window; with only contains/starts_with/regex/text_contains the search is capped, and
    stats.exhaustive comes back false when it had to stop early."""
    ctx = context()
    criterios = Criterios(
        name=name, automation_id=automation_id, control_type=control_type,
        class_name=class_name, text_contains=text_contains, match=match,
    )
    return await ctx.worker.run(
        lambda: find_elements_impl(
            ctx, window_ref=window_ref, criterios=criterios,
            only_interactive=only_interactive, max_results=max_results,
        )
    )


# --------------------------------------------------------------------------- 8.5


class FonteDeValorCOM:
    """Adaptador que traduz cada fonte da §8.5 numa consulta ao elemento vivo.

    `Current*` de proposito, e nao `Cached*`: o que uia_get_value promete e o valor
    AGORA, nao o do instante em que a arvore foi capturada. E um elemento so, fora
    de laco — a proibicao de Current* da §5.2 e sobre travessia, onde um RPC por
    propriedade por no multiplica por centenas.
    """

    def __init__(self, elemento: Any, max_chars: int) -> None:
        self._e = elemento
        self._max = max_chars

    def tentar(self, nome: str) -> Any:
        from .uia.core import automation

        UIA = automation().UIA
        try:
            if nome == "ValuePattern":
                p = self._e.GetCurrentPattern(UIA.UIA_ValuePatternId)
                if not p:
                    return None
                return p.QueryInterface(UIA.IUIAutomationValuePattern).CurrentValue
            if nome == "TextPattern":
                p = self._e.GetCurrentPattern(UIA.UIA_TextPatternId)
                if not p:
                    return None
                tp = p.QueryInterface(UIA.IUIAutomationTextPattern)
                return tp.DocumentRange.GetText(self._max)
            if nome == "RangeValuePattern":
                p = self._e.GetCurrentPattern(UIA.UIA_RangeValuePatternId)
                if not p:
                    return None
                return p.QueryInterface(UIA.IUIAutomationRangeValuePattern).CurrentValue
            if nome == "TogglePattern":
                p = self._e.GetCurrentPattern(UIA.UIA_TogglePatternId)
                if not p:
                    return None
                return p.QueryInterface(UIA.IUIAutomationTogglePattern).CurrentToggleState
            if nome == "SelectionPattern":
                p = self._e.GetCurrentPattern(UIA.UIA_SelectionPatternId)
                if not p:
                    return None
                sp = p.QueryInterface(UIA.IUIAutomationSelectionPattern)
                sel = sp.GetCurrentSelection()
                # Nada selecionado nao e "valor vazio": e ausencia de fonte, entao
                # devolve None e a cadeia segue para LegacyIAccessible/Name.
                nomes = [sel.GetElement(i).CurrentName for i in range(sel.Length)]
                return ", ".join(n for n in nomes if n) or None
            if nome == "LegacyIAccessible":
                p = self._e.GetCurrentPattern(UIA.UIA_LegacyIAccessiblePatternId)
                if not p:
                    return None
                return p.QueryInterface(UIA.IUIAutomationLegacyIAccessiblePattern).CurrentValue
            if nome == "Name":
                return self._e.CurrentName or None
        except Exception:
            # Pattern anunciado mas nao implementado acontece na pratica: segue a
            # cadeia em vez de derrubar a leitura inteira.
            return None
        return None


def _snapshot_atualizado(a: Any, elemento: Any) -> Any:
    """Reconstroi o cache do elemento para que `st`/`pat`/`rect` sejam de agora.

    Sem isto a resposta misturaria dois instantes: `value` viria vivo pelos
    patterns e `st` viria do CacheRequest da captura da arvore — um checkbox
    marcado depois do uia_get_tree sairia com value="on" e st=["unchecked"].
    Custa um RPC, num elemento so.
    """
    try:
        return elemento.BuildUpdatedCache(a.build_cache_request(a.tree_props()))
    except Exception:
        # Provider que recusa o refresh ainda tem o cache da captura: melhor um
        # estado antigo do que nenhum.
        return elemento


def get_value_impl(ctx: ServerContext, *, ref: str, max_chars: int) -> dict[str, Any]:
    """Corpo sincrono de uia_get_value. Roda na thread do worker."""
    from .budget import truncate_name, truncate_text
    from .uia.core import automation, control_type_name, pattern_availability_props
    from .uia.nodes import _cached, _rect_of, states_of
    from .uia.tree import _patterns_disponiveis
    from .uia.values import ler_valor, tipo_de_valor

    inicio = time.perf_counter()
    entrada = ctx.refs.get(ref)
    janela = resolver_janela(ctx, entrada.window_ref)
    a = automation()
    elemento = _snapshot_atualizado(a, entrada.element)

    # Sem try/except: num elemento morto isto levanta UIA_E_ELEMENTNOTAVAILABLE e o
    # worker converte para STALE_REF, que e a resposta acionavel. Engolir a excecao
    # transformaria o campo de senha morto num elemento "sem senha" — o pior default.
    is_password = bool(elemento.CurrentIsPassword)

    valor, fonte = ler_valor(FonteDeValorCOM(elemento, max_chars), is_password=is_password)

    truncado = False
    if isinstance(valor, str) and fonte != "redacted":
        valor, truncado, _ = truncate_text(valor, max_chars=max_chars)

    patterns = _patterns_disponiveis(elemento, pattern_availability_props())
    st = states_of(elemento, patterns)

    ctx.audit.log_call(
        tool="uia_get_value",
        result="ok",
        params={"ref": ref, "max_chars": max_chars},
        target={"process": janela.process, "pid": janela.pid},
        element={"ref": ref, "type": control_type_name(_cached(elemento, "CachedControlType", 0))},
        # `valor` ja e o sentinela REDACTED quando is_password: a redacao acontece
        # antes de qualquer coisa poder escrever. is_password fecha a segunda porta,
        # que ignora log_values='full' (CA-15).
        value=valor if isinstance(valor, str) else str(valor),
        is_password=is_password,
        duration_ms=(time.perf_counter() - inicio) * 1000,
        read_only=ctx.policy.read_only,
    )

    resposta: dict[str, Any] = {
        "ok": True,
        "ref": ref,
        "rebound": False,
        "type": control_type_name(_cached(elemento, "CachedControlType", 0)),
        "name": truncate_name(_cached(elemento, "CachedName", "") or ""),
        "value": valor,
        "source": fonte,
        "value_type": tipo_de_valor(fonte),
        "truncated": truncado,
        "st": st,
        "pat": patterns,
    }

    aid = _cached(elemento, "CachedAutomationId", "") or ""
    if aid:
        resposta["aid"] = aid
    rect = _rect_of(elemento)
    if rect is not None:
        resposta["rect"] = rect
    return resposta


@mcp.tool()
@tool_errors
async def uia_get_value(
    ref: Annotated[str, Field(description="Element ref from uia_get_tree or uia_find_elements.")],
    max_chars: Annotated[int, Field(ge=1, le=40000)] = 6000,
) -> dict[str, Any]:
    """Read the current value and full state of a single element by ref: text of an edit box,
    toggle state of a checkbox, selection of a combo box, range value of a slider."""
    ctx = context()
    return await ctx.worker.run(lambda: get_value_impl(ctx, ref=ref, max_chars=max_chars))


# --------------------------------------------------------------------------- 8.3


class LeitorCOM:
    """Traduz um IUIAutomationElement na LeituraDeNo da §8.3.

    Concentra todo o COM da extracao num lugar so, para que o percurso de
    `uia/text.py` — que e onde mora a logica de ordem e de deduplicacao — continue
    testavel sem Windows.

    `register` e injetado e so existe quando include_refs=True: cunhar uma ref para
    cada bloco de texto encheria o RefStore de nos que o agente nunca vai citar.
    """

    def __init__(
        self,
        automation: Any,
        cache_request: Any,
        props_de_pattern: dict[str, int],
        *,
        register: Any = None,
    ) -> None:
        from .uia.tree import filhos_cacheados

        self._a = automation
        self._props = props_de_pattern
        self._filhos = filhos_cacheados(automation, cache_request)
        self._register = register

    def filhos(self, no: Any) -> Any:
        return self._filhos(no, 0)

    def ler(self, no: Any) -> Any:
        from .uia.nodes import REDACTED, states_of
        from .uia.text import LeituraDeNo
        from .uia.tree import _patterns_disponiveis

        patterns = _patterns_disponiveis(no, self._props)
        st = states_of(no, patterns)
        senha = "password" in st

        return LeituraDeNo(
            texto_de_pattern=None if senha else self._texto_de_pattern(no, patterns),
            texto_proprio=REDACTED if senha else self._texto_proprio(no, st, patterns),
            ref=self._register(no) if self._register is not None else "",
            offscreen="offscreen" in st,
        )

    def _texto_de_pattern(self, no: Any, patterns: list[str]) -> str | None:
        """DocumentRange.GetText do proprio no, que ja cobre a subarvore dele.

        O limite e o teto duro, NAO o max_chars do chamador: `offset` pagina sobre
        um documento que precisa ser o mesmo em toda chamada. Cortar a fonte pelo
        tamanho da pagina faria max_chars=5 paginar sobre um texto de 5 caracteres.
        E constante de modulo, e nao parametro, justamente para que nao haja por
        onde o max_chars entrar aqui.
        """
        from .budget import HARD_MAX_CHARS

        if "Text" not in patterns:
            return None
        limite = HARD_MAX_CHARS
        UIA = self._a.UIA
        try:
            p = no.GetCurrentPattern(UIA.UIA_TextPatternId)
            if not p:
                return None
            return p.QueryInterface(UIA.IUIAutomationTextPattern).DocumentRange.GetText(limite)
        except Exception:
            # Pattern anunciado mas nao implementado: desce pelos filhos.
            return None

    @staticmethod
    def _texto_proprio(no: Any, st: list[str], patterns: list[str]) -> str:
        from .uia.nodes import _cached, _value_of

        nome = (_cached(no, "CachedName", "") or "").strip()
        if nome:
            return nome
        valor = _value_of(no, st, patterns)
        # bool vem do SelectionItem: "True" no meio do texto seria ruido, nao conteudo.
        if valor is None or isinstance(valor, bool):
            return ""
        return str(valor).strip()


def get_text_impl(
    ctx: ServerContext,
    *,
    window_ref: str | None,
    root_ref: str | None,
    max_chars: int,
    offset: int,
    include_refs: bool,
) -> dict[str, Any]:
    """Corpo sincrono de uia_get_text. Roda na thread do worker."""
    from .budget import truncate_text
    from .refs import ElementIdentity
    from .uia.core import automation, control_type_name, pattern_availability_props
    from .uia.nodes import _cached
    from .uia.text import extrair, montar_texto

    inicio = time.perf_counter()

    if not window_ref and not root_ref:
        raise ToolError(
            Code.INVALID_ARGUMENT,
            "Either window_ref or root_ref is required.",
            hint="Call uia_list_windows to get a window_ref.",
        )

    entrada = ctx.refs.get(root_ref) if root_ref else None
    if entrada is not None:
        window_ref = entrada.window_ref

    assert window_ref is not None
    janela = resolver_janela(ctx, window_ref)
    a = automation()
    cr = a.build_cache_request(a.tree_props())

    # Sem try/except: elemento morto tem de virar STALE_REF, e nao uma extracao vazia
    # que o agente leria como "a janela nao tem texto".
    raiz = (
        entrada.element.BuildUpdatedCache(cr)
        if entrada is not None
        else a.element_from_handle_build_cache(janela.hwnd, cr)
    )

    versao = ctx.refs.tree_version(window_ref)
    alvo = window_ref

    def registrar(elem: Any) -> str:
        return ctx.refs.put(
            elem,
            runtime_id=a.runtime_id_of(elem),
            hwnd=janela.hwnd,
            window_ref=alvo,
            identity=ElementIdentity(
                automation_id=_cached(elem, "CachedAutomationId", "") or "",
                control_type=control_type_name(_cached(elem, "CachedControlType", 0)),
                name=_cached(elem, "CachedName", "") or "",
                class_name=_cached(elem, "CachedClassName", "") or "",
            ),
            tree_version=versao,
        )

    leitor = LeitorCOM(
        a, cr, pattern_availability_props(), register=registrar if include_refs else None
    )
    resultado = extrair(raiz, leitor)
    completo = montar_texto(resultado.blocos, include_refs=include_refs)
    fatia, cortado, proximo = truncate_text(completo, max_chars=max_chars, offset=offset)

    ctx.audit.log_call(
        tool="uia_get_text",
        result="ok",
        # O texto em si nunca vai para a auditoria: e conteudo da tela do usuario, e o
        # arquivo fica em disco por dias. So o tamanho.
        params={"window_ref": alvo, "root_ref": root_ref, "chars": len(fatia)},
        target={"process": janela.process, "pid": janela.pid},
        duration_ms=(time.perf_counter() - inicio) * 1000,
        read_only=ctx.policy.read_only,
    )

    resposta: dict[str, Any] = {
        "ok": True,
        "window_ref": alvo,
        "text": fatia,
        # Duas causas, um campo: a fatia cortou, ou o percurso parou no orcamento. As
        # duas querem dizer "ha mais texto"; mentir em qualquer uma faz o agente
        # concluir que leu a janela inteira.
        "truncated": cortado or resultado.truncado,
        "chars": len(fatia),
        "next_offset": proximo,
    }
    if resultado.truncado and not cortado:
        # Sem next_offset o agente ficaria sem saida: o corte foi na travessia, nao no
        # texto. Dizer onde apertar e o minimo acionavel.
        resposta["hint"] = (
            "Traversal hit its node budget before the tree ended, so some text is "
            "missing. Call uia_get_text with root_ref on a smaller subtree."
        )
    return resposta


@mcp.tool()
@tool_errors
async def uia_get_text(
    window_ref: Annotated[
        str | None, Field(description="Window ref from uia_list_windows.")
    ] = None,
    root_ref: Annotated[
        str | None, Field(description="Read only this subtree instead of the whole window.")
    ] = None,
    max_chars: Annotated[int, Field(ge=1, le=40000)] = 6000,
    offset: Annotated[int, Field(ge=0, description="Continue from character N.")] = 0,
    include_refs: Annotated[
        bool, Field(description="Prefix each block with [ref], to map text back to elements.")
    ] = False,
) -> dict[str, Any]:
    """Extract the readable text content of a window or subtree as linear text, in reading
    order. Use this to read a document, dialog message, list contents or status bar without
    dumping the full tree."""
    ctx = context()
    return await ctx.worker.run(
        lambda: get_text_impl(
            ctx,
            window_ref=window_ref,
            root_ref=root_ref,
            max_chars=max_chars,
            offset=offset,
            include_refs=include_refs,
        )
    )
