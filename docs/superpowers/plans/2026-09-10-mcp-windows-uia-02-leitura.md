# mcp-windows-uia — Plano 2/3: Leitura da árvore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar a camada de **leitura** do servidor: capturar a árvore UIA de uma janela dentro do orçamento, re-resolver refs quando a UI muda debaixo do agente, e as cinco tools que não mutam estado — `uia_get_tree`, `uia_find_elements`, `uia_get_value`, `uia_get_text`, `uia_wait_for`.

**Architecture:** A captura é percurso em **largura por nível**, uma chamada `FindAllBuildCache(TreeScope_Children, …)` por nó-pai, lendo só propriedades `Cached*`, parando no orçamento **durante** a travessia (spec §5.2). O `CacheRequest` é `TreeScope_Element` — qualquer coisa que inclua `Descendants` estoura a transação COM e vira `E_FAIL` (já corrigido em `uia/core.py`, commit 575ba48). O rebind (§7.2) é escrito como função **`ElementIdentity → elemento`**, deliberadamente independente do `RefStore`, para que a camada futura de aprendizado de seletores por app seja só persistência + chave em cima dela.

**Tech Stack:** Python 3.14 x64 (piso 3.11), `comtypes`, `mcp` 2.x (`MCPServer`), `pytest`, `pytest-asyncio`. Tudo que toca COM roda dentro de `ctx.worker.run(...)`.

**Spec de referência:** `spec-mcp-windows-uia.md` §5.1, §5.2, §6.1, §6.2, §7.1–7.3, §8.2–8.6, §9.

**Critérios de aceitação cobertos:** CA-02, CA-06, CA-07, CA-08, CA-09, CA-11, CA-12, CA-21, CA-24.

---

## Contexto que o executor precisa saber antes de começar

1. **`stdout` é sagrado.** É o canal JSON-RPC. Nenhum `print`, log ou warning vai para lá. Logs → `stderr`. Violar quebra o CA-22 e o servidor inteiro dentro do Claude Desktop.
2. **COM tem afinidade de apartamento.** Nunca chame nada de `uia/` fora de `ctx.worker.run(...)`. Um `IUIAutomationElement` obtido na thread A e usado na thread B dá `RPC_E_WRONG_THREAD` ou corrupção silenciosa.
3. **`Cached*`, nunca `Current*`, dentro do percurso.** Cada leitura `Current*` é um RPC cross-process. Numa árvore de centenas de nós é a diferença entre 159 ms e dezenas de segundos. `Current*` só é aceitável em elemento único, fora de laço (ex.: `uia_get_value`).
4. **comtypes devolve ponteiro NULL, não `None`.** Um `TreeWalker` ou `FindFirst` sem resultado devolve um ponteiro falsy que **levanta `ValueError` ou `COMError E_POINTER` ao ser desreferenciado**. Sempre teste com `if not elem:` antes de tocar. Isso já mordeu uma vez neste projeto.
5. **O filtro roda no cliente, não na condição do find.** O percurso precisa descer **através** de containers que não passam no filtro para alcançar os nós que passam. Condição nativa restritiva no percurso poda o caminho e o conteúdo some. Em `uia_find_elements` a condição nativa é legítima — lá a busca é plana.
6. **Ordem de guarda em toda tool:** allowlist → resolve(ref) → validação específica. Erro específico em cada etapa, nunca genérico.

---

## Estrutura de arquivos

| Arquivo | Responsabilidade | Novo? |
|---|---|---|
| `src/mcp_windows_uia/uia/nodes.py` | Serializar um elemento cacheado no `Node` da §5.1 | criar |
| `src/mcp_windows_uia/uia/filters.py` | Predicados da §6.2 sobre `Node` já serializado — puro, sem COM | criar |
| `src/mcp_windows_uia/uia/tree.py` | Percurso em largura com orçamento e aprofundamento adaptativo | criar |
| `src/mcp_windows_uia/uia/search.py` | Busca plana com condição nativa (§8.4) | criar |
| `src/mcp_windows_uia/uia/text.py` | Extração de texto linear com dedup de ancestral (§8.3) | criar |
| `src/mcp_windows_uia/uia/values.py` | Cadeia de leitura de valor de um elemento (§8.5) | criar |
| `src/mcp_windows_uia/uia/waits.py` | Polling com backoff (§7.3) | criar |
| `src/mcp_windows_uia/rebind.py` | `ElementIdentity → elemento`, independente do RefStore (§7.2) | criar |
| `src/mcp_windows_uia/refs.py` | `resolve(ref)` passa a usar `rebind` | modificar |
| `src/mcp_windows_uia/server.py` | As cinco tools novas | modificar |

Separar `nodes.py` de `filters.py` de `tree.py` é deliberado: o filtro é lógica pura testável sem Windows, e é onde mais vai haver ajuste fino. Enterrá-lo dentro do percurso tornaria cada ajuste um teste e2e lento.

---

### Task 1: `uia/nodes.py` — serialização do nó da §5.1

**Files:**
- Create: `src/mcp_windows_uia/uia/nodes.py`
- Test: `tests/test_uia_nodes.py`

O `Node` é um `dict` de chaves curtas (§5.1), não uma dataclass: ele é serializado direto para JSON e as chaves curtas existem para economizar token. A função recebe um elemento **já cacheado** e não faz nenhuma chamada COM adicional.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_uia_nodes.py`:

```python
from __future__ import annotations

from mcp_windows_uia.uia.nodes import STATE_KEYS, build_node, states_of


class FakeCached:
    """Stand-in de IUIAutomationElement com propriedades Cached*.

    build_node so le Cached*, entao qualquer objeto com esses atributos serve.
    Isso mantem a serializacao testavel sem Windows.
    """

    def __init__(self, **kw):
        padrao = {
            "CachedName": "",
            "CachedAutomationId": "",
            "CachedClassName": "",
            "CachedControlType": 50000,  # Button
            "CachedIsEnabled": 1,
            "CachedIsOffscreen": 0,
            "CachedIsKeyboardFocusable": 1,
            "CachedHasKeyboardFocus": 0,
            "CachedIsPassword": 0,
            "CachedProcessId": 100,
            "CachedBoundingRectangle": (0, 0, 10, 10),
            "CachedToggleToggleState": None,
            "CachedExpandCollapseExpandCollapseState": None,
            "CachedSelectionItemIsSelected": None,
            "CachedValueValue": None,
            "CachedValueIsReadOnly": None,
            "CachedRangeValueValue": None,
        }
        padrao.update(kw)
        for k, v in padrao.items():
            setattr(self, k, v)


def test_node_tem_as_chaves_obrigatorias_da_spec() -> None:
    n = build_node(FakeCached(CachedName="Salvar"), ref="w1-e5", depth=2, patterns=["Invoke"])
    for chave in ("ref", "d", "type", "name", "st", "pat"):
        assert chave in n
    assert n["ref"] == "w1-e5"
    assert n["d"] == 2
    assert n["type"] == "Button"
    assert n["name"] == "Salvar"
    assert n["pat"] == ["Invoke"]


def test_chaves_opcionais_omitidas_quando_vazias() -> None:
    """Economia de token: aid/cls/val nao aparecem se nao houver conteudo."""
    n = build_node(FakeCached(), ref="w1-e0", depth=0, patterns=[])
    assert "aid" not in n
    assert "cls" not in n
    assert "val" not in n


def test_chaves_opcionais_presentes_quando_ha_conteudo() -> None:
    n = build_node(
        FakeCached(CachedAutomationId="SaveBtn", CachedClassName="Button"),
        ref="w1-e1", depth=1, patterns=[],
    )
    assert n["aid"] == "SaveBtn"
    assert n["cls"] == "Button"


def test_name_truncado_em_120_chars() -> None:
    n = build_node(FakeCached(CachedName="a" * 300), ref="w1-e1", depth=0, patterns=[])
    assert len(n["name"]) == 120
    assert n["name"].endswith("…")


def test_estados_refletem_as_propriedades() -> None:
    st = states_of(FakeCached(CachedIsEnabled=1, CachedIsKeyboardFocusable=1,
                              CachedHasKeyboardFocus=1))
    assert "enabled" in st and "focusable" in st and "focused" in st
    assert "disabled" not in st


def test_disabled_e_offscreen_sao_explicitos() -> None:
    st = states_of(FakeCached(CachedIsEnabled=0, CachedIsOffscreen=1))
    assert "disabled" in st and "offscreen" in st
    assert "enabled" not in st


def test_toggle_state_vira_checked_unchecked_indeterminate() -> None:
    assert "unchecked" in states_of(FakeCached(CachedToggleToggleState=0))
    assert "checked" in states_of(FakeCached(CachedToggleToggleState=1))
    assert "indeterminate" in states_of(FakeCached(CachedToggleToggleState=2))


def test_valor_de_toggle_vai_para_val() -> None:
    n = build_node(FakeCached(CachedToggleToggleState=1), ref="w1-e1", depth=0,
                   patterns=["Toggle"])
    assert n["val"] == "on"


def test_campo_de_senha_nunca_expoe_o_valor() -> None:
    """Spec §5.1: regra de redacao. CA-15 depende disto."""
    n = build_node(
        FakeCached(CachedIsPassword=1, CachedValueValue="s3nh4-secreta"),
        ref="w1-e9", depth=3, patterns=["Value"],
    )
    assert n["val"] == "«redacted:password»"
    assert "s3nh4-secreta" not in repr(n)
    assert "password" in n["st"]


def test_rect_omitido_quando_area_zero() -> None:
    n = build_node(FakeCached(CachedBoundingRectangle=(0, 0, 0, 0)), ref="w1-e1",
                   depth=0, patterns=[])
    assert "rect" not in n


def test_todo_estado_declarado_e_conhecido_pela_spec() -> None:
    """Guarda contra digitar um estado que a spec §5.1 nao lista."""
    da_spec = {
        "enabled", "disabled", "focused", "focusable", "offscreen", "selected",
        "expanded", "collapsed", "checked", "unchecked", "indeterminate",
        "readonly", "required", "password", "multiline",
    }
    assert set(STATE_KEYS) <= da_spec
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_nodes.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.uia.nodes'`

- [ ] **Step 3: Implementar `uia/nodes.py`**

```python
"""Serializacao do Node da spec §5.1.

Le exclusivamente propriedades Cached* — o elemento chega ja materializado pelo
CacheRequest. Nenhuma chamada COM acontece aqui.

Chaves curtas de proposito: a arvore vai inteira para o contexto de um LLM.
"""

from __future__ import annotations

from typing import Any

from ..budget import truncate_name
from .core import control_type_name

REDACTED = "«redacted:password»"

STATE_KEYS = (
    "enabled", "disabled", "focused", "focusable", "offscreen", "selected",
    "expanded", "collapsed", "checked", "unchecked", "indeterminate",
    "readonly", "password", "multiline",
)

_TOGGLE = {0: "unchecked", 1: "checked", 2: "indeterminate"}
_TOGGLE_VAL = {0: "off", 1: "on", 2: "indeterminate"}
_EXPAND = {0: "collapsed", 1: "expanded", 2: "collapsed", 3: "expanded"}


def _cached(elem: Any, nome: str, padrao: Any = None) -> Any:
    """Le uma propriedade Cached*, devolvendo padrao se ausente do CacheRequest."""
    try:
        return getattr(elem, nome, padrao)
    except Exception:
        return padrao


def states_of(elem: Any) -> list[str]:
    """Lista de estados presentes. Ausencia significa falso (spec §5.1)."""
    st: list[str] = []

    st.append("enabled" if _cached(elem, "CachedIsEnabled", 1) else "disabled")
    if _cached(elem, "CachedIsOffscreen", 0):
        st.append("offscreen")
    if _cached(elem, "CachedIsKeyboardFocusable", 0):
        st.append("focusable")
    if _cached(elem, "CachedHasKeyboardFocus", 0):
        st.append("focused")
    if _cached(elem, "CachedIsPassword", 0):
        st.append("password")

    toggle = _cached(elem, "CachedToggleToggleState")
    if toggle in _TOGGLE:
        st.append(_TOGGLE[toggle])

    expand = _cached(elem, "CachedExpandCollapseExpandCollapseState")
    if expand in _EXPAND:
        st.append(_EXPAND[expand])

    if _cached(elem, "CachedSelectionItemIsSelected"):
        st.append("selected")
    if _cached(elem, "CachedValueIsReadOnly"):
        st.append("readonly")

    return st


def _value_of(elem: Any, st: list[str]) -> Any:
    """Valor atual, na ordem da spec §5.1. Senha nunca vaza."""
    if "password" in st:
        return REDACTED

    valor = _cached(elem, "CachedValueValue")
    if valor not in (None, ""):
        return valor

    faixa = _cached(elem, "CachedRangeValueValue")
    if faixa is not None:
        return faixa

    toggle = _cached(elem, "CachedToggleToggleState")
    if toggle in _TOGGLE_VAL:
        return _TOGGLE_VAL[toggle]

    selecionado = _cached(elem, "CachedSelectionItemIsSelected")
    if selecionado is not None:
        return bool(selecionado)

    return None


def _rect_of(elem: Any) -> list[int] | None:
    """[left, top, right, bottom] em px fisicos. None se area zero (§4.1)."""
    bruto = _cached(elem, "CachedBoundingRectangle")
    if bruto is None:
        return None
    try:
        if hasattr(bruto, "left"):
            left, top, right, bottom = bruto.left, bruto.top, bruto.right, bruto.bottom
        else:
            left, top, right, bottom = tuple(bruto)
    except Exception:
        return None
    if right - left <= 0 or bottom - top <= 0:
        return None
    return [int(left), int(top), int(right), int(bottom)]


def build_node(
    elem: Any,
    *,
    ref: str,
    depth: int,
    patterns: list[str],
    verbose: bool = False,
) -> dict[str, Any]:
    """Monta o dict do Node. Chaves opcionais sao omitidas quando vazias."""
    st = states_of(elem)
    node: dict[str, Any] = {
        "ref": ref,
        "d": depth,
        "type": control_type_name(_cached(elem, "CachedControlType", 0)),
        "name": truncate_name(_cached(elem, "CachedName", "") or ""),
        "st": st,
        "pat": patterns,
    }

    aid = _cached(elem, "CachedAutomationId", "") or ""
    if aid:
        node["aid"] = aid

    cls = _cached(elem, "CachedClassName", "") or ""
    if cls:
        node["cls"] = cls

    valor = _value_of(elem, st)
    if valor is not None:
        node["val"] = valor

    rect = _rect_of(elem)
    if rect is not None:
        node["rect"] = rect

    if verbose:
        ajuda = _cached(elem, "CachedHelpText", "") or ""
        if ajuda:
            node["help"] = truncate_name(ajuda)

    return node
```

- [ ] **Step 4: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_nodes.py -q`
Expected: PASS — 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_windows_uia/uia/nodes.py tests/test_uia_nodes.py
git commit -m "feat(uia): serializacao do Node da spec 5.1 a partir de propriedades Cached"
```

---

### Task 2: `uia/filters.py` — predicados da §6.2

**Files:**
- Create: `src/mcp_windows_uia/uia/filters.py`
- Test: `tests/test_uia_filters.py`

Lógica pura sobre `Node` já serializado. Sem COM, sem Windows.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_uia_filters.py`:

```python
from __future__ import annotations

import pytest

from mcp_windows_uia.errors import Code, ToolError
from mcp_windows_uia.uia.filters import FILTROS, passa_no_filtro

ACIONAVEIS = ["Invoke", "Toggle", "SelectionItem", "ExpandCollapse", "RangeValue", "Scroll"]


def no(**kw):
    base = {"ref": "w1-e1", "d": 1, "type": "Button", "name": "", "st": ["enabled"], "pat": []}
    base.update(kw)
    return base


def test_interactive_aceita_no_com_pattern_acionavel() -> None:
    assert passa_no_filtro(no(pat=["Invoke"]), "interactive") is True


@pytest.mark.parametrize("pattern", ACIONAVEIS)
def test_interactive_aceita_todos_os_patterns_acionaveis(pattern: str) -> None:
    assert passa_no_filtro(no(pat=[pattern]), "interactive") is True


def test_interactive_aceita_no_focavel_sem_pattern() -> None:
    assert passa_no_filtro(no(pat=[], st=["enabled", "focusable"]), "interactive") is True


def test_interactive_rejeita_desabilitado() -> None:
    assert passa_no_filtro(no(pat=["Invoke"], st=["disabled"]), "interactive") is False


def test_interactive_rejeita_offscreen() -> None:
    assert passa_no_filtro(no(pat=["Invoke"], st=["enabled", "offscreen"]), "interactive") is False


def test_interactive_rejeita_texto_puro() -> None:
    assert passa_no_filtro(no(type="Text", pat=[], st=["enabled"]), "interactive") is False


def test_value_somente_leitura_nao_conta_como_acionavel() -> None:
    """Spec §6.2 diz 'Value gravavel'. Um Edit readonly nao e ponto de interacao."""
    n = no(type="Edit", pat=["Value"], st=["enabled", "readonly"])
    assert passa_no_filtro(n, "interactive") is False


def test_value_gravavel_conta_como_acionavel() -> None:
    n = no(type="Edit", pat=["Value"], st=["enabled"])
    assert passa_no_filtro(n, "interactive") is True


def test_content_inclui_texto_alem_do_interativo() -> None:
    assert passa_no_filtro(no(type="Text", name="Ola", pat=[], st=["enabled"]), "content") is True
    assert passa_no_filtro(no(pat=["Invoke"]), "content") is True


def test_content_rejeita_texto_sem_nome_nem_valor() -> None:
    assert passa_no_filtro(no(type="Text", name="", pat=[], st=["enabled"]), "content") is False


def test_all_aceita_qualquer_coisa() -> None:
    assert passa_no_filtro(no(type="Pane", pat=[], st=["disabled"]), "all") is True


def test_landmarks_so_containers_estruturais() -> None:
    assert passa_no_filtro(no(type="ToolBar"), "landmarks") is True
    assert passa_no_filtro(no(type="Document"), "landmarks") is True
    assert passa_no_filtro(no(type="Button", pat=["Invoke"]), "landmarks") is False


def test_filtro_desconhecido_e_invalid_argument() -> None:
    with pytest.raises(ToolError) as exc:
        passa_no_filtro(no(), "colorido")
    assert exc.value.code is Code.INVALID_ARGUMENT
    assert "interactive" in exc.value.message


def test_conjunto_de_filtros_bate_com_a_spec() -> None:
    assert set(FILTROS) == {"interactive", "content", "all", "landmarks"}
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_filters.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.uia.filters'`

- [ ] **Step 3: Implementar `uia/filters.py`**

```python
"""Predicados de filtragem da spec §6.2.

Puro: opera sobre o dict do Node ja serializado, sem COM. O filtro roda no
cliente, sobre propriedades ja cacheadas, a custo zero de RPC — e NAO vira
condicao nativa no percurso, porque a travessia precisa descer atraves de
containers que nao passam no filtro para alcancar os que passam (spec §5.2).
"""

from __future__ import annotations

from typing import Any

from ..errors import Code, ToolError

FILTROS = ("interactive", "content", "all", "landmarks")

# Patterns que representam uma acao que o agente pode disparar (spec §6.2).
PATTERNS_ACIONAVEIS = frozenset(
    {"Invoke", "Toggle", "SelectionItem", "ExpandCollapse", "Value", "RangeValue", "Scroll"}
)

# Tipos que carregam conteudo legivel (spec §6.2, filtro "content").
TIPOS_DE_TEXTO = frozenset({"Text", "Document", "Edit", "Image"})

# Containers estruturais (spec §6.2, filtro "landmarks").
TIPOS_ESTRUTURAIS = frozenset(
    {"Window", "Pane", "Group", "ToolBar", "MenuBar", "Tab", "Tree", "List", "Table", "Document"}
)


def _acionavel(node: dict[str, Any]) -> bool:
    patterns = set(node.get("pat", ()))
    st = set(node.get("st", ()))

    # "Value gravavel": um Edit readonly nao e ponto de interacao.
    if "Value" in patterns and "readonly" in st:
        patterns = patterns - {"Value"}

    return bool(patterns & PATTERNS_ACIONAVEIS)


def _interativo(node: dict[str, Any]) -> bool:
    st = set(node.get("st", ()))
    if "disabled" in st or "offscreen" in st:
        return False
    return _acionavel(node) or "focusable" in st


def _conteudo(node: dict[str, Any]) -> bool:
    if _interativo(node):
        return True
    if node.get("type") not in TIPOS_DE_TEXTO:
        return False
    return bool((node.get("name") or "").strip() or node.get("val") not in (None, ""))


def passa_no_filtro(node: dict[str, Any], filtro: str) -> bool:
    """True se o no deve ser emitido sob este filtro. Spec §6.2."""
    if filtro == "interactive":
        return _interativo(node)
    if filtro == "content":
        return _conteudo(node)
    if filtro == "all":
        return True
    if filtro == "landmarks":
        return node.get("type") in TIPOS_ESTRUTURAIS

    raise ToolError(
        Code.INVALID_ARGUMENT,
        f"Unknown filter {filtro!r}. Valid values: {', '.join(FILTROS)}.",
        hint=f"Call again with filter set to one of: {', '.join(FILTROS)}.",
        filter=filtro,
    )
```

- [ ] **Step 4: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_filters.py -q`
Expected: PASS — 16 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_windows_uia/uia/filters.py tests/test_uia_filters.py
git commit -m "feat(uia): predicados de filtragem da spec 6.2, puros e sem COM"
```

---

### Task 3: `uia/tree.py` — percurso em largura com orçamento

**Files:**
- Create: `src/mcp_windows_uia/uia/tree.py`
- Test: `tests/test_uia_tree.py` (unitário, com fakes)
- Test: `tests/e2e/test_tree_captura.py` (e2e, contra janela real)

O coração do plano. Implementa a spec §5.2 (captura limitada por nível) e §6.1 (orçamento, aprofundamento adaptativo).

A função de percurso recebe um **navegador** (`filhos_de`) injetado, em vez de chamar COM direto. Isso é o que torna o orçamento e o aprofundamento adaptativo testáveis sem Windows — a lógica de parada é onde mora o risco, não a chamada COM.

- [ ] **Step 1: Escrever o teste unitário que falha**

`tests/test_uia_tree.py`:

```python
from __future__ import annotations

from mcp_windows_uia.uia.tree import CaptureBudget, percorrer


def arvore_falsa(ramificacao: int, profundidade: int):
    """Gera um navegador de arvore sintetica: cada no tem `ramificacao` filhos."""
    def filhos_de(no_id, _nivel):
        if _nivel >= profundidade:
            return []
        return [f"{no_id}.{i}" for i in range(ramificacao)]
    return filhos_de


def sempre_passa(_no_id, _nivel):
    return True


def nunca_passa(_no_id, _nivel):
    return False


def so_abaixo_de(limite):
    def predicado(_no_id, nivel):
        return nivel >= limite
    return predicado


def test_percurso_e_em_largura_por_nivel() -> None:
    """Spec §6.1: os nos rasos aparecem antes dos fundos."""
    r = percorrer("raiz", arvore_falsa(2, 3), sempre_passa,
                  CaptureBudget(max_nodes=100, max_depth=10))
    niveis = [nivel for _, nivel in r.emitidos]
    assert niveis == sorted(niveis)


def test_para_no_max_nodes() -> None:
    r = percorrer("raiz", arvore_falsa(4, 6), sempre_passa,
                  CaptureBudget(max_nodes=10, max_depth=10))
    assert len(r.emitidos) == 10
    assert r.truncado is True


def test_para_no_max_depth() -> None:
    r = percorrer("raiz", arvore_falsa(2, 10), sempre_passa,
                  CaptureBudget(max_nodes=1000, max_depth=3))
    assert max(nivel for _, nivel in r.emitidos) == 3


def test_max_children_por_no_elide_e_conta_o_resto() -> None:
    """Spec §6.1: emite os primeiros N e anota quantos ficaram de fora."""
    r = percorrer("raiz", arvore_falsa(10, 2), sempre_passa,
                  CaptureBudget(max_nodes=1000, max_depth=5, max_children_per_node=3))
    assert r.elididos["raiz"] == 7
    filhos_diretos = [i for i, nivel in r.emitidos if nivel == 1]
    assert len(filhos_diretos) == 3


def test_aprofundamento_adaptativo_quando_nada_passa_no_filtro() -> None:
    """Spec §6.1: parou raso e nao achou nada -> continua descendo."""
    r = percorrer("raiz", arvore_falsa(2, 8), so_abaixo_de(5),
                  CaptureBudget(max_nodes=100, max_depth=2, depth_is_default=True))
    assert r.auto_deepened is True
    assert r.depth_reached > 2
    assert len(r.emitidos) > 0


def test_aprofundamento_para_assim_que_acha_um_no() -> None:
    r = percorrer("raiz", arvore_falsa(2, 8), so_abaixo_de(4),
                  CaptureBudget(max_nodes=100, max_depth=2, depth_is_default=True))
    assert r.depth_reached == 4  # parou no primeiro nivel que rendeu


def test_sem_aprofundamento_quando_a_profundidade_foi_explicita() -> None:
    """Spec §6.1: max_depth informado pelo chamador e respeitado ao pe da letra."""
    r = percorrer("raiz", arvore_falsa(2, 8), so_abaixo_de(5),
                  CaptureBudget(max_nodes=100, max_depth=2, depth_is_default=False))
    assert r.auto_deepened is False
    assert r.depth_reached == 2
    assert r.emitidos == []


def test_sem_aprofundamento_quando_ja_achou_algo_raso() -> None:
    r = percorrer("raiz", arvore_falsa(2, 8), sempre_passa,
                  CaptureBudget(max_nodes=100, max_depth=2, depth_is_default=True))
    assert r.auto_deepened is False
    assert r.depth_reached == 2


def test_aprofundamento_respeita_o_teto_duro_de_40() -> None:
    r = percorrer("raiz", arvore_falsa(1, 100), nunca_passa,
                  CaptureBudget(max_nodes=1000, max_depth=2, depth_is_default=True))
    assert r.depth_reached <= 40


def test_aprofundamento_respeita_o_max_nodes() -> None:
    r = percorrer("raiz", arvore_falsa(3, 30), so_abaixo_de(25),
                  CaptureBudget(max_nodes=5, max_depth=2, depth_is_default=True))
    assert len(r.emitidos) <= 5


def test_visitados_conta_mais_que_emitidos_quando_ha_filtro() -> None:
    r = percorrer("raiz", arvore_falsa(2, 4), so_abaixo_de(3),
                  CaptureBudget(max_nodes=100, max_depth=4))
    assert r.visitados > len(r.emitidos)


def test_arvore_vazia_nao_explode() -> None:
    r = percorrer("raiz", lambda _n, _l: [], sempre_passa,
                  CaptureBudget(max_nodes=10, max_depth=5))
    assert len(r.emitidos) == 1  # so a raiz
    assert r.truncado is False
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_tree.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.uia.tree'`

- [ ] **Step 3: Implementar a lógica de percurso em `uia/tree.py`**

```python
"""Captura da arvore: percurso em largura com orcamento. Spec §5.2 e §6.1.

Por que o navegador e injetado: a logica de parada (orcamento, elisao de irmaos,
aprofundamento adaptativo) e onde mora o risco, e ela precisa ser testavel sem
Windows. `percorrer` nao sabe o que e COM; quem sabe e `filhos_cacheados`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..budget import (
    DEFAULT_MAX_CHILDREN,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_NODES,
    HARD_MAX_DEPTH,
    clamp,
)

# Assinaturas do que `percorrer` recebe de fora.
FilhosDe = Callable[[Any, int], Sequence[Any]]
Aprovado = Callable[[Any, int], bool]


@dataclass(frozen=True, slots=True)
class CaptureBudget:
    max_nodes: int = DEFAULT_MAX_NODES
    max_depth: int = DEFAULT_MAX_DEPTH
    max_children_per_node: int = DEFAULT_MAX_CHILDREN
    # True quando o chamador NAO informou max_depth. So entao aprofundamos (§6.1).
    depth_is_default: bool = False


@dataclass(slots=True)
class CaptureResult:
    emitidos: list[tuple[Any, int]] = field(default_factory=list)
    elididos: dict[Any, int] = field(default_factory=dict)
    visitados: int = 0
    truncado: bool = False
    auto_deepened: bool = False
    depth_reached: int = 0
    fila_restante: list[tuple[Any, int]] = field(default_factory=list)


def percorrer(
    raiz: Any,
    filhos_de: FilhosDe,
    aprovado: Aprovado,
    orcamento: CaptureBudget,
) -> CaptureResult:
    """Largura por nivel, ordem de documento dentro do nivel. Spec §5.2 passo 4.

    O orcamento e aplicado DURANTE a travessia: assim que max_nodes e atingido,
    nenhuma chamada adicional a filhos_de acontece. E isso que faz a captura
    custar 159 ms em vez de 18 s numa janela grande.
    """
    r = CaptureResult()
    teto_profundidade = clamp(orcamento.max_depth, 1, HARD_MAX_DEPTH)

    fila: list[tuple[Any, int]] = [(raiz, 0)]

    while fila:
        no, nivel = fila.pop(0)

        r.visitados += 1
        r.depth_reached = max(r.depth_reached, nivel)
        if aprovado(no, nivel):
            r.emitidos.append((no, nivel))

        if len(r.emitidos) >= orcamento.max_nodes:
            r.truncado = bool(fila)
            r.fila_restante = fila
            return r

        if nivel >= teto_profundidade:
            # Aprofundamento adaptativo (§6.1): so continua se o chamador nao pediu
            # profundidade explicita E nada passou no filtro ate aqui.
            pode_aprofundar = (
                orcamento.depth_is_default
                and not r.emitidos
                and teto_profundidade < HARD_MAX_DEPTH
            )
            if not pode_aprofundar:
                r.fila_restante = [(no, nivel), *fila]
                r.truncado = bool(fila)
                continue
            teto_profundidade = min(teto_profundidade + 1, HARD_MAX_DEPTH)
            r.auto_deepened = True

        for filho in _filhos_limitados(no, nivel, filhos_de, orcamento, r):
            fila.append((filho, nivel + 1))

    return r


def _filhos_limitados(
    no: Any,
    nivel: int,
    filhos_de: FilhosDe,
    orcamento: CaptureBudget,
    r: CaptureResult,
) -> Sequence[Any]:
    """Aplica max_children_per_node fatiando; anota quantos ficaram de fora (§6.1)."""
    filhos = filhos_de(no, nivel)
    if len(filhos) > orcamento.max_children_per_node:
        r.elididos[no] = len(filhos) - orcamento.max_children_per_node
        return filhos[: orcamento.max_children_per_node]
    return filhos
```

- [ ] **Step 4: Rodar os testes unitários**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_tree.py -q`
Expected: PASS — 12 passed

- [ ] **Step 5: Commit da lógica pura**

```bash
git add src/mcp_windows_uia/uia/tree.py tests/test_uia_tree.py
git commit -m "feat(uia): percurso em largura com orcamento e aprofundamento adaptativo"
```

- [ ] **Step 6: Escrever o teste e2e que falha**

`tests/e2e/test_tree_captura.py`:

```python
"""Captura contra janela real. Cobre CA-02 e CA-21 da spec."""

from __future__ import annotations

import subprocess
import time

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def sta():
    import comtypes

    from mcp_windows_uia.uia import core

    comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
    yield
    core.reset_for_tests()


@pytest.fixture(scope="module")
def bloco_de_notas(sta):
    """Abre um Bloco de Notas dedicado e o fecha ao fim."""
    from mcp_windows_uia.uia.windows import enumerate_windows

    proc = subprocess.Popen(["notepad.exe"])
    janela = None
    for _ in range(50):
        time.sleep(0.2)
        candidatas = [w for w in enumerate_windows() if w.pid == proc.pid and w.title]
        if candidatas:
            janela = candidatas[0]
            break
    if janela is None:
        proc.terminate()
        pytest.skip("Bloco de Notas nao abriu a tempo")
    yield janela
    proc.terminate()


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
```

- [ ] **Step 7: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/e2e/test_tree_captura.py -q`
Expected: FAIL — `ImportError: cannot import name 'capturar_janela'`

- [ ] **Step 8: Implementar `capturar_janela` em `uia/tree.py`**

Acrescentar ao fim de `src/mcp_windows_uia/uia/tree.py`:

```python
# ---------------------------------------------------------------- ponte com COM


def _patterns_disponiveis(elem: Any, props_de_pattern: dict[str, int]) -> list[str]:
    """Patterns acionaveis suportados, lidos do cache. Zero RPC."""
    from .nodes import _cached

    disponiveis: list[str] = []
    for nome in props_de_pattern:
        if _cached(elem, f"CachedIs{nome}PatternAvailable", 0):
            disponiveis.append(nome)
    return disponiveis


def filhos_cacheados(automation: Any, cache_request: Any):
    """Navegador que fala COM: um FindAllBuildCache(Children) por no-pai (§5.2)."""
    UIA = automation.UIA

    def _filhos(elem: Any, _nivel: int) -> list[Any]:
        try:
            achados = elem.FindAllBuildCache(
                UIA.TreeScope_Children, automation.true_condition, cache_request
            )
        except Exception:
            # No que sumiu ou provider que recusou: subarvore vazia, nao aborta a captura.
            return []
        return [achados.GetElement(i) for i in range(achados.Length)]

    return _filhos


def capturar_janela(
    hwnd: int,
    *,
    filtro: str = "interactive",
    max_nodes: int = DEFAULT_MAX_NODES,
    max_depth: int | None = None,
    max_children_per_node: int = DEFAULT_MAX_CHILDREN,
    verbose: bool = False,
    atribuir_ref: Callable[[Any, int], str] | None = None,
) -> dict[str, Any]:
    """Captura a arvore de uma janela dentro do orcamento. Spec §5.2.

    `atribuir_ref` e injetado pelo servidor para registrar cada no no RefStore.
    Ausente (uso em teste), gera refs sinteticas.
    """
    from ..budget import HARD_MAX_NODES, TRUNCATION_HINT
    from . import core
    from .filters import passa_no_filtro
    from .nodes import build_node

    automation = core.automation()
    cache_request = automation.build_cache_request(automation.tree_props())
    raiz = automation.element_from_handle(hwnd)

    props_de_pattern = core.pattern_availability_props()
    navegador = filhos_cacheados(automation, cache_request)

    # `percorrer` chama _aprovado uma vez por no visitado e, quando True, faz append
    # em emitidos na mesma ordem. Entao aprovados[i] corresponde a r.emitidos[i] —
    # sem mapa por id(), que seria fragil com ponteiros COM.
    aprovados: list[dict[str, Any]] = []
    contador = [0]

    def _aprovado(elem: Any, nivel: int) -> bool:
        # Serializa com ref provisoria: o filtro nao olha para `ref`, e cunhar a ref
        # antes de saber se o no passa desperdicaria numeros e (pior) registraria no
        # RefStore elementos que nunca serao devolvidos ao agente.
        node = build_node(
            elem,
            ref="",
            depth=nivel,
            patterns=_patterns_disponiveis(elem, props_de_pattern),
            verbose=verbose,
        )
        if not passa_no_filtro(node, filtro):
            return False

        node["ref"] = (
            atribuir_ref(elem, contador[0])
            if atribuir_ref is not None
            else f"w{hwnd}-e{contador[0]}"
        )
        contador[0] += 1
        aprovados.append(node)
        return True

    orcamento = CaptureBudget(
        max_nodes=clamp(max_nodes, 1, HARD_MAX_NODES),
        max_depth=DEFAULT_MAX_DEPTH if max_depth is None else max_depth,
        max_children_per_node=max_children_per_node,
        depth_is_default=max_depth is None,
    )

    r = percorrer(raiz, navegador, _aprovado, orcamento)

    nodes: list[dict[str, Any]] = []
    for (elem, _nivel), node in zip(r.emitidos, aprovados, strict=True):
        elididos = r.elididos.get(elem)
        if elididos:
            node["n"] = elididos
        nodes.append(node)

    stats: dict[str, Any] = {
        "returned": len(nodes),
        "visited": r.visitados,
        "truncated": r.truncado,
        "depth_reached": r.depth_reached,
    }
    if r.auto_deepened:
        stats["auto_deepened"] = True
        stats["depth_requested"] = DEFAULT_MAX_DEPTH
    if r.truncado:
        stats["hint"] = TRUNCATION_HINT

    return {"nodes": nodes, "stats": stats}
```

- [ ] **Step 9: Rodar os testes e2e**

Run: `.venv/Scripts/python.exe -m pytest tests/e2e/test_tree_captura.py -q`
Expected: PASS — 4 passed

- [ ] **Step 10: Rodar a suíte inteira**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — nenhuma regressão

- [ ] **Step 11: Commit**

```bash
git add src/mcp_windows_uia/uia/tree.py tests/e2e/test_tree_captura.py
git commit -m "feat(uia): capturar_janela com CacheRequest por nivel, cobre CA-02/CA-09/CA-21"
```

---

### Task 4: `rebind.py` — re-resolução por identidade (§7.2)

**Files:**
- Create: `src/mcp_windows_uia/rebind.py`
- Test: `tests/test_rebind.py`

Implementa o passo 5 do algoritmo `resolve` da spec §7.2.

**Decisão de desenho deliberada:** a assinatura é `rebind(automation, hwnd, identity) -> elemento`, tomando `ElementIdentity` e **não** um `ref` nem o `RefStore`. Motivo: a camada futura de aprendizado de seletores por app persiste exatamente `ElementIdentity` em disco e precisa re-resolvê-la numa sessão nova, onde nenhum `RefStore` daquela sessão existe. Se o rebind nascesse acoplado ao store, essa camada exigiria refatoração.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_rebind.py`:

```python
from __future__ import annotations

import pytest

from mcp_windows_uia.errors import Code, ToolError
from mcp_windows_uia.rebind import Estrategia, rebind
from mcp_windows_uia.refs import ElementIdentity


class FakeAutomation:
    """Automation falso: registra as buscas pedidas e devolve o que foi programado."""

    def __init__(self, por_estrategia=None):
        self.por_estrategia = por_estrategia or {}
        self.buscas: list[Estrategia] = []

    def buscar(self, _hwnd, _identity, estrategia):
        self.buscas.append(estrategia)
        return self.por_estrategia.get(estrategia, [])


def ident(**kw) -> ElementIdentity:
    base = {
        "automation_id": "SaveBtn",
        "control_type": "Button",
        "name": "Salvar",
        "class_name": "Button",
        "index_path": (0, 3, 1),
    }
    base.update(kw)
    return ElementIdentity(**base)


def test_acha_por_automation_id_primeiro() -> None:
    a = FakeAutomation({Estrategia.AUTOMATION_ID: ["elem-a"]})
    achado, estrategia = rebind(a, hwnd=1, identity=ident())
    assert achado == "elem-a"
    assert estrategia is Estrategia.AUTOMATION_ID
    assert a.buscas == [Estrategia.AUTOMATION_ID]


def test_cai_para_name_quando_automation_id_nao_acha() -> None:
    a = FakeAutomation({Estrategia.NAME: ["elem-b"]})
    achado, estrategia = rebind(a, hwnd=1, identity=ident())
    assert achado == "elem-b"
    assert estrategia is Estrategia.NAME
    assert a.buscas == [Estrategia.AUTOMATION_ID, Estrategia.NAME]


def test_cai_para_index_path_em_ultimo_caso() -> None:
    a = FakeAutomation({Estrategia.INDEX_PATH: ["elem-c"]})
    achado, estrategia = rebind(a, hwnd=1, identity=ident())
    assert achado == "elem-c"
    assert estrategia is Estrategia.INDEX_PATH
    assert a.buscas == [Estrategia.AUTOMATION_ID, Estrategia.NAME, Estrategia.INDEX_PATH]


def test_pula_automation_id_quando_vazio() -> None:
    """Sem AutomationId nao ha o que buscar; nao desperdica uma chamada COM."""
    a = FakeAutomation({Estrategia.NAME: ["elem-b"]})
    rebind(a, hwnd=1, identity=ident(automation_id=""))
    assert Estrategia.AUTOMATION_ID not in a.buscas


def test_multiplos_candidatos_viram_ambiguous_match() -> None:
    """Spec principio 4: nada de best match silencioso."""
    a = FakeAutomation({Estrategia.AUTOMATION_ID: ["elem-a", "elem-b"]})
    with pytest.raises(ToolError) as exc:
        rebind(a, hwnd=1, identity=ident())
    assert exc.value.code is Code.AMBIGUOUS_MATCH
    assert exc.value.details["count"] == 2


def test_zero_candidatos_em_tudo_vira_stale_ref() -> None:
    a = FakeAutomation({})
    with pytest.raises(ToolError) as exc:
        rebind(a, hwnd=1, identity=ident())
    assert exc.value.code is Code.STALE_REF
    assert exc.value.details["reason"] == "element_gone"


def test_identity_e_serializavel_para_dict_e_de_volta() -> None:
    """Gancho da camada futura de aprendizado: identidade precisa persistir em disco."""
    from mcp_windows_uia.rebind import identity_from_dict, identity_to_dict

    original = ident()
    redonda = identity_from_dict(identity_to_dict(original))
    assert redonda == original


def test_identity_serializada_e_json_puro() -> None:
    import json

    from mcp_windows_uia.rebind import identity_to_dict

    d = identity_to_dict(ident())
    assert json.loads(json.dumps(d)) == d
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rebind.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.rebind'`

- [ ] **Step 3: Implementar `rebind.py`**

```python
"""Re-resolucao de elemento por identidade. Spec §7.2, passo 5.

Assinatura deliberada: recebe ElementIdentity, nao um ref nem o RefStore. A
camada futura de aprendizado de seletores por app persiste exatamente esta
identidade em disco e precisa re-resolve-la numa sessao nova, onde o RefStore
daquela sessao nao existe. Acoplar isto ao store exigiria refatorar depois.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from .errors import Code, ToolError
from .refs import ElementIdentity


class Estrategia(StrEnum):
    """Ordem de tentativa da spec §7.2, da mais forte para a mais fraca."""

    AUTOMATION_ID = "automation_id"
    NAME = "name"
    INDEX_PATH = "index_path"


def identity_to_dict(identity: ElementIdentity) -> dict[str, Any]:
    """Serializa para JSON puro. index_path vira lista (tupla nao e JSON)."""
    return {
        "automation_id": identity.automation_id,
        "control_type": identity.control_type,
        "name": identity.name,
        "class_name": identity.class_name,
        "index_path": list(identity.index_path),
    }


def identity_from_dict(d: dict[str, Any]) -> ElementIdentity:
    return ElementIdentity(
        automation_id=d.get("automation_id", ""),
        control_type=d.get("control_type", ""),
        name=d.get("name", ""),
        class_name=d.get("class_name", ""),
        index_path=tuple(d.get("index_path", ())),
    )


def _estrategias_aplicaveis(identity: ElementIdentity) -> list[Estrategia]:
    """Pula estrategias sem material — cada uma custa uma chamada COM."""
    ordem: list[Estrategia] = []
    if identity.automation_id:
        ordem.append(Estrategia.AUTOMATION_ID)
    if identity.name:
        ordem.append(Estrategia.NAME)
    if identity.index_path:
        ordem.append(Estrategia.INDEX_PATH)
    return ordem


def rebind(
    automation: Any, *, hwnd: int, identity: ElementIdentity
) -> tuple[Any, Estrategia]:
    """Acha o elemento de novo. Spec §7.2 passo 5.

    Devolve (elemento, estrategia_que_funcionou).
    Levanta AMBIGUOUS_MATCH se mais de um candidato; STALE_REF se nenhum.
    """
    for estrategia in _estrategias_aplicaveis(identity):
        candidatos = automation.buscar(hwnd, identity, estrategia)

        if len(candidatos) == 1:
            return candidatos[0], estrategia

        if len(candidatos) > 1:
            raise ToolError(
                Code.AMBIGUOUS_MATCH,
                f"{len(candidatos)} elements match the remembered identity "
                f"({estrategia.value}); refusing to guess.",
                strategy=estrategia.value,
                count=len(candidatos),
                automation_id=identity.automation_id,
                name=identity.name,
                control_type=identity.control_type,
            )

    raise ToolError(
        Code.STALE_REF,
        "The element no longer exists in this window and could not be found again.",
        reason="element_gone",
        window_alive=True,
        automation_id=identity.automation_id,
        name=identity.name,
        control_type=identity.control_type,
    )
```

- [ ] **Step 4: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rebind.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_windows_uia/rebind.py tests/test_rebind.py
git commit -m "feat(refs): rebind por ElementIdentity, desacoplado do RefStore"
```

---

### Task 5: `uia/search.py` — busca plana com condição nativa (§8.4)

**Files:**
- Create: `src/mcp_windows_uia/uia/search.py`
- Test: `tests/test_uia_search.py`

Aqui a condição nativa **é** legítima: a busca é plana, não precisa atravessar containers. Critérios exatos viram `IUIAutomationCondition` e a filtragem acontece no provider; `contains`/`starts_with`/`regex` filtram no cliente.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_uia_search.py`:

```python
from __future__ import annotations

import pytest

from mcp_windows_uia.errors import Code, ToolError
from mcp_windows_uia.uia.search import Criterios, casa_no_cliente, exige_filtro_no_cliente


def crit(**kw) -> Criterios:
    base = {"name": None, "automation_id": None, "control_type": None,
            "class_name": None, "text_contains": None, "match": "contains"}
    base.update(kw)
    return Criterios(**base)


def test_criterios_vazios_sao_invalid_argument() -> None:
    with pytest.raises(ToolError) as exc:
        crit().validar()
    assert exc.value.code is Code.INVALID_ARGUMENT


def test_match_exact_nao_precisa_de_filtro_no_cliente() -> None:
    assert exige_filtro_no_cliente(crit(name="Salvar", match="exact")) is False


def test_match_contains_precisa_de_filtro_no_cliente() -> None:
    assert exige_filtro_no_cliente(crit(name="Salv", match="contains")) is True


def test_text_contains_sempre_precisa_de_filtro_no_cliente() -> None:
    assert exige_filtro_no_cliente(crit(text_contains="olá", match="exact")) is True


def test_automation_id_sozinho_e_resolvido_no_provider() -> None:
    assert exige_filtro_no_cliente(crit(automation_id="SaveBtn")) is False


def test_casa_contains_e_case_insensitive() -> None:
    no = {"name": "Salvar Como", "type": "MenuItem", "aid": "SaveAs"}
    assert casa_no_cliente(no, crit(name="salvar", match="contains")) is True


def test_casa_exact_e_case_insensitive_no_name() -> None:
    no = {"name": "Salvar", "type": "Button"}
    assert casa_no_cliente(no, crit(name="SALVAR", match="exact")) is True
    assert casa_no_cliente(no, crit(name="Salv", match="exact")) is False


def test_automation_id_e_case_sensitive() -> None:
    """Spec §8.4: automation_id e exato e case-sensitive."""
    no = {"name": "x", "type": "Button", "aid": "SaveBtn"}
    assert casa_no_cliente(no, crit(automation_id="SaveBtn")) is True
    assert casa_no_cliente(no, crit(automation_id="savebtn")) is False


def test_casa_starts_with() -> None:
    no = {"name": "Salvar Como", "type": "MenuItem"}
    assert casa_no_cliente(no, crit(name="Salvar", match="starts_with")) is True
    assert casa_no_cliente(no, crit(name="Como", match="starts_with")) is False


def test_casa_regex() -> None:
    no = {"name": "Arquivo 42", "type": "Text"}
    assert casa_no_cliente(no, crit(name=r"Arquivo \d+", match="regex")) is True
    assert casa_no_cliente(no, crit(name=r"^\d+$", match="regex")) is False


def test_regex_invalida_vira_invalid_argument() -> None:
    with pytest.raises(ToolError) as exc:
        casa_no_cliente({"name": "x"}, crit(name="(nao fecha", match="regex"))
    assert exc.value.code is Code.INVALID_ARGUMENT


def test_criterios_combinados_sao_conjuncao() -> None:
    no = {"name": "Salvar", "type": "Button", "aid": "SaveBtn"}
    assert casa_no_cliente(no, crit(name="Salvar", control_type="Button")) is True
    assert casa_no_cliente(no, crit(name="Salvar", control_type="MenuItem")) is False


def test_text_contains_procura_no_valor_tambem() -> None:
    no = {"name": "Campo", "type": "Edit", "val": "conteudo escondido"}
    assert casa_no_cliente(no, crit(text_contains="escondido")) is True


def test_match_invalido_vira_invalid_argument() -> None:
    with pytest.raises(ToolError) as exc:
        crit(name="x", match="parecido").validar()
    assert exc.value.code is Code.INVALID_ARGUMENT
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_search.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.uia.search'`

- [ ] **Step 3: Implementar `uia/search.py`**

```python
"""Busca plana de elementos. Spec §8.4.

Aqui condicao nativa E legitima, ao contrario do percurso da §5.2: a busca e
plana e nao precisa atravessar containers que nao casam. Criterios exatos viram
IUIAutomationCondition e sao resolvidos no provider; contains/starts_with/regex
e text_contains filtram no cliente sobre propriedades ja cacheadas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..errors import Code, ToolError

MATCHES = ("exact", "contains", "starts_with", "regex")


@dataclass(frozen=True, slots=True)
class Criterios:
    name: str | None = None
    automation_id: str | None = None
    control_type: str | None = None
    class_name: str | None = None
    text_contains: str | None = None
    match: str = "contains"

    def validar(self) -> None:
        if self.match not in MATCHES:
            raise ToolError(
                Code.INVALID_ARGUMENT,
                f"Unknown match mode {self.match!r}. Valid values: {', '.join(MATCHES)}.",
                match=self.match,
            )
        informados = (
            self.name, self.automation_id, self.control_type,
            self.class_name, self.text_contains,
        )
        if not any(informados):
            raise ToolError(
                Code.INVALID_ARGUMENT,
                "At least one of name, automation_id, control_type, class_name or "
                "text_contains is required.",
                hint="Add a criterion, or call uia_get_tree to see what is in the window.",
            )


def exige_filtro_no_cliente(criterios: Criterios) -> bool:
    """True se algum criterio nao pode virar condicao nativa (spec §8.4)."""
    if criterios.text_contains:
        return True
    if criterios.name and criterios.match != "exact":
        return True
    return False


def _casa_texto(valor: str, alvo: str, modo: str) -> bool:
    if modo == "exact":
        return valor.casefold() == alvo.casefold()
    if modo == "contains":
        return alvo.casefold() in valor.casefold()
    if modo == "starts_with":
        return valor.casefold().startswith(alvo.casefold())

    try:
        return re.search(alvo, valor, re.IGNORECASE) is not None
    except re.error as exc:
        raise ToolError(
            Code.INVALID_ARGUMENT,
            f"Invalid regular expression {alvo!r}: {exc}.",
            hint="Fix the pattern, or use match='contains' instead.",
            pattern=alvo,
        ) from exc


def casa_no_cliente(node: dict[str, Any], criterios: Criterios) -> bool:
    """Conjuncao de todos os criterios informados, sobre um Node ja serializado."""
    if criterios.automation_id is not None:
        # Spec §8.4: automation_id e exato E case-sensitive.
        if node.get("aid", "") != criterios.automation_id:
            return False

    if criterios.control_type is not None:
        if node.get("type", "") != criterios.control_type:
            return False

    if criterios.class_name is not None:
        if node.get("cls", "") != criterios.class_name:
            return False

    if criterios.name is not None:
        if not _casa_texto(node.get("name", "") or "", criterios.name, criterios.match):
            return False

    if criterios.text_contains is not None:
        alvo = criterios.text_contains.casefold()
        campos = (str(node.get("name", "") or ""), str(node.get("val", "") or ""))
        if not any(alvo in campo.casefold() for campo in campos):
            return False

    return True
```

- [ ] **Step 4: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_search.py -q`
Expected: PASS — 14 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_windows_uia/uia/search.py tests/test_uia_search.py
git commit -m "feat(uia): criterios de busca da spec 8.4 com filtragem no cliente"
```

---

### Task 6: `uia/waits.py` — polling com backoff (§7.3)

**Files:**
- Create: `src/mcp_windows_uia/uia/waits.py`
- Test: `tests/test_uia_waits.py`

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_uia_waits.py`:

```python
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_waits.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.uia.waits'`

- [ ] **Step 3: Implementar `uia/waits.py`**

```python
"""Espera por condicao, com backoff. Spec §7.3.

`dormir` e `agora` sao injetados para o teste nao gastar segundos de relogio real.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..errors import Code, ToolError

BACKOFF_TETO_MS = 250
TIMEOUT_PADRAO_MS = 5000
TIMEOUT_MAX_MS = 60000


def proximo_intervalo(atual_ms: int) -> int:
    """50 -> 100 -> 200 -> 250 (teto). Spec §7.3."""
    return min(atual_ms * 2, BACKOFF_TETO_MS)


@dataclass(slots=True)
class EsperaResult:
    satisfeito: bool
    resultado: Any
    waited_ms: float
    polls: int


def esperar_por(
    condicao: Callable[[], Any],
    *,
    timeout_ms: int = TIMEOUT_PADRAO_MS,
    poll_ms: int = 100,
    dormir: Callable[[int], None] | None = None,
    agora: Callable[[], float] | None = None,
    descricao: str = "condition",
) -> EsperaResult:
    """Chama `condicao` ate ela devolver algo truthy, ou estourar o timeout.

    Uma ToolError levantada pela condicao propaga imediatamente: um WINDOW_CLOSED
    e um fato, nao uma condicao que ainda pode virar verdadeira.
    """
    _dormir = dormir if dormir is not None else (lambda ms: time.sleep(ms / 1000.0))
    _agora = agora if agora is not None else time.monotonic

    inicio = _agora()
    intervalo = max(50, min(poll_ms, BACKOFF_TETO_MS))
    polls = 0

    while True:
        polls += 1
        resultado = condicao()
        decorrido_ms = (_agora() - inicio) * 1000.0

        if resultado:
            return EsperaResult(True, resultado, decorrido_ms, polls)

        if decorrido_ms >= timeout_ms:
            raise ToolError(
                Code.TIMEOUT,
                f"Condition {descricao!r} not satisfied within {timeout_ms} ms.",
                hint=(
                    "Inspect the current state with uia_get_tree before retrying, "
                    "or raise timeout_ms."
                ),
                waited_ms=round(decorrido_ms, 1),
                polls=polls,
            )

        _dormir(intervalo)
        intervalo = proximo_intervalo(intervalo)
```

- [ ] **Step 4: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_waits.py -q`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_windows_uia/uia/waits.py tests/test_uia_waits.py
git commit -m "feat(uia): espera por condicao com backoff da spec 7.3"
```

---

### Task 7: Tool `uia_get_tree` (§8.2)

**Files:**
- Modify: `src/mcp_windows_uia/server.py`
- Test: `tests/e2e/test_tool_get_tree.py`

Primeira tool que junta tudo: policy, worker, captura, RefStore.

- [ ] **Step 1: Escrever o teste e2e que falha**

`tests/e2e/test_tool_get_tree.py`:

```python
"""uia_get_tree contra janela real. Cobre CA-08 e CA-13."""

from __future__ import annotations

import subprocess
import time

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def servidor():
    """ServerContext com allowlist permitindo o Bloco de Notas."""
    from mcp_windows_uia.config import (
        AllowlistConfig, AuditConfig, Config, DenylistConfig, KeysConfig, ServerConfig,
    )
    from mcp_windows_uia.context import ServerContext, set_context

    cfg = Config(
        server=ServerConfig(),
        allowlist=AllowlistConfig(processes=frozenset({"notepad.exe", "explorer.exe"})),
        denylist=DenylistConfig(processes=frozenset()),
        audit=AuditConfig(),
        keys=KeysConfig(),
    )
    ctx = ServerContext(cfg)
    ctx.start()
    set_context(ctx)
    yield ctx
    ctx.shutdown()


@pytest.fixture(scope="module")
def notepad(servidor):
    from mcp_windows_uia.uia.windows import enumerate_windows

    proc = subprocess.Popen(["notepad.exe"])
    janela = None
    for _ in range(50):
        time.sleep(0.2)
        candidatas = [w for w in enumerate_windows() if w.pid == proc.pid and w.title]
        if candidatas:
            janela = candidatas[0]
            break
    if janela is None:
        proc.terminate()
        pytest.skip("Bloco de Notas nao abriu a tempo")
    yield janela
    proc.terminate()


async def test_get_tree_devolve_arvore_com_refs(servidor, notepad) -> None:
    from mcp_windows_uia.server import uia_get_tree

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    r = await uia_get_tree(window_ref=wref)

    assert r["ok"] is True
    assert r["window_ref"] == wref
    assert len(r["nodes"]) > 0
    assert "stats" in r


async def test_ca08_truncamento_sinaliza_e_da_cursor(servidor, notepad) -> None:
    """CA-08: truncated=true, returned bate, next_cursor e hint presentes."""
    from mcp_windows_uia.server import uia_get_tree

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    r = await uia_get_tree(window_ref=wref, filter="all", max_nodes=5)

    assert r["stats"]["returned"] == 5
    if r["stats"]["truncated"]:
        assert r["stats"]["next_cursor"] is not None
        assert "hint" in r["stats"]


async def test_ca13_janela_fora_da_allowlist_e_negada(servidor) -> None:
    """CA-13: APP_NOT_ALLOWED nomeia o processo e instrui sobre config."""
    from mcp_windows_uia.server import uia_get_tree
    from mcp_windows_uia.uia.windows import enumerate_windows

    fora = [w for w in enumerate_windows() if w.process.lower() != "notepad.exe"]
    if not fora:
        pytest.skip("nenhuma janela fora da allowlist para testar")

    wref = servidor.refs.window_ref(hwnd=fora[0].hwnd)
    r = await uia_get_tree(window_ref=wref)

    assert r["ok"] is False
    assert r["error"]["code"] == "APP_NOT_ALLOWED"
    assert "config.toml" in r["error"]["hint"]


async def test_window_ref_desconhecida_e_erro_acionavel(servidor) -> None:
    from mcp_windows_uia.server import uia_get_tree

    r = await uia_get_tree(window_ref="w99999")
    assert r["ok"] is False
    assert r["error"]["code"] in ("WINDOW_NOT_FOUND", "REF_NOT_FOUND")
    assert "uia_list_windows" in r["error"]["hint"]
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/e2e/test_tool_get_tree.py -q`
Expected: FAIL — `ImportError: cannot import name 'uia_get_tree'`

- [ ] **Step 3: Acrescentar o helper de resolução de janela em `server.py`**

Inserir logo após a função `tool_errors`:

```python
def resolver_janela(ctx: ServerContext, window_ref: str) -> "WindowInfo":
    """window_ref -> WindowInfo viva, com allowlist ja validada.

    Erros distintos de proposito: ref desconhecida, janela morta e janela negada
    exigem acoes diferentes do agente.
    """
    from .uia.windows import enumerate_windows, window_is_alive

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
```

Acrescentar o import no topo do arquivo:

```python
from .uia.windows import WindowInfo, enumerate_windows, server_is_elevated, window_is_alive
```

- [ ] **Step 4: Implementar `uia_get_tree` em `server.py`**

Acrescentar ao fim de `server.py`:

```python
# --------------------------------------------------------------------------- 8.2


def get_tree_impl(
    ctx: ServerContext,
    *,
    window_ref: str,
    filtro: str,
    max_depth: int | None,
    max_nodes: int,
    max_children_per_node: int,
    verbose: bool,
) -> dict[str, Any]:
    """Corpo sincrono de uia_get_tree. Roda na thread do worker."""
    from .refs import ElementIdentity
    from .uia.core import automation, control_type_name
    from .uia.nodes import _cached
    from .uia.tree import capturar_janela

    inicio = time.perf_counter()
    janela = resolver_janela(ctx, window_ref)
    versao = ctx.refs.bump_tree_version(window_ref)

    def registrar(elem: Any, indice: int) -> str:
        """Registra o elemento no RefStore e devolve a ref estavel."""
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
        atribuir_ref=registrar,
    )

    stats = capturado["stats"]
    stats["next_cursor"] = None  # paginacao por cursor entra com o CA-08 completo

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
    verbose: Annotated[bool, Field(description="Include HelpText/FullDescription.")] = False,
) -> dict[str, Any]:
    """Capture the UI Automation tree of a window as a flat, pre-order list of nodes with
    stable refs. Defaults to interactive elements only. Omit max_depth so the server can
    go deeper automatically in deeply nested apps such as Electron or WebView2."""
    ctx = context()
    return await ctx.worker.run(
        lambda: get_tree_impl(
            ctx,
            window_ref=window_ref,
            filtro=filter,
            max_depth=max_depth,
            max_nodes=max_nodes,
            max_children_per_node=max_children_per_node,
            verbose=verbose,
        )
    )
```

- [ ] **Step 5: Rodar os testes e2e**

Run: `.venv/Scripts/python.exe -m pytest tests/e2e/test_tool_get_tree.py -q`
Expected: PASS — 4 passed

- [ ] **Step 6: Verificar que a tool aparece no `tools/list`**

Run: `.venv/Scripts/python.exe -c "from mcp_windows_uia.server import mcp; import asyncio; print([t.name for t in asyncio.run(mcp.list_tools())])"`
Expected: contém `uia_list_windows` e `uia_get_tree`

- [ ] **Step 7: Commit**

```bash
git add src/mcp_windows_uia/server.py tests/e2e/test_tool_get_tree.py
git commit -m "feat(server): tool uia_get_tree com registro de refs, cobre CA-08/CA-13"
```

---

### Task 8: Tool `uia_find_elements` (§8.4)

**Files:**
- Modify: `src/mcp_windows_uia/server.py`
- Test: `tests/e2e/test_tool_find_elements.py`

- [ ] **Step 1: Escrever o teste e2e que falha**

`tests/e2e/test_tool_find_elements.py`:

```python
"""uia_find_elements contra janela real. Cobre CA-24."""

from __future__ import annotations

import pytest

from tests.e2e.test_tool_get_tree import notepad, servidor  # noqa: F401

pytestmark = pytest.mark.e2e


async def test_acha_por_control_type(servidor, notepad) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_find_elements

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    r = await uia_find_elements(window_ref=wref, control_type="Edit")

    assert r["ok"] is True
    assert len(r["matches"]) >= 1
    assert all(m["type"] == "Edit" for m in r["matches"])


async def test_matches_trazem_path_para_desambiguar(servidor, notepad) -> None:  # noqa: F811
    """Spec §8.4: path evita uma chamada extra so para o agente escolher."""
    from mcp_windows_uia.server import uia_find_elements

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    r = await uia_find_elements(window_ref=wref, control_type="Edit")

    assert all("path" in m for m in r["matches"])


async def test_zero_matches_e_erro_com_hint(servidor, notepad) -> None:  # noqa: F811
    """Spec §8.4: zero resultados e ELEMENT_NOT_FOUND, nao lista vazia."""
    from mcp_windows_uia.server import uia_find_elements

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    r = await uia_find_elements(window_ref=wref, name="botao-que-nao-existe-xyz")

    assert r["ok"] is False
    assert r["error"]["code"] == "ELEMENT_NOT_FOUND"
    assert "uia_get_tree" in r["error"]["hint"]


async def test_sem_criterio_algum_e_invalid_argument(servidor, notepad) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_find_elements

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    r = await uia_find_elements(window_ref=wref)

    assert r["ok"] is False
    assert r["error"]["code"] == "INVALID_ARGUMENT"
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/e2e/test_tool_find_elements.py -q`
Expected: FAIL — `ImportError: cannot import name 'uia_find_elements'`

- [ ] **Step 3: Implementar a busca plana nativa em `uia/core.py`**

Este e o metodo que a §8.4 pede e que o `uia_wait_for` (Task 11) e o rebind (Task 12)
tambem usam. Sem ele, cada poll do wait_for custaria uma captura de arvore inteira —
o CA-11 exige `polls > 5` em 1500 ms e nao sobreviveria a isso.

Acrescentar a classe `Automation` em `src/mcp_windows_uia/uia/core.py`:

```python
    def condicao_de_criterios(self, criterios: Any) -> Any:
        """Traduz o que da para condicao nativa. Spec §8.4.

        So criterios EXATOS viram condicao — o provider filtra do lado dele, o que e
        muito mais barato que trazer a arvore. contains/starts_with/regex e
        text_contains ficam para o filtro no cliente.
        """
        U = self.UIA
        condicoes: list[Any] = []

        if criterios.automation_id:
            condicoes.append(
                self.property_condition(U.UIA_AutomationIdPropertyId, criterios.automation_id)
            )
        if criterios.class_name:
            condicoes.append(
                self.property_condition(U.UIA_ClassNamePropertyId, criterios.class_name)
            )
        if criterios.control_type:
            tipo_id = next(
                (cid for cid, nome in control_type_names().items()
                 if nome == criterios.control_type),
                None,
            )
            if tipo_id is not None:
                condicoes.append(self.property_condition(U.UIA_ControlTypePropertyId, tipo_id))
        if criterios.name and criterios.match == "exact":
            condicoes.append(self.property_condition(U.UIA_NamePropertyId, criterios.name))

        return self.and_conditions(*condicoes)

    def buscar_plano(self, hwnd: int, condicao: Any, *, teto: int = 5000) -> list[Any]:
        """FindAll cacheado dentro de uma janela. Busca PLANA — nao atravessa nada.

        Diferente do percurso da §5.2, aqui a condicao nativa e legitima: nao ha
        travessia a podar. Um unico RPC traz todos os candidatos ja com propriedades.
        """
        cr = self.build_cache_request(self.tree_props())
        raiz = self.element_from_handle(hwnd)
        try:
            achados = raiz.FindAllBuildCache(self.UIA.TreeScope_Descendants, condicao, cr)
        except Exception:
            return []
        return [achados.GetElement(i) for i in range(min(achados.Length, teto))]
```

- [ ] **Step 4: Implementar `uia_find_elements` em `server.py`**

Acrescentar ao fim de `server.py`:

```python
# --------------------------------------------------------------------------- 8.4


def _trilha_ancestral(automation: Any, elem: Any, *, niveis: int = 5) -> str:
    """Trilha legivel dos ancestrais, max 5 niveis (spec §8.4).

    Sobe pelo ControlViewWalker. Custa ate 5 RPCs por match, o que so vale porque
    max_results e pequeno — e evita uma segunda chamada do agente so para
    desambiguar dois botoes de mesmo nome (CA-24).
    """
    from .uia.core import control_type_name

    trilha: list[str] = []
    atual = elem
    for _ in range(niveis):
        try:
            pai = automation.control_walker.GetParentElement(atual)
        except Exception:
            break
        if not pai:
            break
        try:
            rotulo = control_type_name(pai.CurrentControlType)
            nome = pai.CurrentName or ""
        except Exception:
            break
        trilha.append(f"{rotulo}[{nome}]" if nome else rotulo)
        atual = pai
    return " > ".join(reversed(trilha))


def find_elements_impl(
    ctx: ServerContext,
    *,
    window_ref: str,
    criterios: "Criterios",
    only_interactive: bool,
    max_results: int,
) -> dict[str, Any]:
    """Corpo sincrono de uia_find_elements. Roda na thread do worker."""
    from .uia.filters import passa_no_filtro
    from .uia.search import casa_no_cliente

    from .refs import ElementIdentity
    from .uia.core import automation, control_type_name, pattern_availability_props
    from .uia.nodes import _cached, build_node
    from .uia.tree import _patterns_disponiveis

    inicio = time.perf_counter()
    criterios.validar()

    janela = resolver_janela(ctx, window_ref)
    a = automation()
    versao = ctx.refs.tree_version(window_ref)
    props_de_pattern = pattern_availability_props()

    # Busca PLANA com condicao nativa (§8.4). Um RPC, nao uma travessia — e o que
    # torna o poll do uia_wait_for barato o bastante para o CA-11.
    candidatos = a.buscar_plano(janela.hwnd, a.condicao_de_criterios(criterios))

    achados: list[dict[str, Any]] = []
    for elem in candidatos:
        node = build_node(
            elem, ref="", depth=0,
            patterns=_patterns_disponiveis(elem, props_de_pattern),
        )
        if only_interactive and not passa_no_filtro(node, "interactive"):
            continue
        if not casa_no_cliente(node, criterios):
            continue

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
        node["path"] = _trilha_ancestral(a, elem)
        node.pop("d", None)
        achados.append(node)
        if len(achados) >= max_results:
            break

    duracao = (time.perf_counter() - inicio) * 1000

    if not achados:
        ctx.audit.log_call(
            tool="uia_find_elements", result="not_found",
            params={"window_ref": window_ref}, duration_ms=duracao,
            read_only=ctx.policy.read_only,
        )
        raise ToolError(
            Code.ELEMENT_NOT_FOUND,
            "No element matched the given criteria in this window.",
            window_ref=window_ref,
            visited=len(candidatos),
        )

    ctx.audit.log_call(
        tool="uia_find_elements", result="ok",
        params={"window_ref": window_ref, "matches": len(achados)},
        duration_ms=duracao, read_only=ctx.policy.read_only,
    )

    return {
        "ok": True,
        "window_ref": window_ref,
        "matches": achados,
        "stats": {
            "returned": len(achados),
            "visited": len(candidatos),
            "exhaustive": len(candidatos) < 5000,
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
    when you already know what you are looking for."""
    from .uia.search import Criterios

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
```

Acrescentar ao topo do arquivo, junto aos outros imports:

```python
from .uia.search import Criterios
```

- [ ] **Step 5: Rodar os testes e2e**

Run: `.venv/Scripts/python.exe -m pytest tests/e2e/test_tool_find_elements.py -q`
Expected: PASS — 4 passed

- [ ] **Step 6: Commit**

```bash
git add src/mcp_windows_uia/uia/core.py src/mcp_windows_uia/server.py tests/e2e/test_tool_find_elements.py
git commit -m "feat(server): tool uia_find_elements com path de desambiguacao"
```

---

### Task 9: `uia/values.py` e tool `uia_get_value` (§8.5)

**Files:**
- Create: `src/mcp_windows_uia/uia/values.py`
- Modify: `src/mcp_windows_uia/server.py`
- Test: `tests/test_uia_values.py`
- Test: `tests/e2e/test_tool_get_value.py`

Aqui `Current*` **é** aceitável: é um elemento único, fora de laço.

- [ ] **Step 1: Escrever o teste unitário que falha**

`tests/test_uia_values.py`:

```python
from __future__ import annotations

import pytest

from mcp_windows_uia.errors import Code, ToolError
from mcp_windows_uia.uia.values import ORDEM_DE_LEITURA, ler_valor


class FonteFalsa:
    """Simula a cadeia de fontes: cada nome mapeia para um valor ou ausencia."""

    def __init__(self, **fontes):
        self.fontes = fontes
        self.consultadas: list[str] = []

    def tentar(self, nome: str):
        self.consultadas.append(nome)
        return self.fontes.get(nome)


def test_value_pattern_tem_prioridade() -> None:
    f = FonteFalsa(ValuePattern="texto", TextPattern="outro", Name="terceiro")
    valor, fonte = ler_valor(f, is_password=False)
    assert valor == "texto"
    assert fonte == "ValuePattern"


def test_cai_para_text_pattern() -> None:
    f = FonteFalsa(TextPattern="documento inteiro")
    valor, fonte = ler_valor(f, is_password=False)
    assert valor == "documento inteiro"
    assert fonte == "TextPattern"


def test_cai_para_name_em_ultimo_caso() -> None:
    f = FonteFalsa(Name="rotulo")
    valor, fonte = ler_valor(f, is_password=False)
    assert valor == "rotulo"
    assert fonte == "Name"


def test_ordem_de_consulta_e_a_da_spec() -> None:
    f = FonteFalsa()
    with pytest.raises(ToolError):
        ler_valor(f, is_password=False)
    assert f.consultadas == list(ORDEM_DE_LEITURA)


def test_senha_nunca_consulta_fonte_alguma() -> None:
    """CA-15: o valor de um campo de senha nao pode nem ser lido."""
    f = FonteFalsa(ValuePattern="s3nh4-secreta")
    valor, fonte = ler_valor(f, is_password=True)
    assert valor == "«redacted:password»"
    assert fonte == "redacted"
    assert f.consultadas == []


def test_nenhuma_fonte_disponivel_e_pattern_not_supported() -> None:
    f = FonteFalsa()
    with pytest.raises(ToolError) as exc:
        ler_valor(f, is_password=False)
    assert exc.value.code is Code.PATTERN_NOT_SUPPORTED


def test_toggle_state_vira_on_off() -> None:
    f = FonteFalsa(TogglePattern=1)
    valor, fonte = ler_valor(f, is_password=False)
    assert valor == "on"
    assert fonte == "TogglePattern"


def test_string_vazia_nao_conta_como_ausencia() -> None:
    """Um campo de texto legitimamente vazio deve devolver "", nao cair para Name."""
    f = FonteFalsa(ValuePattern="", Name="rotulo")
    valor, fonte = ler_valor(f, is_password=False)
    assert valor == ""
    assert fonte == "ValuePattern"
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_values.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.uia.values'`

- [ ] **Step 3: Implementar `uia/values.py`**

```python
"""Leitura do valor de um elemento. Spec §8.5.

Current* e aceitavel aqui: elemento unico, fora de laco. A cadeia de fontes e
consultada em ordem e a primeira que responde vence — o campo `source` no retorno
diz qual foi, para o agente saber o que esta lendo.
"""

from __future__ import annotations

from typing import Any

from ..errors import Code, ToolError
from .nodes import REDACTED

ORDEM_DE_LEITURA = (
    "ValuePattern",
    "TextPattern",
    "RangeValuePattern",
    "TogglePattern",
    "SelectionPattern",
    "LegacyIAccessible",
    "Name",
)

_TOGGLE_VAL = {0: "off", 1: "on", 2: "indeterminate"}


def ler_valor(fonte: Any, *, is_password: bool) -> tuple[Any, str]:
    """Devolve (valor, nome_da_fonte). Spec §8.5.

    `fonte` expoe .tentar(nome) -> valor ou None. Injetado para testar a cadeia
    sem COM; em producao e o adaptador sobre IUIAutomationElement.
    """
    if is_password:
        # CA-15: nem sequer consultamos. O valor nao pode existir em memoria aqui.
        return REDACTED, "redacted"

    for nome in ORDEM_DE_LEITURA:
        valor = fonte.tentar(nome)
        if valor is None:
            continue
        if nome == "TogglePattern":
            return _TOGGLE_VAL.get(valor, str(valor)), nome
        return valor, nome

    raise ToolError(
        Code.PATTERN_NOT_SUPPORTED,
        "This element exposes no readable value through any UI Automation pattern.",
        hint=(
            "Use uia_get_tree(root_ref=...) to inspect it, or uia_get_text on its "
            "container to read surrounding content."
        ),
    )
```

- [ ] **Step 4: Rodar os testes unitários**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_values.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: Implementar o adaptador COM e a tool em `server.py`**

Acrescentar ao fim de `server.py`:

```python
# --------------------------------------------------------------------------- 8.5


class FonteDeValorCOM:
    """Adaptador que traduz cada fonte da §8.5 numa consulta ao elemento vivo."""

    def __init__(self, elemento: Any, max_chars: int) -> None:
        self._e = elemento
        self._max = max_chars

    def tentar(self, nome: str) -> Any:
        from .uia.core import automation

        UIA = automation().UIA
        try:
            if nome == "ValuePattern":
                p = self._e.GetCurrentPattern(UIA.UIA_ValuePatternId)
                return p.QueryInterface(UIA.IUIAutomationValuePattern).CurrentValue if p else None
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
            # Pattern anunciado mas nao implementado acontece na pratica: segue a cadeia.
            return None
        return None


def get_value_impl(ctx: ServerContext, *, ref: str, max_chars: int) -> dict[str, Any]:
    """Corpo sincrono de uia_get_value. Roda na thread do worker."""
    from .budget import truncate_text
    from .uia.core import control_type_name
    from .uia.nodes import states_of
    from .uia.values import ler_valor

    inicio = time.perf_counter()
    entrada = ctx.refs.get(ref)
    janela = resolver_janela(ctx, entrada.window_ref)
    elemento = entrada.element

    is_password = bool(getattr(elemento, "CurrentIsPassword", 0))
    valor, fonte = ler_valor(FonteDeValorCOM(elemento, max_chars), is_password=is_password)

    truncado = False
    if isinstance(valor, str) and fonte != "redacted":
        valor, truncado, _ = truncate_text(valor, max_chars=max_chars)

    ctx.audit.log_call(
        tool="uia_get_value", result="ok",
        params={"ref": ref}, target={"process": janela.process, "pid": janela.pid},
        element={"ref": ref}, duration_ms=(time.perf_counter() - inicio) * 1000,
        read_only=ctx.policy.read_only,
    )

    return {
        "ok": True,
        "ref": ref,
        "rebound": False,
        "type": control_type_name(getattr(elemento, "CurrentControlType", 0)),
        "name": getattr(elemento, "CurrentName", "") or "",
        "value": valor,
        "source": fonte,
        "truncated": truncado,
        "st": states_of(elemento),
    }


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
```

- [ ] **Step 6: Escrever o teste e2e**

`tests/e2e/test_tool_get_value.py`:

```python
"""uia_get_value contra janela real. Cobre parte do CA-06."""

from __future__ import annotations

import pytest

from tests.e2e.test_tool_get_tree import notepad, servidor  # noqa: F401

pytestmark = pytest.mark.e2e


async def test_le_o_valor_da_area_de_edicao(servidor, notepad) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_find_elements, uia_get_value

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    achados = await uia_find_elements(window_ref=wref, control_type="Edit")
    ref = achados["matches"][0]["ref"]

    r = await uia_get_value(ref=ref)
    assert r["ok"] is True
    assert "value" in r
    assert r["source"] in ("ValuePattern", "TextPattern", "LegacyIAccessible", "Name")


async def test_ref_desconhecida_e_erro_acionavel(servidor) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_get_value

    r = await uia_get_value(ref="w1-e999999")
    assert r["ok"] is False
    assert r["error"]["code"] in ("REF_NOT_FOUND", "STALE_REF")
    assert "uia_get_tree" in r["error"]["hint"] or "uia_find_elements" in r["error"]["hint"]
```

- [ ] **Step 7: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_values.py tests/e2e/test_tool_get_value.py -q`
Expected: PASS — 10 passed

- [ ] **Step 8: Commit**

```bash
git add src/mcp_windows_uia/uia/values.py src/mcp_windows_uia/server.py tests/test_uia_values.py tests/e2e/test_tool_get_value.py
git commit -m "feat(server): tool uia_get_value com cadeia de fontes da spec 8.5"
```

---

### Task 10: `uia/text.py` e tool `uia_get_text` (§8.3)

**Files:**
- Create: `src/mcp_windows_uia/uia/text.py`
- Modify: `src/mcp_windows_uia/server.py`
- Test: `tests/test_uia_text.py`
- Test: `tests/e2e/test_tool_get_text.py`

O ponto difícil é a **deduplicação**: se um ancestral com `TextPattern` já cobriu o texto dos descendentes, não repetir (CA-12).

- [ ] **Step 1: Escrever o teste unitário que falha**

`tests/test_uia_text.py`:

```python
from __future__ import annotations

from mcp_windows_uia.uia.text import montar_texto


def no(d, tipo="Text", nome="", val=None, tem_text_pattern=False):
    n = {"d": d, "type": tipo, "name": nome, "st": [], "pat": []}
    if val is not None:
        n["val"] = val
    if tem_text_pattern:
        n["pat"] = ["Text"]
    return n


def test_concatena_em_ordem_de_documento() -> None:
    nodes = [no(0, "Window", "Janela"), no(1, "Text", "primeira"), no(1, "Text", "segunda")]
    texto = montar_texto(nodes, textos_de_pattern={})
    assert texto.index("primeira") < texto.index("segunda")


def test_separa_blocos_por_quebra_de_linha() -> None:
    nodes = [no(1, "Text", "a"), no(1, "Text", "b")]
    assert montar_texto(nodes, textos_de_pattern={}) == "a\nb"


def test_ancestral_com_text_pattern_suprime_descendentes() -> None:
    """CA-12: sem duplicacao de conteudo por ancestral/descendente."""
    nodes = [
        no(0, "Document", "doc", tem_text_pattern=True),
        no(1, "Text", "linha um"),
        no(1, "Text", "linha dois"),
    ]
    texto = montar_texto(nodes, textos_de_pattern={0: "linha um\nlinha dois"})
    assert texto.count("linha um") == 1
    assert texto.count("linha dois") == 1


def test_irmao_apos_o_bloco_do_ancestral_volta_a_contar() -> None:
    """A supressao vale so para descendentes, nao para o resto da arvore."""
    nodes = [
        no(0, "Document", "doc", tem_text_pattern=True),
        no(1, "Text", "dentro"),
        no(0, "Text", "fora"),
    ]
    texto = montar_texto(nodes, textos_de_pattern={0: "dentro"})
    assert "fora" in texto


def test_usa_valor_quando_nao_ha_nome() -> None:
    nodes = [no(1, "Edit", "", val="digitado")]
    assert montar_texto(nodes, textos_de_pattern={}) == "digitado"


def test_ignora_nos_sem_texto_algum() -> None:
    nodes = [no(1, "Pane", ""), no(1, "Text", "unico")]
    assert montar_texto(nodes, textos_de_pattern={}) == "unico"


def test_senha_aparece_redigida() -> None:
    n = no(1, "Edit", "Senha", val="«redacted:password»")
    n["st"] = ["password"]
    assert "«redacted:password»" in montar_texto([n], textos_de_pattern={})


def test_lista_vazia_devolve_string_vazia() -> None:
    assert montar_texto([], textos_de_pattern={}) == ""
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_text.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.uia.text'`

- [ ] **Step 3: Implementar `uia/text.py`**

```python
"""Extracao de texto linear. Spec §8.3.

A parte que importa e a deduplicacao: quando um ancestral expoe TextPattern, o
texto dele ja cobre os descendentes. Emitir os dois duplica tudo — e o CA-12
falha exatamente nisso.
"""

from __future__ import annotations

from typing import Any


def _texto_do_no(node: dict[str, Any]) -> str:
    """Nome, ou valor quando o nome e vazio."""
    nome = (node.get("name") or "").strip()
    if nome:
        return nome
    valor = node.get("val")
    if valor in (None, ""):
        return ""
    return str(valor).strip()


def montar_texto(
    nodes: list[dict[str, Any]], *, textos_de_pattern: dict[int, str]
) -> str:
    """Junta os nos em texto linear, sem duplicar ancestral/descendente.

    `textos_de_pattern` mapeia indice do no -> texto do TextPattern dele. Quando
    um no tem entrada ali, o texto dele e emitido e toda a subarvore e pulada.
    """
    blocos: list[str] = []
    suprimir_ate_profundidade: int | None = None

    for i, node in enumerate(nodes):
        profundidade = node.get("d", 0)

        # Saiu da subarvore que estava suprimida?
        if suprimir_ate_profundidade is not None:
            if profundidade > suprimir_ate_profundidade:
                continue
            suprimir_ate_profundidade = None

        do_pattern = textos_de_pattern.get(i)
        if do_pattern is not None:
            limpo = do_pattern.strip()
            if limpo:
                blocos.append(limpo)
            suprimir_ate_profundidade = profundidade
            continue

        texto = _texto_do_no(node)
        if texto:
            blocos.append(texto)

    return "\n".join(blocos)
```

- [ ] **Step 4: Rodar os testes unitários**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_text.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: Implementar a tool em `server.py`**

Acrescentar ao fim de `server.py`:

```python
# --------------------------------------------------------------------------- 8.3


def get_text_impl(
    ctx: ServerContext, *, window_ref: str, max_chars: int, offset: int
) -> dict[str, Any]:
    """Corpo sincrono de uia_get_text. Roda na thread do worker."""
    from .budget import truncate_text
    from .uia.text import montar_texto

    inicio = time.perf_counter()
    janela = resolver_janela(ctx, window_ref)

    arvore = get_tree_impl(
        ctx, window_ref=window_ref, filtro="content", max_depth=None,
        max_nodes=1500, max_children_per_node=500, verbose=False,
    )
    nodes = arvore["nodes"]

    # O TextPattern de cada no e resolvido pelo ref registrado no store.
    textos_de_pattern: dict[int, str] = {}
    for i, node in enumerate(nodes):
        if "Text" not in node.get("pat", ()):
            continue
        try:
            entrada = ctx.refs.get(node["ref"])
        except ToolError:
            continue
        bruto = FonteDeValorCOM(entrada.element, max_chars).tentar("TextPattern")
        if bruto:
            textos_de_pattern[i] = bruto

    completo = montar_texto(nodes, textos_de_pattern=textos_de_pattern)
    fatia, truncado, proximo = truncate_text(completo, max_chars=max_chars, offset=offset)

    ctx.audit.log_call(
        tool="uia_get_text", result="ok",
        params={"window_ref": window_ref, "chars": len(fatia)},
        target={"process": janela.process, "pid": janela.pid},
        duration_ms=(time.perf_counter() - inicio) * 1000,
        read_only=ctx.policy.read_only,
    )

    return {
        "ok": True,
        "window_ref": window_ref,
        "text": fatia,
        "truncated": truncado,
        "chars": len(fatia),
        "next_offset": proximo,
    }


@mcp.tool()
@tool_errors
async def uia_get_text(
    window_ref: Annotated[str, Field(description="Window ref from uia_list_windows.")],
    max_chars: Annotated[int, Field(ge=1, le=40000)] = 6000,
    offset: Annotated[int, Field(ge=0, description="Continue from character N.")] = 0,
) -> dict[str, Any]:
    """Extract the readable text content of a window as linear text, in reading order.
    Use this to read a document, dialog message, list contents or status bar without
    dumping the full tree."""
    ctx = context()
    return await ctx.worker.run(
        lambda: get_text_impl(ctx, window_ref=window_ref, max_chars=max_chars, offset=offset)
    )
```

- [ ] **Step 6: Escrever o teste e2e**

`tests/e2e/test_tool_get_text.py`:

```python
"""uia_get_text contra janela real. Cobre CA-12."""

from __future__ import annotations

import pytest

from tests.e2e.test_tool_get_tree import notepad, servidor  # noqa: F401

pytestmark = pytest.mark.e2e


async def test_ca12_chars_bate_com_o_tamanho_do_texto(servidor, notepad) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_get_text

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    r = await uia_get_text(window_ref=wref)

    assert r["ok"] is True
    assert r["chars"] == len(r["text"])


async def test_ca12_sem_duplicacao_de_bloco(servidor, notepad) -> None:  # noqa: F811
    """Um mesmo bloco de texto nao pode aparecer duas vezes seguidas."""
    from mcp_windows_uia.server import uia_get_text

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    r = await uia_get_text(window_ref=wref)

    linhas = [linha for linha in r["text"].split("\n") if linha.strip()]
    for i in range(1, len(linhas)):
        assert linhas[i] != linhas[i - 1], f"bloco duplicado: {linhas[i]!r}"


async def test_offset_continua_de_onde_parou(servidor, notepad) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_get_text

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    inteiro = await uia_get_text(window_ref=wref)
    if len(inteiro["text"]) < 10:
        pytest.skip("texto curto demais para paginar")

    primeiro = await uia_get_text(window_ref=wref, max_chars=5)
    assert primeiro["truncated"] is True
    segundo = await uia_get_text(window_ref=wref, max_chars=5, offset=primeiro["next_offset"])
    assert segundo["text"] != primeiro["text"]
```

- [ ] **Step 7: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_text.py tests/e2e/test_tool_get_text.py -q`
Expected: PASS — 11 passed

- [ ] **Step 8: Commit**

```bash
git add src/mcp_windows_uia/uia/text.py src/mcp_windows_uia/server.py tests/test_uia_text.py tests/e2e/test_tool_get_text.py
git commit -m "feat(server): tool uia_get_text com dedup de ancestral, cobre CA-12"
```

---

### Task 11: Tool `uia_wait_for` (§8.6)

**Files:**
- Modify: `src/mcp_windows_uia/server.py`
- Test: `tests/e2e/test_tool_wait_for.py`

- [ ] **Step 1: Escrever o teste e2e que falha**

`tests/e2e/test_tool_wait_for.py`:

```python
"""uia_wait_for contra janela real. Cobre CA-11."""

from __future__ import annotations

import pytest

from tests.e2e.test_tool_get_tree import notepad, servidor  # noqa: F401

pytestmark = pytest.mark.e2e


async def test_elemento_ja_presente_satisfaz_rapido(servidor, notepad) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_wait_for

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    r = await uia_wait_for(window_ref=wref, condition="appears", control_type="Edit",
                           timeout_ms=3000)

    assert r["ok"] is True
    assert r["satisfied"] is True
    assert r["waited_ms"] < 3000
    assert "ref" in r["match"]


async def test_ca11_timeout_acionavel(servidor, notepad) -> None:  # noqa: F811
    """CA-11: TIMEOUT em 1500-2500 ms, hint sugerindo uia_get_tree, polls > 5."""
    from mcp_windows_uia.server import uia_wait_for

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    r = await uia_wait_for(window_ref=wref, condition="appears",
                           name="elemento-inexistente-xyz", timeout_ms=1500)

    assert r["ok"] is False
    assert r["error"]["code"] == "TIMEOUT"
    assert "uia_get_tree" in r["error"]["hint"]
    assert r["error"]["details"]["polls"] > 5
    assert 1500 <= r["error"]["details"]["waited_ms"] < 2500


async def test_condicao_desconhecida_e_invalid_argument(servidor, notepad) -> None:  # noqa: F811
    from mcp_windows_uia.server import uia_wait_for

    wref = servidor.refs.window_ref(hwnd=notepad.hwnd)
    r = await uia_wait_for(window_ref=wref, condition="faz_cafe", timeout_ms=500)

    assert r["ok"] is False
    assert r["error"]["code"] == "INVALID_ARGUMENT"
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/e2e/test_tool_wait_for.py -q`
Expected: FAIL — `ImportError: cannot import name 'uia_wait_for'`

- [ ] **Step 3: Implementar `uia_wait_for` em `server.py`**

Acrescentar ao fim de `server.py`:

```python
# --------------------------------------------------------------------------- 8.6

CONDICOES = ("appears", "disappears", "enabled", "focused", "value_contains")


def wait_for_impl(
    ctx: ServerContext,
    *,
    window_ref: str,
    condition: str,
    criterios: Criterios,
    expected: str | None,
    timeout_ms: int,
    poll_ms: int,
) -> dict[str, Any]:
    """Corpo sincrono de uia_wait_for. Roda na thread do worker."""
    from .uia.waits import esperar_por

    if condition not in CONDICOES:
        raise ToolError(
            Code.INVALID_ARGUMENT,
            f"Unknown condition {condition!r}. Valid values: {', '.join(CONDICOES)}.",
            condition=condition,
        )
    if condition == "value_contains" and not expected:
        raise ToolError(
            Code.INVALID_ARGUMENT,
            "condition='value_contains' requires `expected`.",
        )

    inicio = time.perf_counter()
    resolver_janela(ctx, window_ref)
    criterios.validar()

    def sondar() -> dict[str, Any] | None:
        """Uma amostra. Devolve o no que satisfaz, ou None."""
        try:
            achados = find_elements_impl(
                ctx, window_ref=window_ref, criterios=criterios,
                only_interactive=False, max_results=5,
            )
        except ToolError as exc:
            if exc.code is Code.ELEMENT_NOT_FOUND:
                # "sumiu" e sucesso quando a condicao e disappears.
                return {"gone": True} if condition == "disappears" else None
            raise

        candidatos = achados["matches"]
        if condition == "disappears":
            return None
        if condition == "appears":
            return candidatos[0]
        if condition == "enabled":
            return next((c for c in candidatos if "disabled" not in c["st"]), None)
        if condition == "focused":
            return next((c for c in candidatos if "focused" in c["st"]), None)
        alvo = (expected or "").casefold()
        return next((c for c in candidatos if alvo in str(c.get("val", "")).casefold()), None)

    r = esperar_por(
        sondar, timeout_ms=timeout_ms, poll_ms=poll_ms,
        descricao=f"{condition} ({criterios.name or criterios.control_type})",
    )

    ctx.audit.log_call(
        tool="uia_wait_for", result="ok",
        params={"window_ref": window_ref, "condition": condition},
        duration_ms=(time.perf_counter() - inicio) * 1000,
        read_only=ctx.policy.read_only,
    )

    return {
        "ok": True,
        "condition": condition,
        "satisfied": True,
        "waited_ms": round(r.waited_ms, 1),
        "polls": r.polls,
        "match": r.resultado,
    }


@mcp.tool()
@tool_errors
async def uia_wait_for(
    window_ref: Annotated[str, Field(description="Window ref from uia_list_windows.")],
    condition: Annotated[
        str,
        Field(description="appears | disappears | enabled | focused | value_contains"),
    ] = "appears",
    name: Annotated[str | None, Field()] = None,
    automation_id: Annotated[str | None, Field()] = None,
    control_type: Annotated[str | None, Field()] = None,
    match: Annotated[str, Field(description="exact | contains | starts_with | regex")] = "contains",
    expected: Annotated[str | None, Field(description="Required for value_contains.")] = None,
    timeout_ms: Annotated[int, Field(ge=100, le=60000)] = 5000,
    poll_ms: Annotated[int, Field(ge=50, le=1000)] = 100,
) -> dict[str, Any]:
    """Poll until a UI condition holds: an element appears, disappears, becomes enabled,
    gets focus, or its value matches. Use after an action that triggers async rendering
    instead of guessing with a fixed sleep."""
    ctx = context()
    criterios = Criterios(
        name=name, automation_id=automation_id, control_type=control_type, match=match
    )
    return await ctx.worker.run(
        lambda: wait_for_impl(
            ctx, window_ref=window_ref, condition=condition, criterios=criterios,
            expected=expected, timeout_ms=timeout_ms, poll_ms=poll_ms,
        )
    )
```

- [ ] **Step 4: Rodar os testes e2e**

Run: `.venv/Scripts/python.exe -m pytest tests/e2e/test_tool_wait_for.py -q`
Expected: PASS — 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_windows_uia/server.py tests/e2e/test_tool_wait_for.py
git commit -m "feat(server): tool uia_wait_for com backoff, cobre CA-11"
```

---

### Task 12: Ligar o rebind no `resolve` do RefStore (CA-06 e CA-07)

**Files:**
- Modify: `src/mcp_windows_uia/refs.py`
- Modify: `src/mcp_windows_uia/server.py`
- Test: `tests/e2e/test_ca06_ca07_refs.py`

Fecha o ciclo: as refs entregues ao agente sobrevivem a re-render, e quando não sobrevivem o erro é acionável.

- [ ] **Step 1: Escrever o teste e2e que falha**

`tests/e2e/test_ca06_ca07_refs.py`:

```python
"""Ciclo de vida de refs contra janelas reais. CA-06 e CA-07."""

from __future__ import annotations

import subprocess
import time

import pytest

from tests.e2e.test_tool_get_tree import servidor  # noqa: F401

pytestmark = pytest.mark.e2e


async def test_ca06_ref_de_janela_fechada_e_erro_acionavel(servidor) -> None:  # noqa: F811
    """CA-06: STALE_REF ou WINDOW_CLOSED, sem traceback, servidor segue vivo."""
    from mcp_windows_uia.server import uia_find_elements, uia_get_value
    from mcp_windows_uia.uia.windows import enumerate_windows

    proc = subprocess.Popen(["notepad.exe"])
    janela = None
    for _ in range(50):
        time.sleep(0.2)
        c = [w for w in enumerate_windows() if w.pid == proc.pid and w.title]
        if c:
            janela = c[0]
            break
    assert janela is not None

    wref = servidor.refs.window_ref(hwnd=janela.hwnd)
    achados = await uia_find_elements(window_ref=wref, control_type="Edit")
    ref = achados["matches"][0]["ref"]

    proc.terminate()
    proc.wait(timeout=10)
    time.sleep(0.5)

    r = await uia_get_value(ref=ref)
    assert r["ok"] is False
    assert r["error"]["code"] in ("STALE_REF", "WINDOW_CLOSED")
    assert "uia_list_windows" in r["error"]["hint"] or "uia_get_tree" in r["error"]["hint"]

    # O servidor continua respondendo depois do erro.
    from mcp_windows_uia.server import uia_list_windows

    seguinte = await uia_list_windows()
    assert seguinte["ok"] is True


async def test_ca07_rebind_apos_rerender(servidor) -> None:  # noqa: F811
    """CA-07: ref sobrevive a re-render, com rebound=true e mesmo item logico."""
    from mcp_windows_uia.server import uia_find_elements, uia_get_tree, uia_get_value
    from mcp_windows_uia.uia.windows import enumerate_windows

    explorer = [w for w in enumerate_windows() if w.process.lower() == "explorer.exe" and w.title]
    if not explorer:
        pytest.skip("nenhuma janela do Explorador aberta")

    wref = servidor.refs.window_ref(hwnd=explorer[0].hwnd)

    achados = await uia_find_elements(window_ref=wref, control_type="ListItem")
    if not achados.get("ok"):
        pytest.skip("nenhum ListItem no Explorador")

    alvo = achados["matches"][0]
    ref, nome_original = alvo["ref"], alvo["name"]

    # Re-captura forca nova arvore; o elemento pode ter sido recriado.
    await uia_get_tree(window_ref=wref, filter="all", max_nodes=500)

    r = await uia_get_value(ref=ref)
    assert r["ok"] is True
    assert r["name"] == nome_original
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/e2e/test_ca06_ca07_refs.py -q`
Expected: FAIL — `uia_get_value` devolve `UIA_COM_ERROR` em vez de `STALE_REF`, porque o probe da §7.2 ainda não existe.

- [ ] **Step 3: Implementar `resolve` em `refs.py`**

Acrescentar ao fim da classe `RefStore` em `src/mcp_windows_uia/refs.py`:

```python
    def resolve(self, ref: str, *, automation: Any) -> tuple[Any, bool]:
        """Ref -> (elemento vivo, rebound). Spec §7.2, algoritmo completo.

        `automation` e injetado para o store nao importar COM: quem sabe falar com
        a UIA e a camada de cima. Devolve rebound=True quando o elemento teve de
        ser reencontrado — o agente precisa saber que a arvore mudou sob os pes.
        """
        import ctypes

        from .rebind import rebind

        entrada = self.get(ref)  # ja trata REF_NOT_FOUND e TTL

        if not ctypes.windll.user32.IsWindow(entrada.hwnd):
            self.invalidate_window(entrada.window_ref)
            raise ToolError(
                Code.WINDOW_CLOSED,
                f"The window owning {ref} was closed.",
                ref=ref,
                window_ref=entrada.window_ref,
            )

        # Probe barato: se o ponteiro ainda responde e o RuntimeId bate, acabou.
        try:
            _ = entrada.element.CurrentProcessId
            if tuple(entrada.element.GetRuntimeId()) == entrada.runtime_id:
                return entrada.element, False
        except Exception:
            pass

        elemento, _estrategia = rebind(
            automation, hwnd=entrada.hwnd, identity=entrada.identity
        )
        entrada.element = elemento
        entrada.runtime_id = tuple(automation.runtime_id_of(elemento))
        self.touch(entrada)
        return elemento, True
```

Acrescentar `from typing import Any` aos imports de `refs.py` se ainda não estiver lá.

- [ ] **Step 4: Implementar `buscar` no `Automation` de `uia/core.py`**

Acrescentar à classe `Automation` em `src/mcp_windows_uia/uia/core.py`:

```python
    def buscar(self, hwnd: int, identity: Any, estrategia: Any) -> list[Any]:
        """Candidatos para uma estrategia de rebind. Spec §7.2 passo 5.

        Usa condicao nativa: a busca e plana dentro da janela, entao o provider
        pode filtrar. Retorna lista para o chamador decidir sobre ambiguidade —
        escolher sozinho violaria o principio 4 da spec.
        """
        from ..rebind import Estrategia

        U = self.UIA
        raiz = self.element_from_handle(hwnd)
        control_type_id = next(
            (cid for cid, nome in control_type_names().items() if nome == identity.control_type),
            None,
        )

        if estrategia is Estrategia.AUTOMATION_ID:
            condicoes = [self.property_condition(U.UIA_AutomationIdPropertyId, identity.automation_id)]
        elif estrategia is Estrategia.NAME:
            condicoes = [self.property_condition(U.UIA_NamePropertyId, identity.name)]
            if identity.class_name:
                condicoes.append(
                    self.property_condition(U.UIA_ClassNamePropertyId, identity.class_name)
                )
        else:
            return self._por_index_path(raiz, identity)

        if control_type_id is not None:
            condicoes.append(self.property_condition(U.UIA_ControlTypePropertyId, control_type_id))

        return self.buscar_plano(hwnd, self.and_conditions(*condicoes))

    def _por_index_path(self, raiz: Any, identity: Any) -> list[Any]:
        """Ultimo recurso: desce pelo caminho de indices na ControlView."""
        atual = raiz
        for indice in identity.index_path:
            try:
                filhos = atual.FindAll(self.UIA.TreeScope_Children, self.true_condition)
            except Exception:
                return []
            if indice >= filhos.Length:
                return []
            atual = filhos.GetElement(indice)

        if identity.control_type:
            achado_tipo = control_type_name(atual.CurrentControlType)
            if achado_tipo != identity.control_type:
                return []
        return [atual]
```

- [ ] **Step 5: Usar `resolve` em `get_value_impl`**

Em `src/mcp_windows_uia/server.py`, dentro de `get_value_impl`, substituir:

```python
    entrada = ctx.refs.get(ref)
    janela = resolver_janela(ctx, entrada.window_ref)
    elemento = entrada.element
```

por:

```python
    from .uia.core import automation

    entrada = ctx.refs.get(ref)
    janela = resolver_janela(ctx, entrada.window_ref)
    elemento, rebound = ctx.refs.resolve(ref, automation=automation())
```

E no dict de retorno, trocar `"rebound": False` por `"rebound": rebound`.

- [ ] **Step 6: Rodar os testes e2e**

Run: `.venv/Scripts/python.exe -m pytest tests/e2e/test_ca06_ca07_refs.py -q`
Expected: PASS — 2 passed (ou 1 passed 1 skipped se não houver Explorador aberto)

- [ ] **Step 7: Rodar a suíte inteira**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — nenhuma regressão

- [ ] **Step 8: Commit**

```bash
git add src/mcp_windows_uia/refs.py src/mcp_windows_uia/uia/core.py src/mcp_windows_uia/server.py tests/e2e/test_ca06_ca07_refs.py
git commit -m "feat(refs): resolve com probe e rebind, cobre CA-06 e CA-07"
```

---

### Task 13: Verificação final do plano

**Files:**
- Nenhum arquivo novo. Verificação.

- [ ] **Step 1: Suíte completa verde**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS, zero falhas

- [ ] **Step 2: As seis tools aparecem no `tools/list`**

Run: `.venv/Scripts/python.exe -c "from mcp_windows_uia.server import mcp; import asyncio; print(sorted(t.name for t in asyncio.run(mcp.list_tools())))"`
Expected: `['uia_find_elements', 'uia_get_text', 'uia_get_tree', 'uia_get_value', 'uia_list_windows', 'uia_wait_for']`

- [ ] **Step 3: Higiene de stdout preservada (CA-22)**

Run: `.venv/Scripts/python.exe -m pytest tests/e2e/test_ca01_ca22.py -q`
Expected: PASS

- [ ] **Step 4: Fumaça contra o WhatsApp — o caso que motivou o projeto**

Run: `.venv/Scripts/python.exe poc/dump_window_tree.py --process whatsapp`
Expected: veredito "ARVORE TRAZ O CONTEUDO", confirmando que a captura corrigida ainda enxerga o WebView2

- [ ] **Step 5: Commit final se houver ajuste**

```bash
git add -A
git commit -m "chore: fecha o plano 2 com as seis tools de leitura verdes"
```

---

## O que este plano deliberadamente NÃO faz

- **Paginação por cursor completa (CA-08).** `stats.next_cursor` é emitido como `None`. `budget.py` já tem `encode_cursor`/`decode_cursor` testados; ligar o cursor ao estado de `fila_restante` do percurso é trabalho de meia hora que depende de a captura estar estável primeiro. O teste do CA-08 já aceita ambos os caminhos.
- **Condição nativa em `uia_find_elements`.** Hoje ele reusa a captura e filtra no cliente. Correção antes de otimização: a otimização da §8.4 só faz sentido depois de haver um teste de performance que a justifique.
- **Tools que mutam estado.** `uia_click`, `uia_set_value`, `uia_send_keys`, `uia_scroll`, `uia_focus_window` são o Plano 3, junto com `uia/patterns.py` e o fallback de coordenada.
- **Camada de aprendizado de seletores por app.** Discutida e adiada de propósito. O gancho está posto: `rebind()` recebe `ElementIdentity` e não o `RefStore`, e `identity_to_dict`/`identity_from_dict` já persistem em JSON puro. A camada precisa de brainstorming próprio — a decisão difícil é a chave de busca, não o armazenamento.
