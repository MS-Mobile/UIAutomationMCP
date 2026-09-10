# Especificação Técnica — `mcp-windows-uia`

**Servidor MCP local (Python) que expõe a UI Automation do Windows a um agente Claude**

| Campo | Valor |
|---|---|
| Versão da spec | 1.0 |
| Data | 2026-08-19 |
| Público-alvo | Agente de IA implementador (autossuficiente) |
| Nome do pacote | `mcp-windows-uia` |
| Nome do servidor MCP | `windows-uia` |
| Prefixo das ferramentas | `uia_` |
| Transporte | stdio (JSON-RPC 2.0) |
| Plataforma | Windows 10 (1809+) e Windows 11, x64 |
| Runtime | Python 3.14 x64 (piso suportado: 3.11) |

**Convenção de idioma:** este documento é em português. A *superfície de API* (nomes de ferramentas, chaves JSON, códigos e mensagens de erro, descrições das tools expostas via MCP) é em **inglês**, porque é consumida por um LLM e deve seguir a convenção do ecossistema MCP. Não traduza a API.

---

## 1. Contexto e motivação

Hoje o agente Claude opera aplicativos nativos do Windows exclusivamente por **screenshot + clique por coordenada**. Isso implica:

- **Lento**: cada passo custa uma captura de tela (imagem grande no contexto) e uma rodada de raciocínio visual.
- **Frágil**: coordenadas quebram com scroll, redimensionamento, mudança de tema, DPI scaling, resolução diferente ou reordenação de itens.
- **Cego a estado**: o agente não sabe se um botão está desabilitado, se um checkbox está marcado, qual o valor real de um campo, ou se há conteúdo fora da área visível.
- **Sem texto confiável**: ler conteúdo depende de OCR implícito sobre o screenshot.

Dentro do Google Chrome o agente já tem uma experiência qualitativamente superior: ele lê a **árvore de acessibilidade / DOM** via extensão, recebe elementos com **referência estável (`ref`)**, filtra por `filter: "interactive"` e clica por referência — sem coordenadas.

**Objetivo deste servidor:** levar exatamente essa qualidade para aplicativos nativos do Windows, usando a árvore de **UI Automation (UIA)** — a mesma API que leitores de tela como o NVDA e o Narrator consomem. O modelo mental é: *"Chrome DevTools/a11y tree, mas para o desktop inteiro."*

**Princípios de projeto (invioláveis):**

1. **Semântica antes de pixel.** Toda interação tenta primeiro um *control pattern* da UIA (`Invoke`, `Toggle`, `SelectionItem`, `ExpandCollapse`, `Value`, `Scroll`). Clique por coordenada é *fallback* explícito e sempre reportado no retorno.
2. **Orçamento de contexto é recurso escasso.** Nenhuma ferramenta pode retornar árvore completa por padrão. Truncamento, filtragem e paginação são obrigatórios, não opcionais.
3. **Erros são instruções.** Toda falha retorna um `code` estável e um `hint` que diz ao agente **qual é a próxima ação concreta**.
4. **Determinismo sobre heurística.** Nada de "best match" fuzzy silencioso; ambiguidade vira erro `AMBIGUOUS_MATCH` com os candidatos listados.
5. **O servidor controla o desktop inteiro.** Allowlist, modo somente-leitura e auditoria são parte do produto mínimo, não features futuras.

**Fora de escopo da v1:** automação de apps Win32 legados sem suporte a UIA via MSAA bridge avançada; gravação de macros; assinatura `uiAccess=true`; subscrição de eventos UIA (ver §7.4); suporte a UWP em sessão remota; OCR.

---

## 2. Arquitetura

```
┌──────────────────────┐        stdio (JSON-RPC)        ┌────────────────────────────┐
│  Claude Desktop      │ ─────────────────────────────► │  mcp-windows-uia (Python)  │
│  (cliente MCP)       │ ◄───────────────────────────── │                            │
└──────────────────────┘                                │  ┌──────────────────────┐  │
                                                        │  │ camada MCP (MCPServer)│  │
                                                        │  ├──────────────────────┤  │
                                                        │  │ policy: allowlist,   │  │
                                                        │  │ read-only, audit     │  │
                                                        │  ├──────────────────────┤  │
                                                        │  │ RefStore (cache de   │  │
                                                        │  │ refs por sessão)     │  │
                                                        │  ├──────────────────────┤  │
                                                        │  │ UiaWorker (thread    │  │
                                                        │  │ STA única, COM)      │  │
                                                        │  └──────────┬───────────┘  │
                                                        └─────────────┼──────────────┘
                                                                      ▼
                                                         UIAutomationCore.dll (COM)
                                                                      ▼
                                                        apps nativos (Win32/WinForms/
                                                        WPF/WinUI3/UWP/Electron)
```

### 2.1 Estrutura de módulos

```
mcp-windows-uia/
├─ pyproject.toml
├─ config.toml                  # allowlist, flags, limites
├─ src/mcp_windows_uia/
│  ├─ __main__.py               # entrypoint, CLI args, DPI awareness, mcp.run(stdio)
│  ├─ server.py                 # definição das tools (MCPServer, mcp>=2.0)
│  ├─ worker.py                 # UiaWorker: thread STA única + CoInitializeEx
│  ├─ uia/
│  │  ├─ core.py                # CUIAutomation8/IUIAutomation6, CacheRequest, TreeWalker, condições
│  │  ├─ tree.py                # captura/filtragem/serialização da árvore
│  │  ├─ patterns.py            # invoke/toggle/select/expand/value/scroll + fallback
│  │  ├─ windows.py             # enumeração de top-level windows
│  │  └─ waits.py               # polling, timeouts, settle
│  ├─ refs.py                   # RefStore, revalidação, STALE_REF
│  ├─ policy.py                 # allowlist, read-only, rate limit, redação
│  ├─ audit.py                  # log JSONL
│  ├─ errors.py                 # ToolError, códigos, hints
│  └─ budget.py                 # truncamento, paginação, cursores
└─ tests/
   ├─ test_refs.py
   ├─ test_budget.py
   └─ e2e/                      # cenários de aceitação (§12)
```

### 2.2 Modelo de threading (crítico)

A UIA é COM. Objetos `IUIAutomationElement` têm **afinidade de apartamento**. O servidor MCP roda em loop asyncio. Regra obrigatória:

- **Todas** as chamadas COM acontecem em **uma única thread dedicada** inicializada com `CoInitializeEx(None, COINIT_APARTMENTTHREADED)` (STA).
- As tools async fazem `await loop.run_in_executor(uia_executor, fn)` sobre um `ThreadPoolExecutor(max_workers=1)`.
- Ponteiros COM **nunca** cruzam para outra thread. O `RefStore` guarda os ponteiros, mas só a UiaWorker os desreferencia.
- Timeout global por chamada COM: 15 s (`UIA_COM_TIMEOUT_MS`). Se estourar, retorna `TIMEOUT` — nunca deixa o servidor pendurado (apps travados fazem chamadas UIA bloquear indefinidamente).

```python
# worker.py (esqueleto obrigatório)
from concurrent.futures import ThreadPoolExecutor
import comtypes

def _init_sta() -> None:
    comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)

UIA_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="uia", initializer=_init_sta
)
```

Instanciar **obrigatoriamente** `CUIAutomation8` (Windows 8+) via `comtypes.client.CreateObject(UIA.CUIAutomation8, interface=UIA.IUIAutomation6)` e configurar `ConnectionTimeout` / `TransactionTimeout` = 10000 ms. Isso evita travamento quando o app-alvo não bombeia mensagens, e é o que torna **CA-23** implementável. Verificado funcionando em 2026-08-19 (§3.1); o `CUIAutomation` legado **não** expõe essas propriedades.

---

## 3. Escolha de biblioteca Python

### 3.1 Evidência empírica (medida em 2026-08-19, Windows 11 26200, Python 3.14.6 x64)

A decisão abaixo foi tomada contra medição, não contra reputação. Quatro provas de conceito:

| Verificação | Resultado |
|---|---|
| `comtypes.client.GetModule("UIAutomationCore.dll")` | Gera **626 símbolos** em 142 ms: 175 `*PropertyId`, 41 `*ControlTypeId`, 32 `*PatternId`, 32 `Is*PatternAvailablePropertyId`, todas as `TreeScope_*`/`AutomationElementMode_*`, todas as interfaces `IUIAutomation`…`IUIAutomation6` e o coclass `CUIAutomation8` |
| `uiautomation` → `QueryInterface(IUIAutomation2..6)` | **Falha em todas.** A lib instancia `CUIAutomation` (legado), logo `ConnectionTimeout`/`TransactionTimeout` exigidos pela §2.2 são inalcançáveis pelo cliente dela |
| `CreateObject(CUIAutomation8, interface=IUIAutomation6)` via comtypes | OK. `ConnectionTimeout = 10000` e `TransactionTimeout = 10000` aceitos e lidos de volta |
| `FindAllBuildCache` + `CacheRequest` com 11 propriedades | 27 nós com todas as propriedades cacheadas lidas em ~130 ms, zero RPC adicional por propriedade |

### 3.2 Saúde das dependências candidatas

| | `uiautomation` (yinkaisheng) | `comtypes` (Enthought) |
|---|---|---|
| Mantenedor | 1 pessoa física, projeto de tempo livre | Enthought + 6 mantenedores |
| Licença | Apache 2.0 | MIT |
| Último release PyPI | 2.0.29, ago/2025 | 1.4.16, mar/2026 |
| Cadência | esparsa (2 commits triviais em 10 meses) | ~2 meses |
| Issues abertas | 154 | 98 |
| Histórico | 6 releases *yanked* em sequência (2.0.21–2.0.26, abr/2025) | — |
| Bus factor | **1** | vários |

### 3.3 Decisão: `comtypes` puro, sem `uiautomation`

**A base é `comtypes` + `GetModule("UIAutomationCore.dll")`. `uiautomation` não entra, nem em runtime nem como dependência.**

Justificativa:

1. **A §2.2 é inegociável e a lib não a atende.** O servidor precisa de `CUIAutomation8` com `ConnectionTimeout`/`TransactionTimeout` para que **CA-23** (app travado → `TIMEOUT` ≤ 15 s) seja implementável. Só o caminho comtypes entrega isso.
2. **Manter as duas significaria dois clientes COM no mesmo processo** — o do wrapper (sem timeouts) e o nosso (com) — cada um com sua própria visão de cache e seus próprios ponteiros. É uma fonte de bugs sem contrapartida.
3. **O argumento clássico contra comtypes cru caiu.** O boilerplate temido (enums, IDs de propriedade e de pattern) é **gerado** do typelib: 626 símbolos prontos. O que resta escrever à mão é `TreeWalker`, construção de `IUIAutomationCondition` e mapeamento de `HRESULT` — que é o próprio produto, não plumbing descartável.
4. **O que o wrapper ofereceria além disso, esta spec já rejeita.** Busca (§8.4) precisa ser determinística; envio de teclas (§8.9) tem sintaxe própria e liberação garantida de modificadores; esperas (§7.3) têm política própria de backoff e settle. A sobreposição útil é quase nula.
5. `pywinauto` segue rejeitado pelo motivo original: identificador principal é *best match* textual, incompatível com o princípio 4 (determinismo) e com o esquema de refs.

**Regra dura de implementação (mantida e reforçada):** `uia_get_tree` e `uia_find_elements` **devem** usar `FindAllBuildCache` com `CacheRequest` contendo todas as propriedades necessárias, e ler exclusivamente as propriedades `Cached*`. Ler propriedade a propriedade (`Current*`) em árvore de >100 nós é inaceitável — cada leitura é um RPC cross-process.

**Consequência para `uia/core.py`:** o módulo passa a ser dono de (a) instanciar `CUIAutomation8`/`IUIAutomation6` com os timeouts, (b) expor os `TreeWalker` de ControlView, (c) fabricar condições (`CreatePropertyCondition`, `CreateAndCondition`, `CreateTrueCondition`), (d) montar `CacheRequest` reutilizáveis, (e) traduzir `COMError.hresult` para os códigos da §9.2. Estimativa: 300–500 linhas.

Dependências fixadas:

```toml
[project]
requires-python = ">=3.11"
dependencies = [
  "mcp>=1.2.0",
  "comtypes>=1.4.16",
  "psutil>=5.9",          # process name/exe path confiável a partir do pid
]

[dependency-groups]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23"]
```

`psutil` é usado para resolver `pid → process name/exe path` de forma robusta (a UIA fornece só `ProcessId`). `tomli-w` foi removido: a §10.1 estabelece que a config é lida apenas no startup e nunca reescrita em runtime, e `tomllib` (leitura) é stdlib desde 3.11.

**Interpretador de referência: Python 3.14.6 x64** — validado nas provas de conceito acima. O piso declarado continua 3.11 (`tomllib`, sintaxe de tipos), mas o desenvolvimento e os testes de aceitação rodam em 3.14.

---

## 4. Requisitos de ambiente

| Requisito | Detalhe | Consequência se violado |
|---|---|---|
| SO | Windows 10 build 17763+ ou Windows 11 | `SetProcessDpiAwarenessContext` indisponível em builds antigas → usar `SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)` |
| Python | 3.14 x64 (piso 3.11) | Python 32-bit consegue falar UIA com processos 64-bit, mas há degradação/limitações em propriedades nativas; exigir 64-bit |
| Sessão | Sessão interativa desbloqueada, console local | Tela bloqueada ou sessão RDP desconectada → árvore vazia. Retornar `SESSION_UNAVAILABLE` |
| COM | STA por thread (§2.2) | `RPC_E_WRONG_THREAD`, crashes intermitentes |
| DPI | Processo **deve** declarar Per-Monitor V2 antes de qualquer chamada UIA/Win32 | `BoundingRectangle` retorna coords virtualizadas; clique de fallback erra o alvo em telas com escala ≠ 100% |

### 4.1 DPI e múltiplos monitores

No `__main__.py`, **antes de importar/instanciar qualquer coisa de UI**:

```python
import ctypes
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(
        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
except (AttributeError, OSError):
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
```

Regras derivadas:

- `BoundingRectangle` da UIA é sempre em **pixels físicos do desktop virtual**, com o processo PMv2-aware. Reportar exatamente isso em `rect`, sem conversão.
- Coordenadas **podem ser negativas**: monitores à esquerda/acima do primário. Nunca assumir origem em (0,0).
- Clique de fallback via `SendInput` com `MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK` exige normalizar para 0..65535 sobre o **desktop virtual**:
  `nx = round((x - SM_XVIRTUALSCREEN) * 65535 / (SM_CXVIRTUALSCREEN - 1))` (idem para y). Usar `SM_XVIRTUALSCREEN=76`, `SM_YVIRTUALSCREEN=77`, `SM_CXVIRTUALSCREEN=78`, `SM_CYVIRTUALSCREEN=79`.
- Cada janela retorna também `dpi` (via `GetDpiForWindow`) e `monitor` (índice + nome do device) em `uia_list_windows`, para diagnóstico.
- Elementos com `IsOffscreen == True` ou `rect` de área zero **não** aceitam clique por coordenada: erro `ELEMENT_OFFSCREEN` com hint para usar `uia_scroll` ou o pattern `ScrollItem` antes.

### 4.2 Elevação (UIPI) — comportamento obrigatório

O Windows isola processos por nível de integridade. Consequências concretas:

| Cenário | Servidor **não** elevado | Servidor elevado |
|---|---|---|
| App de integridade média (Notepad, Word, Chrome) | Funciona | Funciona |
| App elevado (Gerenciador de Tarefas como admin, regedit elevado, instaladores) | Árvore vazia / `E_ACCESSDENIED`; input sintético descartado silenciosamente | Funciona |
| Prompt de UAC (Secure Desktop) | Inacessível | **Também inacessível** — desktop separado |
| Tela de bloqueio / Ctrl+Alt+Del | Inacessível | Inacessível |

Implementação:

- Detectar no startup se o processo é elevado (`IsUserAnAdmin` / token `TokenElevation`) e expor em `uia_list_windows` → `server_elevated: bool`.
- Ao encontrar janela cujo `pid` pertence a processo de integridade superior (detectável por `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` falhando com `ERROR_ACCESS_DENIED`, ou árvore com 0 filhos e `Name` vazio), retornar `ELEVATION_REQUIRED` com hint explícito.
- **Nunca** tentar auto-elevar. Documentar que rodar elevado amplia drasticamente o raio de dano e deve ser combinado com allowlist restrita.
- Não implementar `uiAccess=true` na v1: exige assinatura com certificado confiável e instalação em `%ProgramFiles%`.

---

## 5. Modelo de dados

### 5.1 Nó da árvore (`Node`)

Chaves curtas, deliberadamente, para economizar tokens. A árvore é serializada como **lista plana em pré-ordem** com campo de profundidade `d` — reconstruir hierarquia é trivial e custa ~30% menos tokens que JSON aninhado.

| Chave | Tipo | Sempre presente | Descrição |
|---|---|---|---|
| `ref` | string | sim | Referência opaca estável. Formato `w<win>-e<n>` (ex.: `w3-e142`) |
| `d` | int | sim | Profundidade relativa à raiz da captura (raiz = 0) |
| `type` | string | sim | ControlType sem prefixo: `Button`, `Edit`, `MenuItem`, `List`, `ListItem`, `Text`, `Pane`, `Custom`… |
| `name` | string | sim (pode ser `""`) | `Name` da UIA, truncado em 120 chars com sufixo `…` |
| `aid` | string | se não vazio | `AutomationId` |
| `cls` | string | se não vazio | `ClassName` |
| `val` | string \| number \| bool | se aplicável | Valor atual: `ValuePattern.Value`, `RangeValuePattern.Value`, `ToggleState` (`on`/`off`/`indeterminate`), `SelectionItem.IsSelected` |
| `st` | string[] | sim | Estados presentes, ver tabela abaixo. Ausência = falso |
| `rect` | [int,int,int,int] | se visível | `[left, top, right, bottom]` em px físicos do desktop virtual |
| `pat` | string[] | sim | Patterns acionáveis suportados: `Invoke`,`Toggle`,`SelectionItem`,`ExpandCollapse`,`Value`,`RangeValue`,`Scroll`,`ScrollItem`,`Text`,`Grid`,`Table`,`Window` |
| `n` | int | se truncado | Nº de filhos **não** retornados sob este nó |
| `help` | string | se não vazio e `verbose=true` | `HelpText` / `FullDescription` |

Valores possíveis em `st`: `enabled`, `disabled`, `focused`, `focusable`, `offscreen`, `selected`, `expanded`, `collapsed`, `checked`, `unchecked`, `indeterminate`, `readonly`, `required`, `password`, `multiline`.

Regra de redação: se `password ∈ st`, `val` **nunca** é retornado; em seu lugar `"val": "«redacted:password»"` e `"len": <int>` opcional.

### 5.2 Captura e cache

**Distinção que gera bug se ignorada:** `FindAllBuildCache(escopo_da_busca, condição, cache_request)` tem *dois* escopos independentes. O primeiro argumento diz **quais elementos achar**. O `TreeScope` do `CacheRequest` diz, para **cada elemento achado**, quanto da subárvore *dele* pré-carregar junto. Não são a mesma coisa e não devem receber o mesmo valor.

`CacheRequest.TreeScope` **deve ser `TreeScope_Element`**. Qualquer valor que inclua `Descendants` faz uma busca que casa N elementos pedir N subárvores completas numa única transação COM; ela estoura o `TransactionTimeout` de 10 s (§2.2) e aflora como `E_FAIL` (`0x80004005`).

Medido em 2026-09-10 no WhatsApp Desktop (WebView2, 24409 nós na ControlView), com find=`TreeScope_Subtree` a partir da janela:

| `CacheRequest.TreeScope` | Resultado |
|---|---|
| `Element` | OK — 24409 nós em 18,2 s; ler `Cached*` dos 24409 leva **198 ms** |
| `Children` | OK, porém 22,5 s |
| `Descendants` | `E_FAIL` após ~13,4 s |
| `Subtree` | `E_FAIL` após ~13,5 s |

Não é restrição categórica: num elemento folha, `cache=Subtree` funciona. A falha é de volume, e o tempo até falhar coincide com o `TransactionTimeout` que nós mesmos configuramos.

#### Algoritmo obrigatório: captura limitada por nível

A segunda lição da medição é que **uma única busca com find=Subtree é inviável mesmo com o cache correto**: 18,2 s excede o teto de 15 s do `UiaWorker` (§2.2), então `uia_get_tree` devolveria `TIMEOUT` justamente nos apps mais interessantes. E ela paga por 24409 nós para entregar 200 — o orçamento da §6.1 era aplicado *depois* de buscar tudo.

**O orçamento é aplicado durante o percurso, não depois.** `uia_get_tree` executa:

1. Resolve a janela (por `window_ref`, `hwnd` ou `title`).
2. Monta **uma vez** o `CacheRequest`, reusado em todas as chamadas do passo 4:
   - Propriedades: `Name`, `AutomationId`, `ClassName`, `ControlType`, `IsEnabled`, `IsOffscreen`, `IsKeyboardFocusable`, `HasKeyboardFocus`, `BoundingRectangle`, `RuntimeId`, `ProcessId`, `IsPassword`, `ToggleToggleState`, `ExpandCollapseExpandCollapseState`, `SelectionItemIsSelected`, `ValueValue`, `ValueIsReadOnly`, `RangeValueValue`, e `IsXxxPatternAvailable` para os patterns da tabela da §5.1.
   - `TreeScope = TreeScope_Element` — regra acima, inegociável.
   - `TreeFilter = ControlViewCondition` (não `RawView`: infla a árvore com nós irrelevantes).
   - `AutomationElementMode = Full` (necessário para obter patterns depois).
3. Inicializa a fila de percurso com a raiz da captura (`root_ref`, ou o elemento da janela).
4. **Enquanto** houver nós na fila **e** `emitidos < max_nodes` **e** `profundidade < max_depth`:
   1. Desenfileira o nó e chama `FindAllBuildCache(TreeScope_Children, ControlViewCondition, cache_request)`.
   2. Lê **apenas** propriedades `Cached*` dos filhos — zero RPC adicional.
   3. Aplica `max_children_per_node` fatiando o array retornado (sem RPC); anota `"n": <restantes>` no nó pai.
   4. Aplica o filtro (§6.2) no cliente, sobre as propriedades já cacheadas.
   5. Enfileira os filhos sobreviventes para o próximo nível.
5. Registra cada nó emitido no `RefStore` e emite a lista plana.

O percurso é em **largura por níveis, ordem de documento dentro do nível**, como a §6.1 já exige — e agora a §5.2 o implementa em vez de contradizê-lo.

Custo: **uma chamada COM por nó-pai visitado**, cada uma trazendo todos os filhos com todas as propriedades de uma vez. Isso satisfaz a regra dura da §3.3: o que ela proíbe é ler propriedade a propriedade (`Current*`), que custaria ~50 RPCs por nó.

Medido na mesma janela:

| Orçamento | Nós emitidos | Chamadas COM | Tempo |
|---|---|---|---|
| `max_nodes=200`, `max_depth=12` | 46 | 44 | **159 ms** |
| `max_nodes=600`, `max_depth=20` | 287 | 213 | 375 ms |
| `max_nodes=1500`, `max_depth=40` | 631 | 632 | 721 ms |
| *(referência)* find=Subtree sem limite | 24409 | 1 | **18205 ms** |

**Armadilha a evitar:** não empurrar o filtro da §6.2 para dentro da condição do `FindAllBuildCache`. O percurso precisa **descer através** de containers que não passam no filtro para alcançar os nós que passam; uma condição nativa restritiva poda o caminho e o conteúdo some. A condição do percurso é sempre a ControlView; o filtro roda no cliente, sobre propriedades já cacheadas, a custo zero. (A otimização por condição nativa da §8.4 é legítima porque lá a busca é *plana* — não precisa atravessar nada.)

**Pendência aberta para a §6.1 — `max_depth`:** o default de 12 foi calibrado para app nativo. Árvores de WebView2/Electron são muito mais fundas: a do WhatsApp tem profundidade real 45, e com `max_depth=12` a captura devolve 46 nós, **nenhum deles conteúdo de conversa** — o conteúdo mora abaixo do nível 12. O default atual torna `uia_get_tree` inútil nessa classe de app. Decidir na §6.1 antes do Plano 2.

---

## 6. Orçamento de resposta (obrigatório)

### 6.1 Limites

| Parâmetro | Default | Máximo | Efeito |
|---|---|---|---|
| `max_nodes` | 200 | 1500 | Nº de nós na resposta |
| `max_depth` | 12 | 40 | Profundidade máxima percorrida |
| `max_chars` (texto) | 6000 | 40000 | Caracteres de conteúdo textual |
| `max_children_per_node` | 30 | 500 | Irmãos por container antes de elidir |
| Name truncation | 120 chars | — | Sufixo `…` |

Comportamento ao estourar:

- Percurso em **largura por níveis, ordem de documento dentro do nível** — garante que os elementos mais relevantes (rasos) apareçam antes.
- Ao atingir `max_children_per_node`, emite os primeiros N e, no nó pai, `"n": <restantes>`. O agente pode expandir chamando `uia_get_tree` com `root_ref = <ref do pai>`.
- Ao atingir `max_nodes`, para e devolve `cursor` opaco (base64 de `{window_ref, tree_version, breadth_position}`), válido por 120 s.
- Toda resposta de árvore inclui:

```json
"stats": {
  "returned": 200, "visited": 1843, "truncated": true,
  "next_cursor": "eyJ3IjoidzMiLCJ2Ijo3LCJwIjoyMDB9",
  "hint": "Response truncated. Narrow with filter='interactive', a smaller max_depth, root_ref, or call uia_find_elements instead of dumping the tree."
}
```

**Regra dura:** o servidor **nunca** retorna árvore não truncada acima de 1500 nós, mesmo com `max_nodes` maior. Ele corta e sinaliza.

### 6.2 Filtros (`filter`)

| Valor | Inclui |
|---|---|
| `interactive` (**default**) | Nós com pelo menos um pattern acionável (`Invoke`,`Toggle`,`SelectionItem`,`ExpandCollapse`,`Value` gravável,`RangeValue`,`Scroll`) **ou** `IsKeyboardFocusable=true`; e `IsEnabled=true`; e `IsOffscreen=false`. Ancestrais desses nós são incluídos apenas se necessários para hierarquia (marcados como estruturais) |
| `content` | `interactive` + nós de texto (`Text`, `Document`, `Edit` read-only, `Image` com `Name`) |
| `all` | Tudo na ControlView, respeitando limites de orçamento |
| `landmarks` | Só containers estruturais (`Window`,`Pane`,`Group`,`ToolBar`,`MenuBar`,`Tab`,`Tree`,`List`,`Table`,`Document`) — útil como primeiro passo de orientação em app desconhecido |

**Poda de cadeias:** containers sem nenhum descendente que passe no filtro são removidos. Cadeias de container com um único filho (`Pane > Pane > Pane > Button`) colapsam para o nó folha, e o nó retornado ganha `"d"` do nível real (a hierarquia colapsada é indicada por saltos em `d`).

---

## 7. Referências (`ref`) e sincronização

### 7.1 Esquema de refs

```
ref = "w" <window_seq> "-e" <element_seq>        # elemento
ref = "w" <window_seq>                           # janela (top-level)
```

`window_seq` e `element_seq` são contadores monotônicos por processo do servidor. Refs são **opacas** para o agente: nunca construir ou adivinhar.

Entrada no `RefStore`:

```python
@dataclass
class RefEntry:
    ref: str
    element: "IUIAutomationElement"   # ponteiro COM, só tocado na UiaWorker
    runtime_id: tuple[int, ...]       # UIA RuntimeId no momento da captura
    hwnd: int                         # janela top-level dona
    window_ref: str
    identity: ElementIdentity         # aid, control_type, name, class_name, index_path
    tree_version: int
    created_at: float
    last_ok_at: float
```

`index_path`: caminho de índices na ControlView desde a raiz da janela, ex.: `[0, 3, 1, 7]`. É o material de re-resolução quando o `RuntimeId` morre.

### 7.2 Ciclo de vida e revalidação

**Fatos da UIA que ditam o desenho:**
- `RuntimeId` é único **enquanto o elemento existe**, e pode ser reutilizado depois que ele morre.
- `RuntimeId` **não** sobrevive a fechar/reabrir o app, nem a re-virtualização de itens de lista (item que sai da viewport pode ser destruído e recriado com id diferente).
- Um ponteiro COM para elemento destruído levanta `COMError` com `HRESULT 0x80040201` (`UIA_E_ELEMENTNOTAVAILABLE`).

**Algoritmo `resolve(ref)` — executado no início de toda tool que aceita `ref`:**

```
1. entry = store.get(ref)
   se ausente          → REF_NOT_FOUND
   se expirado (TTL)   → STALE_REF (motivo: "expired")
2. se não IsWindow(entry.hwnd) → WINDOW_CLOSED
3. tenta ler entry.element.CurrentProcessId (probe barato)
   ok  → passo 4
   COMError 0x80040201 → passo 5 (rebind)
4. compara GetRuntimeId() com entry.runtime_id
   igual     → retorna (element, rebound=False)
   diferente → passo 5
5. REBIND: dentro da janela entry.hwnd, procura por, em ordem:
   a) AutomationId + ControlType   (se aid não vazio)   — condição exata
   b) Name + ControlType + ClassName                     — condição exata
   c) index_path na ControlView atual, validando ControlType do nó final
   Se exatamente 1 candidato → atualiza entry (element, runtime_id, last_ok_at),
                               retorna (element, rebound=True)
   Se >1 candidatos          → AMBIGUOUS_MATCH com refs candidatas
   Se 0 candidatos           → STALE_REF (motivo: "element_gone")
```

- TTL padrão de ref: **300 s** (`UIA_REF_TTL_S`). Cada `resolve` bem-sucedido renova `last_ok_at`.
- Ao fechar uma janela, todas as refs daquele `window_ref` são invalidadas em lote.
- `RefStore` tem capacidade máxima (`UIA_REF_CACHE_MAX`, default 5000) com evicção LRU. Refs evictadas → `REF_NOT_FOUND`.
- Toda resposta de tool que fez rebind inclui `"rebound": true`, para o agente saber que a árvore mudou sob seus pés.
- `tree_version` da janela incrementa a cada `uia_get_tree`/`uia_find_elements`. Refs de versões antigas continuam válidas enquanto o elemento existir — a versão serve só para cursores de paginação.

**Erro `STALE_REF` deve sempre trazer:**

```json
{"error":{"code":"STALE_REF","message":"Ref w3-e142 no longer resolves to a live element (element_gone).",
"hint":"The UI changed. Call uia_get_tree(window_ref='w3', filter='interactive') to get fresh refs, then retry.",
"details":{"window_ref":"w3","reason":"element_gone","window_alive":true}}}
```

### 7.3 Espera e sincronização

UI nativa renderiza de forma assíncrona; ler a árvore logo após um clique frequentemente pega estado intermediário.

**Mecanismos, todos obrigatórios:**

1. **`uia_wait_for` (tool dedicada)** — polling por condição de elemento (aparecer / sumir / ficar habilitado / conter texto).
2. **Parâmetro `timeout_ms` nas tools de leitura e busca** (default 0 = sem espera; >0 = re-tenta até achar).
3. **Settle pós-ação** — após `uia_click`, `uia_set_value`, `uia_send_keys`, o servidor espera por `settle_ms` (default 250, configurável) usando a estratégia:
   - `WaitForInputIdle(hProcess, min(settle_ms, 1000))` quando o processo tem fila de mensagens GUI;
   - depois, poll de 50 ms comparando um *hash de assinatura* da janela (contagem de filhos diretos + `Name` da janela + hash do foco atual). Estabilizou por 2 amostras consecutivas → pronto. Estourou `settle_ms` → segue mesmo assim, e marca `"settled": false` na resposta.
4. **Backoff de polling**: 50 ms → 100 ms → 200 ms, teto 250 ms. Timeout padrão de espera: 5000 ms; máximo 60000 ms.

Nenhuma tool bloqueia mais que `timeout_ms + settle_ms + 2000 ms`. Estourou → `TIMEOUT` com hint.

### 7.4 Por que não eventos UIA na v1

Handlers de evento UIA (`AddAutomationEventHandler`) exigem callbacks COM entrando em thread própria, têm custo alto de performance no app-alvo (forçam provider a ligar modo full), e podem deadlockar se o cliente não bombeia mensagens. Polling é mais lento porém previsível e depurável. Reavaliar em v2 com `AddAutomationEventHandler` restrito a `Window_WindowOpened`.

---

## 8. Superfície de ferramentas

Convenções comuns a todas as tools:

- Retorno de sucesso: objeto JSON (serializado como `text` content no MCP). Todo retorno inclui `"ok": true`.
- Retorno de erro: `"ok": false` + `"error": {code, message, hint, details}`, e a resposta MCP é marcada `isError = true`.
- Toda tool que atinge uma janela valida a **allowlist** (§10) antes de qualquer outra coisa.
- Toda tool que muda estado é bloqueada em **read-only mode** (§10.2).
- Todos os `*_ref` aceitam tanto ref de janela (`w3`) quanto de elemento (`w3-e12`) onde a semântica permitir; onde não permitir, erro `INVALID_ARGUMENT`.

### Sumário

| # | Tool | Categoria | Muta estado |
|---|---|---|---|
| 1 | `uia_list_windows` | descoberta | não |
| 2 | `uia_get_tree` | leitura | não |
| 3 | `uia_get_text` | leitura | não |
| 4 | `uia_find_elements` | busca | não |
| 5 | `uia_get_value` | leitura | não |
| 6 | `uia_wait_for` | sincronização | não |
| 7 | `uia_click` | interação | **sim** |
| 8 | `uia_set_value` | interação | **sim** |
| 9 | `uia_send_keys` | interação | **sim** |
| 10 | `uia_scroll` | interação | **sim** |
| 11 | `uia_focus_window` | interação | **sim** |

---

### 8.1 `uia_list_windows`

**Description (exposta ao agente):** `List top-level windows currently open on the desktop, with a stable window ref for each. Call this first to discover what applications are available before capturing a UI tree.`

**Parâmetros**

| Nome | Tipo | Obrig. | Default | Descrição |
|---|---|---|---|---|
| `include_minimized` | bool | não | `true` | Inclui janelas minimizadas |
| `include_hidden` | bool | não | `false` | Inclui janelas sem `WS_VISIBLE` ou com título vazio |
| `process_filter` | string | não | — | Substring case-insensitive de process name ou título |
| `max_results` | int | não | `40` | Teto 200 |

**Retorno**

```json
{
  "ok": true,
  "server_elevated": false,
  "read_only": false,
  "focused_window_ref": "w1",
  "windows": [
    {"ref":"w1","title":"Sem título - Bloco de Notas","process":"Notepad.exe","pid":12044,
     "hwnd":723918,"focused":true,"minimized":false,"maximized":false,
     "rect":[100,100,1000,760],"dpi":144,"monitor":0,"allowed":true,"elevated":false},
    {"ref":"w2","title":"Calculadora","process":"CalculatorApp.exe","pid":9820,
     "hwnd":198442,"focused":false,"minimized":false,"maximized":false,
     "rect":[1920,0,2260,700],"dpi":96,"monitor":1,"allowed":true,"elevated":false},
    {"ref":"w3","title":"Gerenciador de Tarefas","process":"Taskmgr.exe","pid":4412,
     "hwnd":331002,"focused":false,"minimized":false,"maximized":true,
     "rect":[0,0,1920,1080],"dpi":96,"monitor":0,"allowed":false,"elevated":true}
  ],
  "stats":{"returned":3,"total":17,"filtered_by_allowlist":0}
}
```

Notas: `allowed:false` significa que a janela existe mas está fora da allowlist — ela **é listada** (para o agente entender o desktop) mas qualquer outra tool sobre ela retorna `APP_NOT_ALLOWED`. Se `config.hide_denied = true`, janelas negadas são omitidas e contabilizadas em `filtered_by_allowlist`.

**Erros:** `SESSION_UNAVAILABLE`, `UIA_COM_ERROR`.

---

### 8.2 `uia_get_tree`

**Description:** `Capture the UI Automation tree of a window as a flat, pre-order list of nodes with stable refs. Defaults to interactive elements only. Use root_ref to drill into a subtree instead of re-dumping the whole window.`

**Parâmetros**

| Nome | Tipo | Obrig. | Default | Descrição |
|---|---|---|---|---|
| `window_ref` | string | sim¹ | — | Ref de janela de `uia_list_windows` |
| `root_ref` | string | não | — | Captura a partir deste elemento (¹ dispensa `window_ref`) |
| `filter` | enum | não | `"interactive"` | `interactive` \| `content` \| `all` \| `landmarks` |
| `max_depth` | int | não | `12` | 1–40 |
| `max_nodes` | int | não | `200` | 1–1500 |
| `max_children_per_node` | int | não | `30` | 1–500 |
| `cursor` | string | não | — | Continua captura truncada anterior |
| `verbose` | bool | não | `false` | Inclui `help` (`HelpText`/`FullDescription`) |
| `timeout_ms` | int | não | `0` | Se >0, espera a janela ter ≥1 nó que passe no filtro |

**Retorno**

```json
{
  "ok": true,
  "window_ref": "w1",
  "window_title": "Sem título - Bloco de Notas",
  "tree_version": 4,
  "nodes": [
    {"ref":"w1-e0","d":0,"type":"Window","name":"Sem título - Bloco de Notas","cls":"Notepad","st":["enabled","focused"],"rect":[100,100,1000,760],"pat":["Window","Transform"]},
    {"ref":"w1-e1","d":1,"type":"MenuBar","name":"Barra de menus da aplicação","st":["enabled"],"rect":[104,132,996,156],"pat":[]},
    {"ref":"w1-e2","d":2,"type":"MenuItem","name":"Arquivo","aid":"File","st":["enabled","focusable"],"rect":[108,134,150,154],"pat":["Invoke","ExpandCollapse"]},
    {"ref":"w1-e3","d":2,"type":"MenuItem","name":"Editar","aid":"Edit","st":["enabled","focusable"],"rect":[152,134,196,154],"pat":["Invoke","ExpandCollapse"]},
    {"ref":"w1-e7","d":1,"type":"Edit","name":"Editor de texto","aid":"RichEditBox","cls":"RichEditBox","val":"","st":["enabled","focusable","multiline"],"rect":[104,160,996,730],"pat":["Value","Text","Scroll"]}
  ],
  "stats":{"returned":5,"visited":38,"truncated":false,"next_cursor":null}
}
```

**Erros:** `WINDOW_NOT_FOUND`, `WINDOW_CLOSED`, `APP_NOT_ALLOWED`, `ELEVATION_REQUIRED`, `STALE_REF` (se `root_ref`), `TIMEOUT`, `UIA_COM_ERROR`, `INVALID_ARGUMENT`.

---

### 8.3 `uia_get_text`

**Description:** `Extract the readable text content of a window or subtree as linear text, in reading order. Use this to read a document, dialog message, list contents, or status bar without dumping the full tree.`

**Parâmetros**

| Nome | Tipo | Obrig. | Default | Descrição |
|---|---|---|---|---|
| `window_ref` | string | sim¹ | — | Janela alvo |
| `root_ref` | string | não | — | Subárvore alvo (¹) |
| `max_chars` | int | não | `6000` | Teto 40000 |
| `offset` | int | não | `0` | Continuação a partir do caractere N |
| `include_refs` | bool | não | `false` | Prefixa cada bloco com `[ref]`, para casar texto → elemento |

**Algoritmo:** para cada nó na ControlView em ordem de documento — se suporta `TextPattern`, usa `DocumentRange.GetText(max)`; senão usa `Name` + `ValuePattern.Value`. Deduplica: se um ancestral com `TextPattern` já cobriu o texto dos descendentes, não repete. Separa blocos com `\n`. Elementos `password` viram `«redacted:password»`. Elementos com `IsOffscreen=true` são incluídos mas marcados `[offscreen]` quando `include_refs=true`.

**Retorno**

```json
{
  "ok": true,
  "window_ref": "w2",
  "text": "Calculadora\nPadrão\nVisor é 12.345\n1  2  3  4  5\nMC MR M+ M- MS\n",
  "truncated": false,
  "chars": 74,
  "next_offset": null
}
```

**Erros:** `WINDOW_NOT_FOUND`, `WINDOW_CLOSED`, `APP_NOT_ALLOWED`, `STALE_REF`, `UIA_COM_ERROR`.

---

### 8.4 `uia_find_elements`

**Description:** `Search a window for elements matching a criterion (name, automation id, control type, partial text) and return candidate refs. Cheaper and more precise than dumping the tree when you already know what you are looking for.`

**Parâmetros**

| Nome | Tipo | Obrig. | Default | Descrição |
|---|---|---|---|---|
| `window_ref` | string | sim¹ | — | Janela alvo |
| `root_ref` | string | não | — | Restringe a subárvore (¹) |
| `name` | string | não | — | Comparação conforme `match` |
| `automation_id` | string | não | — | Exato, case-sensitive |
| `control_type` | string | não | — | Ex.: `Button`, `Edit`, `MenuItem` |
| `class_name` | string | não | — | Exato |
| `text_contains` | string | não | — | Substring em `Name`, `Value` ou texto do `TextPattern` |
| `match` | enum | não | `"contains"` | `exact` \| `contains` \| `starts_with` \| `regex` (case-insensitive exceto `automation_id`) |
| `only_interactive` | bool | não | `true` | Restringe a nós com pattern acionável ou focáveis |
| `max_results` | int | não | `20` | Teto 100 |
| `timeout_ms` | int | não | `0` | Se >0, repete a busca até achar ≥1 ou estourar |

Pelo menos um dentre `name`/`automation_id`/`control_type`/`class_name`/`text_contains` é obrigatório, senão `INVALID_ARGUMENT`.

**Otimização obrigatória:** critérios de `automation_id`, `control_type`, `class_name` e `name` com `match="exact"` viram `IUIAutomationCondition` nativas (`CreatePropertyCondition` + `CreateAndCondition`) e são resolvidos com `FindAllBuildCache` — filtragem no provider, não em Python. Só `contains`/`starts_with`/`regex`/`text_contains` filtram no cliente, e nesse caso a busca respeita `max_depth=40` e um teto interno de 5000 nós visitados (estourou → resultado parcial com `"exhaustive": false`).

**Retorno**

```json
{
  "ok": true,
  "window_ref": "w1",
  "matches": [
    {"ref":"w1-e12","type":"Button","name":"Salvar","aid":"SaveButton","st":["enabled","focusable"],"rect":[820,690,900,720],"pat":["Invoke"],"path":"Window > Pane > CommandBar > Button"},
    {"ref":"w1-e31","type":"MenuItem","name":"Salvar como…","aid":"SaveAs","st":["enabled"],"rect":[210,240,360,264],"pat":["Invoke"],"path":"Window > MenuBar > MenuItem[Arquivo] > Menu > MenuItem"}
  ],
  "stats":{"returned":2,"visited":412,"exhaustive":true}
}
```

`path` é uma trilha legível de ControlType/Name dos ancestrais (máx. 5 níveis) — ajuda o agente a desambiguar sem outra chamada.

**Erros:** `ELEMENT_NOT_FOUND` (zero matches — sim, é erro, com hint), `WINDOW_NOT_FOUND`, `APP_NOT_ALLOWED`, `TIMEOUT`, `INVALID_ARGUMENT`.

Hint padrão de `ELEMENT_NOT_FOUND`: `"No element matched. Try match='contains' with a shorter name, drop control_type, or call uia_get_tree(filter='interactive') to see what is actually there."`

---

### 8.5 `uia_get_value`

**Description:** `Read the current value and full state of a single element by ref: text of an edit box, toggle state of a checkbox, selection of a combo box, range value of a slider.`

**Parâmetros**

| Nome | Tipo | Obrig. | Default | Descrição |
|---|---|---|---|---|
| `ref` | string | sim | — | Ref do elemento |
| `max_chars` | int | não | `6000` | Trunca valores longos |

**Ordem de leitura:** `ValuePattern.Value` → `TextPattern.DocumentRange.GetText` → `RangeValuePattern.Value` → `TogglePattern.ToggleState` → `SelectionPattern` (retorna nomes dos itens selecionados) → `LegacyIAccessible.Value` → `Name`. O campo `source` diz qual foi usado.

**Retorno**

```json
{
  "ok": true,
  "ref": "w1-e7",
  "rebound": false,
  "type": "Edit",
  "name": "Editor de texto",
  "aid": "RichEditBox",
  "value": "Olá do agente\r\n",
  "source": "ValuePattern",
  "value_type": "string",
  "truncated": false,
  "st": ["enabled","focusable","focused","multiline"],
  "rect": [104,160,996,730],
  "pat": ["Value","Text","Scroll"]
}
```

Exemplo checkbox:

```json
{"ok":true,"ref":"w4-e19","type":"CheckBox","name":"Quebra automática de linha",
 "value":"on","source":"TogglePattern","value_type":"toggle","st":["enabled","checked","focusable"]}
```

**Erros:** `REF_NOT_FOUND`, `STALE_REF`, `WINDOW_CLOSED`, `PATTERN_NOT_SUPPORTED` (nenhuma fonte de valor disponível), `APP_NOT_ALLOWED`.

---

### 8.6 `uia_wait_for`

**Description:** `Poll until a UI condition holds: an element appears, disappears, becomes enabled, gets focus, or its value matches. Use after an action that triggers async rendering (dialog opening, page loading, list populating) instead of guessing with a fixed sleep.`

**Parâmetros**

| Nome | Tipo | Obrig. | Default | Descrição |
|---|---|---|---|---|
| `window_ref` | string | sim¹ | — | Janela alvo |
| `ref` | string | não | — | Espera sobre elemento específico (¹) |
| `condition` | enum | sim | — | `appears` \| `disappears` \| `enabled` \| `focused` \| `value_equals` \| `value_contains` \| `window_appears` |
| `name` / `automation_id` / `control_type` / `text_contains` / `match` | — | não | — | Mesmos semânticos de `uia_find_elements`, usados quando `condition` é sobre busca |
| `expected` | string | condicional | — | Obrigatório para `value_equals` / `value_contains` |
| `timeout_ms` | int | não | `5000` | 100–60000 |
| `poll_ms` | int | não | `100` | 50–1000 (backoff aplicado até 250 ms) |

**Retorno**

```json
{"ok":true,"condition":"appears","satisfied":true,"waited_ms":840,"polls":7,
 "match":{"ref":"w5-e3","type":"Button","name":"Salvar","st":["enabled","focusable"],"rect":[640,420,720,450]}}
```

Falha:

```json
{"ok":false,"error":{"code":"TIMEOUT",
 "message":"Condition 'appears' (name contains 'Salvar') not satisfied within 5000 ms.",
 "hint":"The dialog may not have opened, or the label differs. Call uia_get_tree(window_ref='w5', filter='interactive') to inspect the current state, or raise timeout_ms.",
 "details":{"waited_ms":5012,"polls":34,"window_alive":true,"last_seen_candidates":0}}}
```

---

### 8.7 `uia_click`

**Description:** `Activate an element by ref using its UI Automation control pattern (Invoke, Toggle, SelectionItem, ExpandCollapse). Falls back to a real mouse click at the element's center only when no pattern is available. Never requires coordinates from the caller.`

**Parâmetros**

| Nome | Tipo | Obrig. | Default | Descrição |
|---|---|---|---|---|
| `ref` | string | sim | — | Elemento alvo |
| `action` | enum | não | `"auto"` | `auto` \| `invoke` \| `toggle` \| `select` \| `expand` \| `collapse` \| `mouse` |
| `button` | enum | não | `"left"` | `left` \| `right` \| `middle` (só com fallback de mouse ou `action="mouse"`) |
| `double` | bool | não | `false` | Duplo clique (implica `action="mouse"`) |
| `allow_coordinate_fallback` | bool | não | `true` | Se `false` e não houver pattern → `PATTERN_NOT_SUPPORTED` |
| `settle_ms` | int | não | `250` | 0–5000 |
| `scroll_into_view` | bool | não | `true` | Aplica `ScrollItemPattern.ScrollIntoView` se offscreen |

**Ordem de resolução com `action="auto"`:**

1. Se `InvokePattern` disponível → `Invoke()`.
2. Senão se `TogglePattern` → `Toggle()`.
3. Senão se `SelectionItemPattern` → `Select()`.
4. Senão se `ExpandCollapsePattern` → alterna conforme `ExpandCollapseState`.
5. Senão se `LegacyIAccessiblePattern` → `DoDefaultAction()`.
6. Senão, se `allow_coordinate_fallback` → `SetFocus()`, valida `IsOffscreen=false` e rect com área >0, move o cursor para o centro do `rect` e emite `SendInput` (down+up). **Só aqui** usa coordenada.
7. Senão → `PATTERN_NOT_SUPPORTED`.

Pré-checagens (nesta ordem, todas geram erro específico): allowlist → read-only → resolve(ref) → `IsEnabled` (senão `ELEMENT_DISABLED`) → offscreen + `scroll_into_view`.

**Retorno**

```json
{"ok":true,"ref":"w1-e2","method":"InvokePattern","fallback_used":false,
 "rebound":false,"settled":true,"duration_ms":312,
 "state_after":{"st":["enabled","expanded","focused"],"val":null},
 "window_changed":true,
 "note":"A new window/menu appeared. Call uia_get_tree or uia_list_windows to see it."}
```

`window_changed` é `true` quando o settle detecta nova janela top-level do mesmo processo ou mudança da janela em foco — sinal forte de menu/diálogo aberto.

**Erros:** `REF_NOT_FOUND`, `STALE_REF`, `WINDOW_CLOSED`, `ELEMENT_DISABLED`, `ELEMENT_OFFSCREEN`, `PATTERN_NOT_SUPPORTED`, `READ_ONLY_MODE`, `APP_NOT_ALLOWED`, `ACTION_RATE_LIMITED`, `TIMEOUT`, `UIA_COM_ERROR`.

---

### 8.8 `uia_set_value`

**Description:** `Set the text/value of an element by ref, preferring ValuePattern (atomic, no keystrokes). Falls back to focus + clear + typing when the control is not settable through the pattern.`

**Parâmetros**

| Nome | Tipo | Obrig. | Default | Descrição |
|---|---|---|---|---|
| `ref` | string | sim | — | Elemento alvo |
| `value` | string | sim | — | Valor a definir |
| `mode` | enum | não | `"replace"` | `replace` \| `append` |
| `method` | enum | não | `"auto"` | `auto` \| `pattern` \| `type` |
| `verify` | bool | não | `true` | Relê o valor após escrever e compara |
| `typing_delay_ms` | int | não | `0` | Atraso por caractere no fallback (apps que perdem teclas) |
| `settle_ms` | int | não | `250` | — |

**Ordem com `method="auto"`:**

1. `ValuePattern` disponível e `IsReadOnly=false` → `SetValue(value)` (append = valor atual + novo).
2. Senão, `RangeValuePattern` e `value` numérico → `SetValue(float)`.
3. Senão, fallback de digitação: `SetFocus()` → se `replace`, `Ctrl+A` seguido de `Delete` → envia o texto via `SendInput` com eventos `KEYEVENTF_UNICODE` (suporta acentos e emoji corretamente; **não** usar `VkKeyScan`).
4. Se `verify=true`, relê com a mesma cadeia de `uia_get_value` e compara normalizando `\r\n` → `\n`. Divergência → `VERIFY_FAILED` com `expected`/`actual`.

Elemento com `IsPassword=true`: valor **nunca** é logado nem ecoado na resposta; `verify` é forçado a `false` e a resposta traz `"value_echo": "«redacted:password»"`.

**Retorno**

```json
{"ok":true,"ref":"w1-e7","method":"ValuePattern","fallback_used":false,
 "verified":true,"chars_written":32,"rebound":false,"settled":true,"duration_ms":118,
 "value_after":"Relatório trimestral — rascunho"}
```

**Erros:** `REF_NOT_FOUND`, `STALE_REF`, `ELEMENT_DISABLED`, `ELEMENT_READONLY`, `PATTERN_NOT_SUPPORTED`, `VERIFY_FAILED`, `READ_ONLY_MODE`, `APP_NOT_ALLOWED`, `TIMEOUT`.

---

### 8.9 `uia_send_keys`

**Description:** `Send a key sequence or shortcut to a window (optionally focusing an element first). Use for shortcuts (Ctrl+S), navigation (Tab, Escape, arrows) and for controls that only respond to real keystrokes. Prefer uia_click / uia_set_value when a pattern exists.`

**Parâmetros**

| Nome | Tipo | Obrig. | Default | Descrição |
|---|---|---|---|---|
| `window_ref` | string | sim¹ | — | Janela alvo (é trazida ao foco antes) |
| `ref` | string | não | — | Foca este elemento antes de enviar (¹) |
| `keys` | string | sim | — | Sequência, sintaxe abaixo |
| `delay_ms` | int | não | `0` | Atraso entre teclas |
| `settle_ms` | int | não | `250` | — |

**Sintaxe de `keys`** (determinística, sem escapes ambíguos):

- Teclas nomeadas entre chaves: `{ENTER}` `{TAB}` `{ESC}` `{BACKSPACE}` `{DELETE}` `{HOME}` `{END}` `{PGUP}` `{PGDN}` `{UP}` `{DOWN}` `{LEFT}` `{RIGHT}` `{F1}`–`{F24}` `{SPACE}` `{INSERT}` `{APPS}`.
- Combinações: `{CTRL+S}`, `{CTRL+SHIFT+N}`, `{ALT+F4}`, `{WIN+D}`. Modificadores válidos: `CTRL`, `ALT`, `SHIFT`, `WIN`.
- Repetição: `{TAB 3}`.
- Qualquer outro texto é digitado literalmente como Unicode. `{` literal escreve-se `{{`.

**Segurança:** `{CTRL+ALT+DELETE}` é impossível (SAS) — retorna `INVALID_ARGUMENT`. Combinações na `blocked_keys` da config (default: `{ALT+F4}`, `{WIN+L}`, `{WIN+R}`, `{CTRL+SHIFT+ESC}`) são rejeitadas com `BLOCKED_ACTION`.

Implementação: `SendInput` com scan codes; modificadores pressionados/soltos explicitamente e com `try/finally` liberando todos os modificadores mesmo em exceção (deixar `Ctrl` preso é falha grave).

**Retorno**

```json
{"ok":true,"window_ref":"w1","keys_sent":"{CTRL+S}","events":4,
 "focused_before":"w1-e7","settled":true,"window_changed":true,
 "note":"Focus moved to a new window: 'Salvar como'. Call uia_list_windows."}
```

**Erros:** `WINDOW_NOT_FOUND`, `WINDOW_CLOSED`, `FOCUS_FAILED`, `INVALID_ARGUMENT` (sintaxe de `keys`), `BLOCKED_ACTION`, `READ_ONLY_MODE`, `APP_NOT_ALLOWED`, `ACTION_RATE_LIMITED`.

---

### 8.10 `uia_scroll`

**Description:** `Scroll a scrollable element (list, document, pane) using ScrollPattern, or bring a specific element into view with ScrollItemPattern. Use before interacting with content that is off-screen.`

**Parâmetros**

| Nome | Tipo | Obrig. | Default | Descrição |
|---|---|---|---|---|
| `ref` | string | sim | — | Elemento rolável, ou elemento a trazer à vista com `direction="into_view"` |
| `direction` | enum | não | `"down"` | `up` \| `down` \| `left` \| `right` \| `into_view` \| `to_percent` |
| `amount` | enum | não | `"page"` | `line` \| `page` (mapeia para `ScrollAmount.SmallIncrement`/`LargeIncrement`) |
| `times` | int | não | `1` | 1–20 |
| `h_percent` / `v_percent` | float | não | — | 0–100, usados com `direction="to_percent"` |
| `settle_ms` | int | não | `150` | — |

**Ordem:** `direction="into_view"` → `ScrollItemPattern.ScrollIntoView()`. Demais → `ScrollPattern.Scroll(h,v)` ou `SetScrollPercent`. Se o elemento não tem `ScrollPattern`, sobe na árvore até 5 ancestrais procurando um que tenha; achou → usa e informa em `scrolled_ref`. Nenhum ancestral → fallback de roda do mouse (`SendInput` `MOUSEEVENTF_WHEEL`, 120 unidades por `line`, 3× por `page`) sobre o centro do elemento, se `allow_coordinate_fallback` (config) permitir. Senão `PATTERN_NOT_SUPPORTED`.

**Retorno**

```json
{"ok":true,"ref":"w6-e44","scrolled_ref":"w6-e40","method":"ScrollPattern",
 "fallback_used":false,"v_percent_before":0.0,"v_percent_after":24.5,
 "h_percent_after":0.0,"at_end":false,
 "note":"Content changed. Refs below the fold may be new; re-run uia_get_tree if needed."}
```

**Erros:** `REF_NOT_FOUND`, `STALE_REF`, `PATTERN_NOT_SUPPORTED`, `NOT_SCROLLABLE` (pattern presente mas `VerticallyScrollable=false`), `READ_ONLY_MODE`, `APP_NOT_ALLOWED`.

---

### 8.11 `uia_focus_window`

**Description:** `Bring a window to the foreground and give it keyboard focus. Required before sending keys to a background window; also restores minimized windows.`

**Parâmetros**

| Nome | Tipo | Obrig. | Default | Descrição |
|---|---|---|---|---|
| `window_ref` | string | sim | — | Janela alvo |
| `restore_if_minimized` | bool | não | `true` | `WindowPattern.SetWindowVisualState(Normal)` |
| `timeout_ms` | int | não | `3000` | Espera confirmação de foreground |

**Implementação:** `WindowPattern.SetWindowVisualState` quando disponível; depois `SetForegroundWindow(hwnd)`. Se o Windows recusar (regra de *foreground lock*: só o processo em foreground pode transferir foco), aplicar o workaround padrão — `AttachThreadInput(currentThreadId, foregroundThreadId, TRUE)` → `BringWindowToTop` + `SetForegroundWindow` → `AttachThreadInput(..., FALSE)`. Confirmar com poll de `GetForegroundWindow()` até `timeout_ms`. Não conseguiu → `FOCUS_FAILED`.

**Retorno**

```json
{"ok":true,"window_ref":"w2","focused":true,"restored":true,
 "previous_focus":{"window_ref":"w1","title":"Sem título - Bloco de Notas"},
 "waited_ms":180}
```

**Erros:** `WINDOW_NOT_FOUND`, `WINDOW_CLOSED`, `FOCUS_FAILED`, `READ_ONLY_MODE`, `APP_NOT_ALLOWED`, `ELEVATION_REQUIRED`.

---

## 9. Tratamento de erro

### 9.1 Formato

```json
{
  "ok": false,
  "error": {
    "code": "ELEMENT_DISABLED",
    "message": "Element w4-e12 ('Salvar', Button) is disabled and cannot be invoked.",
    "hint": "Something upstream is blocking it — a required field may be empty. Call uia_get_tree(root_ref='w4-e5', filter='interactive') to inspect the form, fill the missing input with uia_set_value, then retry.",
    "details": {"ref":"w4-e12","name":"Salvar","type":"Button","window_ref":"w4","retryable":false}
  }
}
```

Regras: `message` descreve o fato; `hint` prescreve a **próxima ação concreta** (nomeando a tool a chamar); `details.retryable` diz se repetir a mesma chamada pode funcionar.

### 9.2 Catálogo de códigos

| Código | Quando | `hint` (padrão) | Retryable |
|---|---|---|---|
| `INVALID_ARGUMENT` | Parâmetro ausente/inválido | Descreve o parâmetro correto e valores aceitos | não |
| `SESSION_UNAVAILABLE` | Sessão bloqueada/desconectada | `The desktop session is locked or not interactive. Ask the user to unlock the machine.` | sim |
| `WINDOW_NOT_FOUND` | `window_ref`/título sem correspondência | `Call uia_list_windows to get current window refs.` | não |
| `WINDOW_CLOSED` | Janela existia e sumiu | `The window was closed. Call uia_list_windows; the app may need to be reopened.` | não |
| `APP_NOT_ALLOWED` | Fora da allowlist | `'<process>' is not in the allowlist. Ask the user to add it to config.toml [allowlist] and restart the server.` | não |
| `READ_ONLY_MODE` | Tool mutante com `--read-only` | `Server is in read-only mode; only inspection tools work. Ask the user to restart without --read-only.` | não |
| `BLOCKED_ACTION` | Atalho/ação na denylist | `This shortcut is blocked by policy. Achieve the goal through the UI (uia_find_elements + uia_click).` | não |
| `ELEVATION_REQUIRED` | Alvo elevado, servidor não | `The target app runs elevated and is invisible to this server (UIPI). Ask the user to run the app non-elevated, or the server as administrator.` | não |
| `REF_NOT_FOUND` | Ref desconhecida/evictada | `Unknown ref. Call uia_get_tree or uia_find_elements to obtain fresh refs.` | não |
| `STALE_REF` | Elemento morreu/mudou | `The UI changed. Re-capture with uia_get_tree(window_ref=..., filter='interactive') and use the new ref.` | não |
| `AMBIGUOUS_MATCH` | >1 candidato no rebind/busca exata | `Multiple elements match. Pick one of details.candidates and call again with that ref.` | não |
| `ELEMENT_NOT_FOUND` | Busca sem resultado | `Loosen the criteria (match='contains'), or call uia_get_tree(filter='interactive') to see what exists.` | sim (com `timeout_ms`) |
| `ELEMENT_DISABLED` | `IsEnabled=false` | Ver exemplo §9.1 | sim |
| `ELEMENT_READONLY` | `ValuePattern.IsReadOnly=true` | `This field is read-only. Use uia_get_value to read it; it cannot be written.` | não |
| `ELEMENT_OFFSCREEN` | Fora da viewport e sem scroll possível | `Call uia_scroll(ref=..., direction='into_view') first, then retry.` | sim |
| `PATTERN_NOT_SUPPORTED` | Controle não expõe o pattern pedido | `This control does not support <Pattern>. Try uia_click(action='mouse'), or uia_send_keys with the app shortcut.` | não |
| `NOT_SCROLLABLE` | `ScrollPattern` presente mas eixo não rolável | `Content fits; nothing to scroll on this axis.` | não |
| `VERIFY_FAILED` | Valor escrito ≠ lido de volta | `The control rejected or reformatted the input. Check details.actual and adjust (masks, max length, allowed characters).` | sim |
| `FOCUS_FAILED` | Não conseguiu foreground | `Windows refused the focus change. Ask the user to click the window once, then retry.` | sim |
| `TIMEOUT` | Estourou espera/COM | `Increase timeout_ms, or inspect the current state with uia_get_tree before retrying.` | sim |
| `ACTION_RATE_LIMITED` | Excedeu `max_actions_per_minute` | `Too many mutating actions. Wait <n> s and retry; batch your work.` | sim |
| `UIA_COM_ERROR` | `HRESULT` não mapeado | `Unexpected UI Automation failure (details.hresult). The app may be busy or hung. Retry once; if it persists, report to the user.` | sim |

Mapeamento de `HRESULT` obrigatório: `0x80040201 UIA_E_ELEMENTNOTAVAILABLE` → `STALE_REF`; `0x80040200 UIA_E_INVALIDOPERATION` → `PATTERN_NOT_SUPPORTED`; `0x80131505 UIA_E_TIMEOUT` → `TIMEOUT`; `0x80070005 E_ACCESSDENIED` → `ELEVATION_REQUIRED`; `0x800706BA RPC_S_SERVER_UNAVAILABLE` → `WINDOW_CLOSED`.

---

## 10. Segurança

Este servidor concede **controle total do desktop** a um modelo de linguagem. As três medidas abaixo são requisito de release, não opcionais.

### 10.1 Allowlist de aplicações

Arquivo `config.toml`, recarregado apenas no startup (mudança em runtime exige restart — evita escalonamento por escrita no arquivo durante a sessão).

```toml
[server]
read_only = false
hide_denied = false
allow_coordinate_fallback = true
max_actions_per_minute = 60
settle_ms = 250
ref_ttl_s = 300

[allowlist]
mode = "allow"        # "allow" = só o que está listado; "deny_all" = nada (kill switch)
processes = ["Notepad.exe", "CalculatorApp.exe", "explorer.exe", "WINWORD.EXE", "EXCEL.EXE"]
# opcional: restringe por título dentro do processo (regex, case-insensitive)
title_patterns = { "explorer.exe" = "^(?!.*Painel de Controle).*$" }

[denylist]
# avaliada ANTES da allowlist; sempre vence
processes = ["consent.exe", "CredentialUIBroker.exe", "LogonUI.exe", "keepass.exe",
             "1Password.exe", "Bitwarden.exe", "mstsc.exe"]
title_patterns = ["(?i)(senha|password|banco|banking|carteira|wallet|seed phrase)"]

[audit]
dir = "%LOCALAPPDATA%\\mcp-windows-uia\\audit"
retain_days = 30
log_values = "redacted"   # "full" | "redacted" | "none"

[keys]
blocked = ["{ALT+F4}", "{WIN+L}", "{WIN+R}", "{CTRL+SHIFT+ESC}"]
```

Regras:

- Avaliação: denylist → allowlist → decisão. Default de fábrica é **allowlist vazia** = nada permitido; o servidor loga um aviso no stderr no startup e toda tool retorna `APP_NOT_ALLOWED` com hint dizendo como configurar. Falha fechada, não aberta.
- Match de processo é por **nome do executável**, case-insensitive, resolvido via `psutil.Process(pid).name()`. Nunca por título apenas (título é forjável pelo conteúdo da página/documento).
- `uia_list_windows` é a única tool que enxerga janelas negadas (para orientação), e mesmo assim só expõe título, processo e `allowed:false`.

### 10.2 Modo somente-leitura

- Flag CLI `--read-only` e `[server] read_only = true`. CLI vence.
- Bloqueia: `uia_click`, `uia_set_value`, `uia_send_keys`, `uia_scroll`, `uia_focus_window`. Libera: as 6 tools de leitura/busca/espera.
- Estado é reportado em `uia_list_windows` → `"read_only": true`, para o agente saber antes de tentar.
- Recomendação documentada ao usuário: começar em read-only ao apontar o servidor para um app novo.

### 10.3 Auditoria

JSONL, um arquivo por dia, `append-only`, uma linha por chamada de tool (sucesso **e** falha):

```json
{"ts":"2026-08-19T14:03:22.417-03:00","seq":184,"tool":"uia_set_value",
 "target":{"window_ref":"w1","hwnd":723918,"pid":12044,"process":"Notepad.exe","title":"Sem título - Bloco de Notas"},
 "element":{"ref":"w1-e7","type":"Edit","name":"Editor de texto","aid":"RichEditBox"},
 "params":{"mode":"replace","method":"auto","chars":32},
 "value":"Relatório trimestral — rascunho",
 "result":"ok","method_used":"ValuePattern","fallback_used":false,
 "duration_ms":118,"read_only":false}
```

- `log_values = "redacted"` (default): grava `"value":"«redacted»","value_len":32,"value_sha256":"<hex8>"` — permite auditar *que* algo foi escrito sem vazar conteúdo.
- Elementos com `IsPassword=true`: valor **nunca** é gravado, em nenhum modo.
- Toda negação de policy também é logada (`"result":"denied","code":"APP_NOT_ALLOWED"`).
- Rotação diária, retenção `retain_days`, permissões do diretório restritas ao usuário.
- **stdout é sagrado**: nenhum log vai para stdout (é o canal JSON-RPC). Logs operacionais → stderr; auditoria → arquivo.

### 10.4 Superfície residual (documentar ao usuário)

O servidor pode, dentro da allowlist: ler qualquer texto na tela daqueles apps (incluindo conteúdo sensível), digitar em qualquer campo, acionar qualquer botão. Não há confirmação humana por ação — se isso for necessário, é responsabilidade do cliente MCP (Claude Desktop) pedir aprovação de tool. Documentar explicitamente no README que injeção de prompt via conteúdo de tela é um vetor real: texto lido de um app pode conter instruções maliciosas dirigidas ao agente. O servidor mitiga apenas por escopo (allowlist) e reversibilidade (auditoria).

---

## 11. Transporte MCP e instalação

### 11.1 Servidor mínimo (stdio)

```python
# src/mcp_windows_uia/__main__.py
from __future__ import annotations

import argparse, ctypes, logging, sys
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer  # mcp>=2.0; na 1.x era mcp.server.fastmcp.FastMCP
from pydantic import Field

# 1) DPI awareness ANTES de qualquer coisa de UI
def _init_dpi() -> None:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except OSError:
            pass

# 2) Logging SEMPRE em stderr — stdout é o canal JSON-RPC
logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

mcp = MCPServer("windows-uia")


@mcp.tool()
async def uia_list_windows(
    include_minimized: Annotated[bool, Field(description="Include minimized windows.")] = True,
    include_hidden: Annotated[bool, Field(description="Include windows without a title or not visible.")] = False,
    process_filter: Annotated[str | None, Field(description="Case-insensitive substring of process name or title.")] = None,
    max_results: Annotated[int, Field(ge=1, le=200)] = 40,
) -> dict:
    """List top-level windows currently open on the desktop, with a stable window ref for each.
    Call this first to discover what applications are available before capturing a UI tree."""
    from .worker import run_in_uia
    from .uia.windows import list_windows
    return await run_in_uia(list_windows, include_minimized, include_hidden,
                            process_filter, max_results)


@mcp.tool()
async def uia_get_tree(
    window_ref: str | None = None,
    root_ref: str | None = None,
    filter: Literal["interactive", "content", "all", "landmarks"] = "interactive",
    max_depth: Annotated[int, Field(ge=1, le=40)] = 12,
    max_nodes: Annotated[int, Field(ge=1, le=1500)] = 200,
    max_children_per_node: Annotated[int, Field(ge=1, le=500)] = 30,
    cursor: str | None = None,
    verbose: bool = False,
    timeout_ms: Annotated[int, Field(ge=0, le=60000)] = 0,
) -> dict:
    """Capture the UI Automation tree of a window as a flat, pre-order list of nodes with
    stable refs. Defaults to interactive elements only. Use root_ref to drill into a subtree."""
    from .worker import run_in_uia
    from .uia.tree import get_tree
    return await run_in_uia(get_tree, window_ref, root_ref, filter, max_depth,
                            max_nodes, max_children_per_node, cursor, verbose, timeout_ms)


# ... demais 9 tools seguem o mesmo padrão ...


def main() -> None:
    parser = argparse.ArgumentParser(prog="mcp-windows-uia")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--read-only", action="store_true")
    args = parser.parse_args()

    _init_dpi()
    from .policy import load_config
    load_config(args.config, force_read_only=args.read_only)
    logging.info("mcp-windows-uia starting (read_only=%s)", args.read_only)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

Requisitos do transporte:

- **Nada** pode ser escrito em `stdout` além do protocolo. Bibliotecas barulhentas (`comtypes` em debug) devem ter logger reconfigurado. Em caso de dúvida, redirecionar `sys.stdout` para `sys.stderr` logo após capturar o stream original que o SDK usa.
- Sempre executar Python com `-u` (unbuffered) para evitar bufferização de respostas.
- Toda exceção não tratada dentro de uma tool deve virar erro estruturado (§9), nunca derrubar o processo. Um decorator `@tool_errors` centraliza isso.

### 11.2 Registro no Claude Desktop

Arquivo: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "windows-uia": {
      "command": "C:\\tools\\mcp-windows-uia\\.venv\\Scripts\\python.exe",
      "args": [
        "-u", "-m", "mcp_windows_uia",
        "--config", "C:\\tools\\mcp-windows-uia\\config.toml"
      ],
      "env": {
        "PYTHONPATH": "C:\\tools\\mcp-windows-uia\\src",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

Passos de instalação a documentar no README:

1. `py -3.14 -m venv .venv` (Python 64-bit) e `.venv\Scripts\pip install -e .`
2. Editar `config.toml`: preencher `[allowlist] processes`.
3. Smoke test fora do Claude: `.venv\Scripts\python -u -m mcp_windows_uia --config config.toml --read-only` e enviar um `initialize` + `tools/list` por stdin (ou usar `mcp dev`).
4. Adicionar o bloco acima ao `claude_desktop_config.json` e reiniciar o Claude Desktop **completamente** (encerrar pela bandeja, não só fechar a janela).
5. Verificar que as 11 tools aparecem na lista de ferramentas do app.

Se usar `uv`: `"command": "uv"`, `"args": ["--directory", "C:\\tools\\mcp-windows-uia", "run", "mcp-windows-uia"]`.

---

## 12. Critérios de aceitação

Cenários executáveis. Cada um é PASS/FAIL objetivo. Os automatizáveis vivem em `tests/e2e/` e rodam contra o servidor via cliente MCP stdio real (não chamando funções Python diretamente). Ambiente de referência: Windows 11, monitor primário a 100% e secundário a 150%.

| ID | Cenário | Passos | Critério de PASS |
|---|---|---|---|
| **CA-01** | Descoberta de janelas | Abrir Bloco de Notas; chamar `uia_list_windows` | Resposta contém entrada com `process="Notepad.exe"`, `ref` no formato `w<n>`, `pid` correto, `focused=true`, `rect` não vazio. Tempo < 1,5 s |
| **CA-02** | Árvore filtrada e enxuta | `uia_get_tree(window_ref, filter="interactive")` no Bloco de Notas | Retorna ≤ 60 nós; contém exatamente um nó com `type="Edit"` ou `type="Document"` correspondente à área de edição; `stats.truncated=false`; nenhum nó com `disabled` em `st` |
| **CA-03** | **Round-trip de texto** | Localizar a área de edição pela árvore (sem hardcode de `AutomationId`); `uia_set_value(ref, "Olá do agente 123 — ção")`; depois `uia_get_value(ref)` | `uia_set_value` retorna `method="ValuePattern"` **ou** `fallback_used=true` com `verified=true`; `uia_get_value.value` é exatamente `"Olá do agente 123 — ção"` (normalizando `\r\n`). Acentos e travessão preservados |
| **CA-04** | **Menu por pattern, sem coordenada** | `uia_find_elements(name="Arquivo", control_type="MenuItem")`; `uia_click(ref, allow_coordinate_fallback=false)`; `uia_wait_for(condition="appears", name="Salvar como", timeout_ms=3000)`; `uia_click` no item | Ambos os cliques retornam `fallback_used=false` e `method` ∈ {`InvokePattern`,`ExpandCollapsePattern`}; o diálogo "Salvar como" aparece em `uia_list_windows`. Nenhuma coordenada foi passada pelo chamador em nenhum momento |
| **CA-05** | Toggle de checkbox | Abrir um app com checkbox (ex.: Bloco de Notas → Formatar → Quebra automática de linha, ou Configurações); ler `uia_get_value` (estado A); `uia_click`; ler de novo | `method="TogglePattern"`, `fallback_used=false`, e o valor muda de `on`↔`off` de forma consistente com `st` (`checked`/`unchecked`) |
| **CA-06** | Ref obsoleta detectada | `uia_get_tree` no Bloco de Notas; fechar a janela (sem salvar); usar um `ref` de elemento em `uia_click` | Erro `STALE_REF` ou `WINDOW_CLOSED`, com `hint` mencionando `uia_get_tree`/`uia_list_windows`. **Sem** exceção Python vazando, sem travamento, servidor continua respondendo à chamada seguinte |
| **CA-07** | Rebind automático | Numa lista (Explorador de Arquivos), capturar ref de um item; forçar re-render (`F5`); usar o ref em `uia_get_value` | Sucesso com `"rebound": true`, e o valor corresponde ao mesmo item lógico (mesmo `Name`) |
| **CA-08** | Truncamento e paginação | `uia_get_tree` em janela grande (Configurações do Windows ou Explorador) com `filter="all", max_nodes=100` | `stats.truncated=true`, `stats.returned=100`, `next_cursor` não nulo, e `stats.hint` presente. Chamada seguinte com `cursor` retorna nós **diferentes** (interseção vazia de `ref`) e avança |
| **CA-09** | Limite absoluto | `uia_get_tree(filter="all", max_nodes=99999)` | `INVALID_ARGUMENT` **ou** clamp para 1500 com truncamento sinalizado. Em nenhum caso a resposta excede 1500 nós |
| **CA-10** | Espera por elemento assíncrono | Com Bloco de Notas em foco: `uia_send_keys("{CTRL+S}")` e imediatamente `uia_wait_for(condition="window_appears", name="Salvar", timeout_ms=5000)` | `satisfied=true`, `waited_ms < 5000`, e a ref retornada resolve num `uia_get_value` subsequente |
| **CA-11** | Timeout acionável | `uia_wait_for` por um elemento inexistente, `timeout_ms=1500` | `TIMEOUT` em 1500–2000 ms, com `hint` sugerindo `uia_get_tree` e `details.polls > 5` |
| **CA-12** | Extração de texto linear | `uia_get_text` numa janela com conteúdo conhecido (Bloco de Notas com 3 linhas) | As 3 linhas aparecem em ordem, separadas por `\n`, sem duplicação de conteúdo por ancestral/descendente, `chars` bate com `len(text)` |
| **CA-13** | Allowlist bloqueia | Remover `CalculatorApp.exe` da allowlist; reiniciar; abrir Calculadora; `uia_get_tree` nela | `APP_NOT_ALLOWED` com nome do processo no `message` e instrução de config no `hint`; a janela **aparece** em `uia_list_windows` com `allowed:false`; o log de auditoria contém uma linha `"result":"denied"` |
| **CA-14** | Modo somente-leitura | Iniciar com `--read-only`; chamar `uia_get_tree` e depois `uia_click` | `uia_get_tree` sucede; `uia_click` retorna `READ_ONLY_MODE`; `uia_list_windows` reporta `"read_only": true` |
| **CA-15** | Redação de senha | Num app com campo de senha (`IsPassword=true`), `uia_set_value(ref, "s3nh4-secreta")` e `uia_get_value(ref)` | Nem a resposta nem o arquivo de auditoria contêm a string `s3nh4-secreta` em lugar nenhum; `uia_get_value.value == "«redacted:password»"` |
| **CA-16** | Auditoria completa | Executar CA-03 inteiro; inspecionar o JSONL do dia | Uma linha por chamada de tool, com `tool`, `target.process`, `target.pid`, `element.ref`, `result`, `duration_ms`; timestamps monotônicos; JSON válido linha a linha |
| **CA-17** | DPI e monitor secundário | Mover o Bloco de Notas para o monitor a 150%; `uia_get_tree`; forçar `uia_click(action="mouse")` num botão | `rect` corresponde à posição física real (verificável por screenshot com margem de ±2 px); o clique atinge o controle correto (efeito observável), inclusive com coordenada X negativa se o monitor estiver à esquerda |
| **CA-18** | Elevação | Abrir Gerenciador de Tarefas (elevado) com servidor não elevado; `uia_get_tree` | `ELEVATION_REQUIRED` com hint explicando UIPI. **Não** retorna árvore vazia silenciosamente nem trava |
| **CA-19** | Pattern ausente | Num controle `Custom` sem patterns, `uia_click(allow_coordinate_fallback=false)` | `PATTERN_NOT_SUPPORTED` nomeando o control type e sugerindo `action="mouse"` ou `uia_send_keys`. Com `allow_coordinate_fallback=true`, sucede com `fallback_used=true` |
| **CA-20** | Scroll semântico | Abrir Explorador com pasta de >100 itens; `uia_find_elements` por um item fora da viewport; `uia_scroll(ref, direction="into_view")`; `uia_get_value` | `method="ScrollItemPattern"`, `fallback_used=false`; após o scroll, o item tem `offscreen` ausente de `st` |
| **CA-21** | Performance de árvore | `uia_get_tree(filter="interactive", max_nodes=200)` na Calculadora, 5 execuções | p95 < 1200 ms. Falha se a implementação estiver lendo propriedades sem `CacheRequest` (verificável por profiling: nº de chamadas COM ≈ 1–3, não ≈ N×M) |
| **CA-22** | Higiene de stdout | Rodar o servidor e executar 20 chamadas variadas, capturando stdout bruto | Todo byte em stdout é JSON-RPC válido; nenhum log, warning de `comtypes`, `print` ou traceback contamina o canal |
| **CA-23** | Robustez a app travado | Apontar para um app que parou de bombear mensagens (simulável com um script que bloqueia o thread de UI por 60 s); `uia_get_tree` | Retorna `TIMEOUT` em ≤ 15 s; a chamada seguinte a outra janela funciona normalmente (a thread UIA não ficou envenenada) |
| **CA-24** | Ambiguidade explícita | Janela com dois botões de mesmo `Name` e sem `AutomationId`; `uia_find_elements(name="OK", match="exact")` | Retorna **os dois** com `path` distintos (não escolhe sozinho). Se um rebind ficar ambíguo, retorna `AMBIGUOUS_MATCH` com `details.candidates` |
| **CA-25** | Liberação de modificadores | `uia_send_keys("{CTRL+SHIFT+N}")` provocando exceção artificial no meio do envio | Após o erro, `GetAsyncKeyState` de `CTRL`/`SHIFT`/`ALT`/`WIN` indica todos soltos |

**Definição de pronto (v1):** CA-01 a CA-25 passam; `tools/list` expõe exatamente as 11 tools com descrições e schemas desta spec; README cobre instalação, allowlist, read-only, auditoria e limitações de elevação/DPI.

---

## 13. Referências

- UI Automation — visão geral e control patterns: https://learn.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32
- `IUIAutomationElement`, `RuntimeId` e cache: https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationelement
- Caching em UI Automation clients: https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-cachingforclients
- DPI awareness (`SetProcessDpiAwarenessContext`): https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setprocessdpiawarenesscontext
- `SendInput` e coordenadas absolutas em desktop virtual: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput
- User Interface Privilege Isolation (UIPI): https://learn.microsoft.com/en-us/windows/win32/winmsg/about-messages-and-message-queues
- Model Context Protocol — especificação: https://modelcontextprotocol.io/specification
- SDK Python do MCP: https://github.com/modelcontextprotocol/python-sdk
- `uiautomation` (Python): https://github.com/yinkaisheng/Python-UIAutomation-for-Windows
- `pywinauto` — backend UIA: https://pywinauto.readthedocs.io/en/latest/
- `comtypes`: https://github.com/enthought/comtypes
