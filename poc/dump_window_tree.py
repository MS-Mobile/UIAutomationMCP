"""POC: a arvore de UI Automation de uma janela expoe o conteudo real?

Motivacao (nao faz parte do plano/TDD): o WhatsApp Desktop e WebView2 (Edge/Chromium
embutido). Chromium so publica a arvore de acessibilidade sob demanda, as vezes com
atraso. Antes de confiar o projeto inteiro na arvore UIA, precisamos medir se o
conteudo (conversas, mensagens, campo de digitar) aparece — ou se morre num Pane
vazio, como aconteceu com o screenshot do computer-use.

Uso:
    .venv/Scripts/python.exe poc/dump_window_tree.py [--process whatsapp] [--title ...]
                                                     [--passes 3] [--wait 2.0]
                                                     [--max-nodes 6000] [--view both]

Roda na thread principal em STA (igual a fixture `sta` dos testes e2e). Le `Current*`
direto — nao ligamos para perf de cache aqui.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

import comtypes

# Deixa o pacote importavel rodando o script direto de poc/.
sys.path.insert(0, "src")

from mcp_windows_uia.uia import core  # noqa: E402
from mcp_windows_uia.uia.dpi import init_dpi_awareness  # noqa: E402
from mcp_windows_uia.uia.windows import enumerate_windows  # noqa: E402

# Nos cujo Name preenchido normalmente carrega conteudo de verdade (nao chrome da UI).
CONTENT_TYPES = {"Text", "Document", "Edit", "ListItem", "TreeItem", "DataItem", "Hyperlink"}


@dataclass
class WalkResult:
    nodes: int = 0
    max_depth: int = 0
    type_counts: Counter = field(default_factory=Counter)
    pids: Counter = field(default_factory=Counter)
    content_samples: list[str] = field(default_factory=list)
    has_document: bool = False
    errors: int = 0
    error_kinds: Counter = field(default_factory=Counter)
    capped: bool = False
    lines: list[str] = field(default_factory=list)


def step(fn, elem, res: WalkResult):
    """Um passo de TreeWalker. Distingue 'acabou' de 'falhou'.

    comtypes levanta ValueError('NULL COM pointer access') quando o walker devolve
    NULL — que e o caso normal de folha / ultimo irmao, nao um erro. So COMError
    (e qualquer coisa com .hresult) conta como falha de verdade.
    """
    try:
        return fn(elem)
    except ValueError:
        return None  # fim normal da lista
    except Exception as exc:
        if getattr(exc, "hresult", None) is not None:
            res.errors += 1
            res.error_kinds[f"0x{exc.hresult & 0xFFFFFFFF:08X}"] += 1
        else:
            res.errors += 1
            res.error_kinds[type(exc).__name__] += 1
        return None


def _rect(elem) -> tuple[int, int, int, int]:
    try:
        r = elem.CurrentBoundingRectangle
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return (0, 0, 0, 0)


def _value(elem, A) -> str | None:
    try:
        p = elem.GetCurrentPattern(A.UIA.UIA_ValuePatternId)
        if not p:
            return None
        vp = p.QueryInterface(A.UIA.IUIAutomationValuePattern)
        v = vp.CurrentValue
        return v or None
    except Exception:
        return None


def _safe(fn, default=""):
    try:
        return fn()
    except Exception:
        return default


def walk(
    A,
    walker,
    elem,
    root_pid: int,
    *,
    max_nodes: int,
    max_depth: int,
    depth: int = 0,
    res: WalkResult | None = None,
) -> WalkResult:
    res = res or WalkResult()
    if res.nodes >= max_nodes:
        res.capped = True
        return res

    res.nodes += 1
    res.max_depth = max(res.max_depth, depth)

    ct_id = _safe(lambda: elem.CurrentControlType, 0)
    ct = core.control_type_name(ct_id) if ct_id else "?"
    name = _safe(lambda: elem.CurrentName, "") or ""
    aid = _safe(lambda: elem.CurrentAutomationId, "") or ""
    cls = _safe(lambda: elem.CurrentClassName, "") or ""
    pid = _safe(lambda: elem.CurrentProcessId, 0)
    val = _value(elem, A)
    left, top, right, bottom = _rect(elem)

    res.type_counts[ct] += 1
    res.pids[pid] += 1
    if ct == "Document":
        res.has_document = True

    disp_name = (name[:80] + "…") if len(name) > 80 else name
    disp_val = ""
    if val:
        v = val if len(val) <= 60 else val[:60] + "…"
        disp_val = f"  val={v!r}"
    cross = "  «cross-process»" if pid and pid != root_pid else ""
    extra = " ".join(x for x in (f"aid={aid!r}" if aid else "", f"cls={cls!r}" if cls else "") if x)
    res.lines.append(
        f"{'  ' * depth}{ct} {disp_name!r}{disp_val}"
        f"{('  ' + extra) if extra else ''}"
        f"  rect=({left},{top},{right},{bottom}){cross}"
    )

    if ct in CONTENT_TYPES and name.strip() and len(res.content_samples) < 40:
        res.content_samples.append(f"[{ct}] {name.strip()[:120]}")

    if depth >= max_depth:
        return res

    child = step(walker.GetFirstChildElement, elem, res)
    while child is not None and res.nodes < max_nodes:
        walk(
            A, walker, child, root_pid,
            max_nodes=max_nodes, max_depth=max_depth, depth=depth + 1, res=res,
        )
        child = step(walker.GetNextSiblingElement, child, res)
    return res


def find_window(process: str | None, title: str | None):
    for include_hidden in (False, True):
        for w in enumerate_windows(include_hidden=include_hidden):
            hay_proc = (w.process or "").lower()
            hay_title = (w.title or "").lower()
            if process and process.lower() not in hay_proc and process.lower() not in hay_title:
                continue
            if title and title.lower() not in hay_title:
                continue
            if process or title:
                return w, include_hidden
    return None, False


def verdict(passes: list[WalkResult], root_pid: int) -> None:
    print("\n" + "=" * 70)
    print("VEREDITO")
    print("=" * 70)
    for i, r in enumerate(passes, 1):
        content_nodes = sum(r.type_counts[t] for t in CONTENT_TYPES)
        named_content = len(r.content_samples)
        cross = {p: c for p, c in r.pids.items() if p and p != root_pid}
        print(
            f"  passe {i}: {r.nodes} nos{' (CAP ATINGIDO)' if r.capped else ''} | "
            f"prof.max {r.max_depth} | "
            f"{content_nodes} nos de conteudo ({named_content}+ com Name) | "
            f"Document={'sim' if r.has_document else 'nao'} | "
            f"cross-process pids={cross or 'nenhum'} | "
            f"erros={r.errors}{' ' + str(dict(r.error_kinds)) if r.errors else ''}"
        )

    first, last = passes[0], passes[-1]
    grew = last.nodes > first.nodes * 1.2
    has_content = len(last.content_samples) >= 5

    if last.capped:
        print(
            "\n  NOTA: o cap de nos foi atingido — a arvore e maior que isso. "
            "A comparacao entre passes fica saturada; use --max-nodes maior para medir o total."
        )

    print()
    if has_content and not grew:
        print("  => ARVORE TRAZ O CONTEUDO desde a 1a varredura. Risco eliminado; seguir o plano.")
    elif has_content and grew:
        print("  => ARVORE POPULOU APOS ESPERA (Chromium ligou a11y tarde).")
        print("     Desenho muda: toda captura precisa de 'prime + re-poll'. Documentar na spec §7.3.")
    else:
        print("  => ARVORE SEM CONTEUDO mesmo apos espera.")
        print("     Desenho muda de verdade: investigar WM_GETOBJECT / flag de a11y no WebView2,")
        print("     ou aceitar fallback a screenshot+OCR para apps WebView2/Electron.")

    if last.content_samples:
        print("\n  amostras de conteudo (ultimo passe):")
        for s in last.content_samples[:20]:
            print(f"    {s}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--process", default="whatsapp", help="substring do nome do processo ou titulo")
    ap.add_argument("--title", default=None, help="substring do titulo da janela")
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--wait", type=float, default=2.0, help="segundos de espera entre passes")
    ap.add_argument("--max-nodes", type=int, default=6000)
    ap.add_argument("--max-depth", type=int, default=45)
    ap.add_argument("--view", choices=("raw", "control", "both"), default="both")
    ap.add_argument("--dump", action="store_true", help="imprime a arvore inteira, nao so o veredito")
    args = ap.parse_args()

    # O console do Windows e cp1252; a arvore vem cheia de acentos e emoji.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"DPI awareness: {init_dpi_awareness()}")
    comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)

    win, hidden = find_window(args.process, args.title)
    if win is None:
        print(
            f"\nNenhuma janela casou process/title ~ {args.process!r}/{args.title!r}.\n"
            "Abra o WhatsApp Desktop (nao minimizado) e rode de novo.\n"
            "Janelas visiveis agora:"
        )
        for w in enumerate_windows():
            print(f"  {w.process:<28} {w.title!r}")
        return 1

    print(
        f"\nJanela: {win.title!r}  process={win.process}  pid={win.pid}  hwnd={win.hwnd}\n"
        f"  minimizada={win.minimized}  {'(achada so com include_hidden)' if hidden else ''}"
    )
    if win.minimized:
        print("  AVISO: janela minimizada — WebView2 pode suspender render/a11y. Restaure e rode de novo.")

    A = core.automation()
    root = A.element_from_handle(win.hwnd)

    views = ("raw", "control") if args.view == "both" else (args.view,)
    for view in views:
        walker = A.raw_walker if view == "raw" else A.control_walker
        print("\n" + "#" * 70)
        print(f"# VIEW: {view}")
        print("#" * 70)
        results: list[WalkResult] = []
        for i in range(1, args.passes + 1):
            r = walk(
                A, walker, root, win.pid,
                max_nodes=args.max_nodes, max_depth=args.max_depth,
            )
            results.append(r)
            print(f"  passe {i}: {r.nodes} nos, prof.max {r.max_depth}, "
                  f"{len(r.content_samples)} amostras de conteudo")
            if i < args.passes:
                time.sleep(args.wait)
                root = A.element_from_handle(win.hwnd)  # re-obtem apos espera

        if args.dump:
            print(f"\n--- arvore ({view}, ultimo passe) ---")
            for line in results[-1].lines:
                print(line)

        verdict(results, win.pid)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
