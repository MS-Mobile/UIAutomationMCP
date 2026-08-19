# mcp-windows-uia — Plano 1/3: Fundação Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar o esqueleto do servidor MCP `windows-uia` com toda a infraestrutura transversal (erros, config, policy, auditoria, orçamento, refs, thread STA, DPI, cliente UIA) e a primeira tool funcional, `uia_list_windows`, passando os critérios de aceitação CA-01 e CA-22.

**Architecture:** Servidor MCP stdio em Python. Todas as chamadas COM correm numa única thread STA (`ThreadPoolExecutor(max_workers=1)`); ponteiros COM nunca cruzam threads. O cliente UIA é `CUIAutomation8` obtido via `comtypes.client.CreateObject` com `ConnectionTimeout`/`TransactionTimeout` = 10 s — é isso que impede que um app travado pendure o servidor. A enumeração de janelas usa `EnumWindows` (Win32) em vez da árvore UIA, porque só o Win32 dá `WS_VISIBLE`/`IsIconic`/placement de forma barata e confiável. As camadas puras (erros, config, policy, budget, refs, audit) não tocam COM e são testadas isoladamente.

**Tech Stack:** Python 3.14 x64 (piso 3.11), `mcp` (FastMCP, stdio), `comtypes>=1.4.16` (+ `GetModule("UIAutomationCore.dll")`), `psutil`, `pytest`, `pytest-asyncio`. **Sem `uiautomation`** — ver §3.3 da spec.

**Spec de referência:** `spec-mcp-windows-uia.md`, seções citadas em cada task.

---

## Contexto que o executor precisa saber antes de começar

1. **`stdout` é sagrado.** É o canal JSON-RPC. Nenhum `print`, log ou warning pode ir para lá. Logs → `stderr`. Auditoria → arquivo. Violar isso quebra o CA-22 e o servidor inteiro dentro do Claude Desktop.
2. **COM tem afinidade de apartamento.** Um `IUIAutomationElement` criado na thread A não pode ser usado na thread B (`RPC_E_WRONG_THREAD`, ou pior, corrupção silenciosa). Por isso existe `worker.py`. Nunca chame nada de `uia/` fora de `worker.run(...)`.
3. **`comtypes.client.GetModule` gera código na primeira execução** (~142 ms) dentro de `comtypes/gen/`. É idempotente e cacheado. Não commitar `comtypes/gen`.
4. **DPI antes de tudo.** `SetProcessDpiAwarenessContext` precisa rodar antes de qualquer chamada Win32/UIA que envolva coordenadas, senão os retângulos vêm virtualizados e o clique de fallback (Plano 3) erra o alvo.
5. **Este plano não implementa rebind de refs.** O algoritmo `resolve()` da spec §7.2 (re-resolução por `AutomationId` / `Name` / `index_path`) depende de busca na árvore e vem no Plano 2. Aqui o `RefStore` só armazena, expira e invalida.

---

### Task 1: Esqueleto do projeto e ambiente

**Files:**
- Create: `pyproject.toml`
- Create: `src/mcp_windows_uia/__init__.py`
- Create: `tests/conftest.py`
- Create: `config.example.toml`
- Modify: `.gitignore`

- [ ] **Step 1: Criar `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "mcp-windows-uia"
version = "0.1.0"
description = "MCP server exposing Windows UI Automation to an AI agent"
requires-python = ">=3.11"
dependencies = [
  "mcp>=1.2.0",
  "comtypes>=1.4.16",
  "psutil>=5.9",
]

[project.scripts]
mcp-windows-uia = "mcp_windows_uia.__main__:main"

[tool.hatch.build.targets.wheel]
packages = ["src/mcp_windows_uia"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
  "e2e: exige sessao interativa do Windows desbloqueada",
]
```

- [ ] **Step 2: Criar o pacote e o conftest**

`src/mcp_windows_uia/__init__.py`:

```python
"""MCP server exposing Windows UI Automation to an AI agent."""

__version__ = "0.1.0"
```

`tests/conftest.py`:

```python
from __future__ import annotations

import sys

import pytest


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Pula testes marcados como e2e fora do Windows."""
    if "e2e" in item.keywords and not sys.platform.startswith("win"):
        pytest.skip("e2e exige Windows")
```

- [ ] **Step 3: Criar `config.example.toml`**

Este é o arquivo que o usuário copia para `config.toml`. `[allowlist] processes` vem **vazio**: a spec §10.1 exige falha fechada.

```toml
[server]
read_only = false
hide_denied = false
allow_coordinate_fallback = true
max_actions_per_minute = 60
settle_ms = 250
ref_ttl_s = 300
ref_cache_max = 5000
com_timeout_ms = 15000

[allowlist]
# "allow" = so o que esta listado. "deny_all" = kill switch, nada passa.
mode = "allow"
# VAZIO DE PROPOSITO: preencha antes de usar. Vazio == nada e permitido.
processes = []
# Opcional: restringe por titulo dentro de um processo (regex).
# title_patterns = { "explorer.exe" = "^Documentos" }

[denylist]
# Avaliada ANTES da allowlist; sempre vence.
processes = ["consent.exe", "CredentialUIBroker.exe", "LogonUI.exe",
             "keepass.exe", "1Password.exe", "Bitwarden.exe", "mstsc.exe"]
title_patterns = ["(?i)(senha|password|banco|banking|carteira|wallet|seed phrase)"]

[audit]
dir = "%LOCALAPPDATA%\\mcp-windows-uia\\audit"
retain_days = 30
log_values = "redacted"   # "full" | "redacted" | "none"

[keys]
blocked = ["{ALT+F4}", "{WIN+L}", "{WIN+R}", "{CTRL+SHIFT+ESC}"]
```

- [ ] **Step 4: Acrescentar ao `.gitignore`**

```
comtypes/
config.toml
```

- [ ] **Step 5: Criar a venv e instalar**

```bash
py -3.14 -m venv .venv
.venv/Scripts/python.exe -m pip install -q --upgrade pip
.venv/Scripts/python.exe -m pip install -q -e . pytest pytest-asyncio
```

- [ ] **Step 6: Verificar que a suíte roda vazia**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `no tests ran` (exit code 5). Confirma que o `pyproject.toml` é válido e o pacote é importável.

Run: `.venv/Scripts/python.exe -c "import mcp_windows_uia; print(mcp_windows_uia.__version__)"`
Expected: `0.1.0`

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src tests config.example.toml .gitignore
git commit -m "chore: esqueleto do projeto e ambiente de testes"
```

---

### Task 2: `errors.py` — códigos, hints e mapeamento de HRESULT

**Files:**
- Create: `src/mcp_windows_uia/errors.py`
- Test: `tests/test_errors.py`

Implementa a spec §9. Regra central: `message` descreve o fato, `hint` prescreve a próxima tool a chamar, `details.retryable` diz se repetir pode funcionar.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_errors.py`:

```python
from __future__ import annotations

import pytest

from mcp_windows_uia.errors import (
    DEFAULT_HINTS,
    Code,
    ToolError,
    from_com_error,
    hresult_to_code,
)


def test_tool_error_serializa_no_formato_da_spec() -> None:
    err = ToolError(
        Code.ELEMENT_DISABLED,
        "Element w4-e12 ('Salvar', Button) is disabled and cannot be invoked.",
        ref="w4-e12",
        name="Salvar",
        type="Button",
        window_ref="w4",
    )
    d = err.to_dict()

    assert d["ok"] is False
    assert d["error"]["code"] == "ELEMENT_DISABLED"
    assert d["error"]["message"].startswith("Element w4-e12")
    assert d["error"]["details"]["ref"] == "w4-e12"
    assert d["error"]["details"]["retryable"] is True


def test_hint_padrao_nomeia_a_proxima_tool() -> None:
    err = ToolError(Code.WINDOW_NOT_FOUND, "Window w9 does not exist.")
    assert "uia_list_windows" in err.to_dict()["error"]["hint"]


def test_hint_explicito_sobrepoe_o_padrao() -> None:
    err = ToolError(Code.TIMEOUT, "Nope.", hint="Custom next step.")
    assert err.to_dict()["error"]["hint"] == "Custom next step."


def test_todo_codigo_tem_hint_padrao() -> None:
    """Principio 3 da spec: erros sao instrucoes. Nenhum codigo fica sem hint."""
    faltando = [c.value for c in Code if c not in DEFAULT_HINTS]
    assert faltando == []


@pytest.mark.parametrize(
    ("hresult", "esperado"),
    [
        (0x80040201, Code.STALE_REF),
        (0x80040200, Code.PATTERN_NOT_SUPPORTED),
        (0x80131505, Code.TIMEOUT),
        (0x80070005, Code.ELEVATION_REQUIRED),
        (0x800706BA, Code.WINDOW_CLOSED),
        (0x8000FFFF, Code.UIA_COM_ERROR),
    ],
)
def test_mapeamento_de_hresult(hresult: int, esperado: Code) -> None:
    assert hresult_to_code(hresult) is esperado


def test_hresult_negativo_e_normalizado() -> None:
    """comtypes entrega HRESULT com sinal; 0x80040201 chega como -2147220991."""
    assert hresult_to_code(-2147220991) is Code.STALE_REF


class _FakeCOMError(Exception):
    def __init__(self, hresult: int) -> None:
        super().__init__(hresult, "fake", None)
        self.hresult = hresult
        self.text = "fake com failure"


def test_from_com_error_preserva_hresult_nos_details() -> None:
    err = from_com_error(_FakeCOMError(-2147220991), ref="w3-e142")
    assert err.code is Code.STALE_REF
    assert err.details["ref"] == "w3-e142"
    assert err.details["hresult"] == "0x80040201"


def test_from_com_error_sem_hresult_cai_no_catch_all() -> None:
    err = from_com_error(ValueError("algo estranho"))
    assert err.code is Code.UIA_COM_ERROR
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_errors.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.errors'`

- [ ] **Step 3: Implementar `errors.py`**

```python
"""Codigos de erro, hints e serializacao. Spec §9."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class Code(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    SESSION_UNAVAILABLE = "SESSION_UNAVAILABLE"
    WINDOW_NOT_FOUND = "WINDOW_NOT_FOUND"
    WINDOW_CLOSED = "WINDOW_CLOSED"
    APP_NOT_ALLOWED = "APP_NOT_ALLOWED"
    READ_ONLY_MODE = "READ_ONLY_MODE"
    BLOCKED_ACTION = "BLOCKED_ACTION"
    ELEVATION_REQUIRED = "ELEVATION_REQUIRED"
    REF_NOT_FOUND = "REF_NOT_FOUND"
    STALE_REF = "STALE_REF"
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
    ELEMENT_DISABLED = "ELEMENT_DISABLED"
    ELEMENT_READONLY = "ELEMENT_READONLY"
    ELEMENT_OFFSCREEN = "ELEMENT_OFFSCREEN"
    PATTERN_NOT_SUPPORTED = "PATTERN_NOT_SUPPORTED"
    NOT_SCROLLABLE = "NOT_SCROLLABLE"
    VERIFY_FAILED = "VERIFY_FAILED"
    FOCUS_FAILED = "FOCUS_FAILED"
    TIMEOUT = "TIMEOUT"
    ACTION_RATE_LIMITED = "ACTION_RATE_LIMITED"
    UIA_COM_ERROR = "UIA_COM_ERROR"


DEFAULT_HINTS: dict[Code, str] = {
    Code.INVALID_ARGUMENT: (
        "Check the parameter names, types and allowed values in the tool schema, "
        "then call again."
    ),
    Code.SESSION_UNAVAILABLE: (
        "The desktop session is locked or not interactive. Ask the user to unlock the machine."
    ),
    Code.WINDOW_NOT_FOUND: "Call uia_list_windows to get current window refs.",
    Code.WINDOW_CLOSED: (
        "The window was closed. Call uia_list_windows; the app may need to be reopened."
    ),
    Code.APP_NOT_ALLOWED: (
        "This application is not in the allowlist. Ask the user to add its executable name "
        "to config.toml under [allowlist] processes and restart the server."
    ),
    Code.READ_ONLY_MODE: (
        "Server is in read-only mode; only inspection tools work. "
        "Ask the user to restart it without --read-only."
    ),
    Code.BLOCKED_ACTION: (
        "This shortcut is blocked by policy. Achieve the goal through the UI "
        "(uia_find_elements + uia_click)."
    ),
    Code.ELEVATION_REQUIRED: (
        "The target app runs elevated and is invisible to this server (UIPI). Ask the user to "
        "run the app non-elevated, or to run the server as administrator."
    ),
    Code.REF_NOT_FOUND: (
        "Unknown ref. Call uia_get_tree or uia_find_elements to obtain fresh refs."
    ),
    Code.STALE_REF: (
        "The UI changed. Re-capture with uia_get_tree(window_ref=..., filter='interactive') "
        "and use the new ref."
    ),
    Code.AMBIGUOUS_MATCH: (
        "Multiple elements match. Pick one of details.candidates and call again with that ref."
    ),
    Code.ELEMENT_NOT_FOUND: (
        "No element matched. Try match='contains' with a shorter name, drop control_type, "
        "or call uia_get_tree(filter='interactive') to see what is actually there."
    ),
    Code.ELEMENT_DISABLED: (
        "Something upstream is blocking it — a required field may be empty. Inspect the form "
        "with uia_get_tree(root_ref=..., filter='interactive'), fill the missing input with "
        "uia_set_value, then retry."
    ),
    Code.ELEMENT_READONLY: (
        "This field is read-only. Use uia_get_value to read it; it cannot be written."
    ),
    Code.ELEMENT_OFFSCREEN: "Call uia_scroll(ref=..., direction='into_view') first, then retry.",
    Code.PATTERN_NOT_SUPPORTED: (
        "This control does not support the requested pattern. Try uia_click(action='mouse'), "
        "or uia_send_keys with the application's own shortcut."
    ),
    Code.NOT_SCROLLABLE: "Content fits; nothing to scroll on this axis.",
    Code.VERIFY_FAILED: (
        "The control rejected or reformatted the input. Check details.actual and adjust "
        "(input masks, max length, allowed characters)."
    ),
    Code.FOCUS_FAILED: (
        "Windows refused the focus change. Ask the user to click the window once, then retry."
    ),
    Code.TIMEOUT: (
        "Increase timeout_ms, or inspect the current state with uia_get_tree before retrying."
    ),
    Code.ACTION_RATE_LIMITED: (
        "Too many mutating actions. Wait a few seconds and retry; batch your work."
    ),
    Code.UIA_COM_ERROR: (
        "Unexpected UI Automation failure (see details.hresult). The app may be busy or hung. "
        "Retry once; if it persists, report it to the user."
    ),
}

RETRYABLE: frozenset[Code] = frozenset(
    {
        Code.SESSION_UNAVAILABLE,
        Code.ELEMENT_NOT_FOUND,
        Code.ELEMENT_DISABLED,
        Code.ELEMENT_OFFSCREEN,
        Code.VERIFY_FAILED,
        Code.FOCUS_FAILED,
        Code.TIMEOUT,
        Code.ACTION_RATE_LIMITED,
        Code.UIA_COM_ERROR,
    }
)

# Spec §9.2, mapeamento obrigatorio.
_HRESULT_MAP: dict[int, Code] = {
    0x80040201: Code.STALE_REF,              # UIA_E_ELEMENTNOTAVAILABLE
    0x80040200: Code.PATTERN_NOT_SUPPORTED,  # UIA_E_INVALIDOPERATION
    0x80131505: Code.TIMEOUT,                # UIA_E_TIMEOUT
    0x80070005: Code.ELEVATION_REQUIRED,     # E_ACCESSDENIED
    0x800706BA: Code.WINDOW_CLOSED,          # RPC_S_SERVER_UNAVAILABLE
}


def normalize_hresult(hresult: int) -> int:
    """comtypes entrega HRESULT com sinal. Normaliza para unsigned 32 bits."""
    return hresult & 0xFFFFFFFF


def hresult_to_code(hresult: int) -> Code:
    return _HRESULT_MAP.get(normalize_hresult(hresult), Code.UIA_COM_ERROR)


class ToolError(Exception):
    """Erro estruturado. Toda tool converte qualquer falha nisso."""

    def __init__(
        self,
        code: Code,
        message: str,
        *,
        hint: str | None = None,
        **details: Any,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint if hint is not None else DEFAULT_HINTS[code]
        self.details: dict[str, Any] = dict(details)
        self.details.setdefault("retryable", code in RETRYABLE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code.value,
                "message": self.message,
                "hint": self.hint,
                "details": self.details,
            },
        }

    def __repr__(self) -> str:
        return f"ToolError({self.code.value}: {self.message})"


def from_com_error(exc: Exception, **details: Any) -> ToolError:
    """Converte um comtypes.COMError (ou qualquer coisa com .hresult) em ToolError."""
    raw = getattr(exc, "hresult", None)
    if raw is None:
        return ToolError(Code.UIA_COM_ERROR, f"Unexpected COM failure: {exc}", **details)

    code = hresult_to_code(raw)
    hexed = f"0x{normalize_hresult(raw):08X}"
    text = getattr(exc, "text", None) or str(exc)
    return ToolError(
        code,
        f"UI Automation call failed with {hexed}: {text}",
        hresult=hexed,
        **details,
    )
```

- [ ] **Step 4: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_errors.py -q`
Expected: PASS — 13 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_windows_uia/errors.py tests/test_errors.py
git commit -m "feat(errors): codigos, hints padrao e mapeamento de HRESULT"
```

---

### Task 3: `config.py` — carregamento de `config.toml`

**Files:**
- Create: `src/mcp_windows_uia/config.py`
- Test: `tests/test_config.py`

Implementa a spec §10.1. Config é lida **só no startup** e nunca reescrita — por isso as dataclasses são `frozen`. Permitir recarga viva abriria um caminho de escalonamento: o agente escreveria no arquivo e ampliaria a própria allowlist.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_config.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from mcp_windows_uia.config import Config, load_config
from mcp_windows_uia.errors import Code, ToolError

MINIMO = """
[allowlist]
mode = "allow"
processes = ["Notepad.exe", "CalculatorApp.exe"]
"""


def _escrever(tmp_path: Path, texto: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(texto, encoding="utf-8")
    return p


def test_defaults_quando_secoes_ausentes(tmp_path: Path) -> None:
    cfg = load_config(_escrever(tmp_path, MINIMO))
    assert cfg.server.read_only is False
    assert cfg.server.max_actions_per_minute == 60
    assert cfg.server.settle_ms == 250
    assert cfg.server.ref_ttl_s == 300
    assert cfg.server.ref_cache_max == 5000
    assert cfg.audit.log_values == "redacted"
    assert cfg.audit.retain_days == 30


def test_nomes_de_processo_normalizados_para_minusculas(tmp_path: Path) -> None:
    cfg = load_config(_escrever(tmp_path, MINIMO))
    assert cfg.allowlist.processes == frozenset({"notepad.exe", "calculatorapp.exe"})


def test_config_ausente_gera_allowlist_vazia_e_nao_explode(tmp_path: Path) -> None:
    """Spec §10.1: falha fechada. Arquivo ausente = nada permitido, mas o servidor sobe."""
    cfg = load_config(tmp_path / "nao-existe.toml")
    assert cfg.allowlist.processes == frozenset()
    assert cfg.allowlist.mode == "allow"


def test_cli_read_only_vence_o_arquivo(tmp_path: Path) -> None:
    texto = MINIMO + "\n[server]\nread_only = false\n"
    cfg = load_config(_escrever(tmp_path, texto), force_read_only=True)
    assert cfg.server.read_only is True


def test_read_only_do_arquivo_e_respeitado(tmp_path: Path) -> None:
    texto = MINIMO + "\n[server]\nread_only = true\n"
    cfg = load_config(_escrever(tmp_path, texto), force_read_only=False)
    assert cfg.server.read_only is True


def test_variaveis_de_ambiente_expandidas_no_dir_de_auditoria(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    texto = MINIMO + '\n[audit]\ndir = "%LOCALAPPDATA%\\\\uia\\\\audit"\n'
    cfg = load_config(_escrever(tmp_path, texto))
    assert "%LOCALAPPDATA%" not in str(cfg.audit.dir)
    assert cfg.audit.dir.parts[-2:] == ("uia", "audit")


def test_title_patterns_da_denylist_sao_compilados(tmp_path: Path) -> None:
    texto = MINIMO + '\n[denylist]\ntitle_patterns = ["(?i)senha"]\n'
    cfg = load_config(_escrever(tmp_path, texto))
    assert cfg.denylist.title_patterns[0].search("Trocar SENHA agora") is not None


def test_regex_invalida_vira_invalid_argument(tmp_path: Path) -> None:
    texto = MINIMO + '\n[denylist]\ntitle_patterns = ["(nao fecha"]\n'
    with pytest.raises(ToolError) as exc:
        load_config(_escrever(tmp_path, texto))
    assert exc.value.code is Code.INVALID_ARGUMENT


def test_toml_malformado_vira_invalid_argument(tmp_path: Path) -> None:
    with pytest.raises(ToolError) as exc:
        load_config(_escrever(tmp_path, "isto ][ nao e toml"))
    assert exc.value.code is Code.INVALID_ARGUMENT


def test_log_values_invalido_e_rejeitado(tmp_path: Path) -> None:
    texto = MINIMO + '\n[audit]\nlog_values = "everything"\n'
    with pytest.raises(ToolError) as exc:
        load_config(_escrever(tmp_path, texto))
    assert exc.value.code is Code.INVALID_ARGUMENT


def test_modo_deny_all_e_aceito(tmp_path: Path) -> None:
    cfg = load_config(
        _escrever(tmp_path, '[allowlist]\nmode = "deny_all"\nprocesses = ["a.exe"]\n')
    )
    assert cfg.allowlist.mode == "deny_all"


def test_mode_invalido_e_rejeitado(tmp_path: Path) -> None:
    with pytest.raises(ToolError) as exc:
        load_config(_escrever(tmp_path, '[allowlist]\nmode = "talvez"\n'))
    assert exc.value.code is Code.INVALID_ARGUMENT


def test_config_e_congelada(tmp_path: Path) -> None:
    cfg: Config = load_config(_escrever(tmp_path, MINIMO))
    with pytest.raises(Exception):
        cfg.server.read_only = True  # type: ignore[misc]
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.config'`

- [ ] **Step 3: Implementar `config.py`**

```python
"""Carregamento e validacao de config.toml. Spec §10.1."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .errors import Code, ToolError

AllowMode = Literal["allow", "deny_all"]
LogValues = Literal["full", "redacted", "none"]

_DEFAULT_DENY_PROCESSES = ("consent.exe", "credentialuibroker.exe", "logonui.exe")
_DEFAULT_BLOCKED_KEYS = ("{ALT+F4}", "{WIN+L}", "{WIN+R}", "{CTRL+SHIFT+ESC}")


@dataclass(frozen=True, slots=True)
class ServerConfig:
    read_only: bool = False
    hide_denied: bool = False
    allow_coordinate_fallback: bool = True
    max_actions_per_minute: int = 60
    settle_ms: int = 250
    ref_ttl_s: int = 300
    ref_cache_max: int = 5000
    com_timeout_ms: int = 15000


@dataclass(frozen=True, slots=True)
class AllowlistConfig:
    mode: AllowMode = "allow"
    processes: frozenset[str] = frozenset()
    title_patterns: dict[str, re.Pattern[str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DenylistConfig:
    processes: frozenset[str] = frozenset(_DEFAULT_DENY_PROCESSES)
    title_patterns: tuple[re.Pattern[str], ...] = ()


@dataclass(frozen=True, slots=True)
class AuditConfig:
    dir: Path = field(default_factory=lambda: Path.cwd() / "audit")
    retain_days: int = 30
    log_values: LogValues = "redacted"


@dataclass(frozen=True, slots=True)
class KeysConfig:
    blocked: frozenset[str] = frozenset(_DEFAULT_BLOCKED_KEYS)


@dataclass(frozen=True, slots=True)
class Config:
    server: ServerConfig
    allowlist: AllowlistConfig
    denylist: DenylistConfig
    audit: AuditConfig
    keys: KeysConfig
    source_path: Path | None = None


def _bad(msg: str) -> ToolError:
    return ToolError(Code.INVALID_ARGUMENT, msg, hint="Fix config.toml and restart the server.")


def _compile(pattern: str, where: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise _bad(f"Invalid regex in {where}: {pattern!r} ({exc}).") from exc


def _norm_processes(values: Any, where: str) -> frozenset[str]:
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise _bad(f"{where} must be a list of strings.")
    return frozenset(v.strip().lower() for v in values if v.strip())


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise _bad(f"Section [{name}] must be a table.")
    return value


def _normalize_keys(values: Any) -> frozenset[str]:
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise _bad("[keys] blocked must be a list of strings.")
    return frozenset(v.strip().upper().replace(" ", "") for v in values if v.strip())


def load_config(path: str | os.PathLike[str], *, force_read_only: bool = False) -> Config:
    """Le config.toml. Arquivo ausente => defaults com allowlist vazia (falha fechada)."""
    p = Path(path)
    raw: dict[str, Any] = {}
    source: Path | None = None

    if p.is_file():
        try:
            raw = tomllib.loads(p.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise _bad(f"config.toml is not valid TOML: {exc}") from exc
        except OSError as exc:
            raise _bad(f"Could not read config file {p}: {exc}") from exc
        source = p.resolve()

    srv = _section(raw, "server")
    server = ServerConfig(
        read_only=bool(srv.get("read_only", False)) or force_read_only,
        hide_denied=bool(srv.get("hide_denied", False)),
        allow_coordinate_fallback=bool(srv.get("allow_coordinate_fallback", True)),
        max_actions_per_minute=int(srv.get("max_actions_per_minute", 60)),
        settle_ms=int(srv.get("settle_ms", 250)),
        ref_ttl_s=int(srv.get("ref_ttl_s", 300)),
        ref_cache_max=int(srv.get("ref_cache_max", 5000)),
        com_timeout_ms=int(srv.get("com_timeout_ms", 15000)),
    )

    allow = _section(raw, "allowlist")
    mode = allow.get("mode", "allow")
    if mode not in ("allow", "deny_all"):
        raise _bad("[allowlist] mode must be 'allow' or 'deny_all'.")
    allow_titles_raw = allow.get("title_patterns", {})
    if not isinstance(allow_titles_raw, dict):
        raise _bad("[allowlist] title_patterns must be a table of process -> regex.")
    allowlist = AllowlistConfig(
        mode=mode,
        processes=_norm_processes(allow.get("processes", []), "[allowlist] processes"),
        title_patterns={
            str(k).lower(): _compile(str(v), f"[allowlist] title_patterns.{k}")
            for k, v in allow_titles_raw.items()
        },
    )

    deny = _section(raw, "denylist")
    deny_titles_raw = deny.get("title_patterns", [])
    if not isinstance(deny_titles_raw, list):
        raise _bad("[denylist] title_patterns must be a list of regex strings.")
    denylist = DenylistConfig(
        processes=_norm_processes(
            deny.get("processes", list(_DEFAULT_DENY_PROCESSES)), "[denylist] processes"
        ),
        title_patterns=tuple(_compile(str(t), "[denylist] title_patterns") for t in deny_titles_raw),
    )

    aud = _section(raw, "audit")
    log_values = aud.get("log_values", "redacted")
    if log_values not in ("full", "redacted", "none"):
        raise _bad("[audit] log_values must be one of: 'full', 'redacted', 'none'.")
    audit_dir_raw = str(aud.get("dir", Path.cwd() / "audit"))
    audit = AuditConfig(
        dir=Path(os.path.expandvars(audit_dir_raw)).expanduser(),
        retain_days=int(aud.get("retain_days", 30)),
        log_values=log_values,
    )

    keys_section = _section(raw, "keys")
    keys = KeysConfig(
        blocked=_normalize_keys(keys_section.get("blocked", list(_DEFAULT_BLOCKED_KEYS)))
    )

    return Config(
        server=server,
        allowlist=allowlist,
        denylist=denylist,
        audit=audit,
        keys=keys,
        source_path=source,
    )
```

- [ ] **Step 4: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -q`
Expected: PASS — 13 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_windows_uia/config.py tests/test_config.py
git commit -m "feat(config): carregamento validado de config.toml com falha fechada"
```

---

### Task 4: `policy.py` — allowlist, read-only e rate limit

**Files:**
- Create: `src/mcp_windows_uia/policy.py`
- Test: `tests/test_policy.py`

Implementa a spec §10.1 e §10.2. Ordem de avaliação: **denylist → allowlist → decisão**. Match por nome de executável, nunca só por título (título é forjável pelo conteúdo do documento aberto).

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_policy.py`:

```python
from __future__ import annotations

import re

import pytest

from mcp_windows_uia.config import (
    AllowlistConfig,
    AuditConfig,
    Config,
    DenylistConfig,
    KeysConfig,
    ServerConfig,
)
from mcp_windows_uia.errors import Code, ToolError
from mcp_windows_uia.policy import Policy


def _config(
    *,
    allow: set[str] | None = None,
    deny: set[str] | None = None,
    deny_titles: list[str] | None = None,
    allow_titles: dict[str, str] | None = None,
    mode: str = "allow",
    read_only: bool = False,
    max_actions: int = 60,
) -> Config:
    return Config(
        server=ServerConfig(read_only=read_only, max_actions_per_minute=max_actions),
        allowlist=AllowlistConfig(
            mode=mode,  # type: ignore[arg-type]
            processes=frozenset(p.lower() for p in (allow or set())),
            title_patterns={k.lower(): re.compile(v) for k, v in (allow_titles or {}).items()},
        ),
        denylist=DenylistConfig(
            processes=frozenset(p.lower() for p in (deny or set())),
            title_patterns=tuple(re.compile(t) for t in (deny_titles or [])),
        ),
        audit=AuditConfig(),
        keys=KeysConfig(),
    )


def test_processo_na_allowlist_passa() -> None:
    pol = Policy(_config(allow={"notepad.exe"}))
    assert pol.window_allowed("Notepad.exe", "Sem titulo") is True
    pol.check_window("Notepad.exe", "Sem titulo")


def test_processo_fora_da_allowlist_e_negado() -> None:
    pol = Policy(_config(allow={"notepad.exe"}))
    with pytest.raises(ToolError) as exc:
        pol.check_window("CalculatorApp.exe", "Calculadora")
    assert exc.value.code is Code.APP_NOT_ALLOWED
    assert "CalculatorApp.exe" in exc.value.message
    assert "config.toml" in exc.value.hint


def test_allowlist_vazia_nega_tudo() -> None:
    """Spec §10.1: default de fabrica e falha fechada."""
    pol = Policy(_config(allow=set()))
    assert pol.window_allowed("Notepad.exe", "x") is False


def test_match_de_processo_e_case_insensitive() -> None:
    pol = Policy(_config(allow={"notepad.exe"}))
    assert pol.window_allowed("NOTEPAD.EXE", "x") is True


def test_denylist_vence_a_allowlist() -> None:
    pol = Policy(_config(allow={"keepass.exe"}, deny={"keepass.exe"}))
    with pytest.raises(ToolError) as exc:
        pol.check_window("KeePass.exe", "cofre")
    assert exc.value.code is Code.APP_NOT_ALLOWED
    assert "denylist" in exc.value.message.lower()


def test_denylist_por_titulo_bloqueia_mesmo_com_processo_permitido() -> None:
    pol = Policy(_config(allow={"chrome.exe"}, deny_titles=["(?i)banking"]))
    with pytest.raises(ToolError):
        pol.check_window("chrome.exe", "My BANKING portal")
    pol.check_window("chrome.exe", "Noticias")


def test_allowlist_title_pattern_restringe_dentro_do_processo() -> None:
    pol = Policy(_config(allow={"explorer.exe"}, allow_titles={"explorer.exe": r"^Documentos"}))
    assert pol.window_allowed("explorer.exe", "Documentos") is True
    assert pol.window_allowed("explorer.exe", "Painel de Controle") is False


def test_modo_deny_all_e_kill_switch() -> None:
    pol = Policy(_config(allow={"notepad.exe"}, mode="deny_all"))
    assert pol.window_allowed("Notepad.exe", "x") is False


def test_read_only_bloqueia_tools_mutantes() -> None:
    pol = Policy(_config(allow={"notepad.exe"}, read_only=True))
    with pytest.raises(ToolError) as exc:
        pol.check_mutating("uia_click")
    assert exc.value.code is Code.READ_ONLY_MODE
    assert "uia_click" in exc.value.message


def test_read_only_nao_afeta_leitura() -> None:
    pol = Policy(_config(allow={"notepad.exe"}, read_only=True))
    pol.check_window("Notepad.exe", "x")


def test_rate_limit_dispara_apos_o_teto() -> None:
    relogio = [1000.0]
    pol = Policy(_config(allow={"a.exe"}, max_actions=3), clock=lambda: relogio[0])
    for _ in range(3):
        pol.check_rate()
    with pytest.raises(ToolError) as exc:
        pol.check_rate()
    assert exc.value.code is Code.ACTION_RATE_LIMITED
    assert exc.value.details["retry_after_s"] > 0


def test_rate_limit_reabre_depois_da_janela_de_60s() -> None:
    relogio = [1000.0]
    pol = Policy(_config(allow={"a.exe"}, max_actions=2), clock=lambda: relogio[0])
    pol.check_rate()
    pol.check_rate()
    relogio[0] += 61.0
    pol.check_rate()


def test_rate_limit_zero_desativa_o_limite() -> None:
    pol = Policy(_config(allow={"a.exe"}, max_actions=0))
    for _ in range(500):
        pol.check_rate()
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.policy'`

- [ ] **Step 3: Implementar `policy.py`**

```python
"""Decisoes de politica: allowlist, read-only e rate limit. Spec §10.1 e §10.2."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from .config import Config
from .errors import Code, ToolError


class Policy:
    """Guarda de acesso. Ordem de avaliacao: denylist -> allowlist -> decisao."""

    def __init__(self, config: Config, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._cfg = config
        self._clock = clock
        self._actions: deque[float] = deque()

    @property
    def read_only(self) -> bool:
        return self._cfg.server.read_only

    # --------------------------------------------------------------- allowlist

    def denied_reason(self, process_name: str, title: str) -> str | None:
        """Motivo da negacao, ou None se a janela e permitida."""
        proc = (process_name or "").strip().lower()
        titulo = title or ""

        if proc in self._cfg.denylist.processes:
            return f"process '{process_name}' is in the denylist"
        for pattern in self._cfg.denylist.title_patterns:
            if pattern.search(titulo):
                return f"window title matches a denylist pattern ({pattern.pattern!r})"

        if self._cfg.allowlist.mode == "deny_all":
            return "server is in deny_all mode (kill switch)"
        if proc not in self._cfg.allowlist.processes:
            return f"process '{process_name}' is not in the allowlist"

        restricao = self._cfg.allowlist.title_patterns.get(proc)
        if restricao is not None and not restricao.search(titulo):
            return (
                f"window title does not match the allowlist restriction for "
                f"'{process_name}' ({restricao.pattern!r})"
            )
        return None

    def window_allowed(self, process_name: str, title: str) -> bool:
        return self.denied_reason(process_name, title) is None

    def check_window(self, process_name: str, title: str, **details: object) -> None:
        motivo = self.denied_reason(process_name, title)
        if motivo is None:
            return
        raise ToolError(
            Code.APP_NOT_ALLOWED,
            f"Access to this window was denied: {motivo}.",
            process=process_name,
            title=title,
            **details,
        )

    # --------------------------------------------------------------- read-only

    def check_mutating(self, tool_name: str) -> None:
        if self._cfg.server.read_only:
            raise ToolError(
                Code.READ_ONLY_MODE,
                f"{tool_name} mutates UI state and the server is running in read-only mode.",
                tool=tool_name,
            )

    # -------------------------------------------------------------- rate limit

    def check_rate(self) -> None:
        teto = self._cfg.server.max_actions_per_minute
        if teto <= 0:
            return
        agora = self._clock()
        while self._actions and agora - self._actions[0] >= 60.0:
            self._actions.popleft()
        if len(self._actions) >= teto:
            espera = 60.0 - (agora - self._actions[0])
            raise ToolError(
                Code.ACTION_RATE_LIMITED,
                f"Rate limit reached: {teto} mutating actions per minute.",
                hint=f"Too many mutating actions. Wait {espera:.0f} s and retry; batch your work.",
                retry_after_s=round(max(espera, 0.1), 1),
                limit=teto,
            )
        self._actions.append(agora)
```

- [ ] **Step 4: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_policy.py -q`
Expected: PASS — 13 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_windows_uia/policy.py tests/test_policy.py
git commit -m "feat(policy): allowlist com falha fechada, read-only e rate limit"
```

---

### Task 5: `budget.py` — clamps, truncamento e cursores

**Files:**
- Create: `src/mcp_windows_uia/budget.py`
- Test: `tests/test_budget.py`

Implementa a spec §6.1. O teto absoluto de 1500 nós é **regra dura**: vale mesmo se o chamador pedir mais (CA-09).

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_budget.py`:

```python
from __future__ import annotations

import pytest

from mcp_windows_uia.budget import (
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_budget.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.budget'`

- [ ] **Step 3: Implementar `budget.py`**

```python
"""Orcamento de resposta: limites, truncamento e cursores. Spec §6.1."""

from __future__ import annotations

import base64
import binascii
import json
import time
from dataclasses import dataclass

from .errors import Code, ToolError

HARD_MAX_NODES = 1500
HARD_MAX_DEPTH = 40
HARD_MAX_CHILDREN = 500
HARD_MAX_CHARS = 40000
NAME_LIMIT = 120
CURSOR_TTL_S = 120.0

DEFAULT_MAX_NODES = 200
DEFAULT_MAX_DEPTH = 12
DEFAULT_MAX_CHILDREN = 30
DEFAULT_MAX_CHARS = 6000

TRUNCATION_HINT = (
    "Response truncated. Narrow with filter='interactive', a smaller max_depth, root_ref, "
    "or call uia_find_elements instead of dumping the tree."
)


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


@dataclass(frozen=True, slots=True)
class Limits:
    max_nodes: int
    max_depth: int
    max_children_per_node: int

    @classmethod
    def build(
        cls,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_children_per_node: int = DEFAULT_MAX_CHILDREN,
    ) -> Limits:
        """Aplica os tetos duros da spec. Pedir mais que o teto nao aumenta o teto."""
        return cls(
            max_nodes=clamp(max_nodes, 1, HARD_MAX_NODES),
            max_depth=clamp(max_depth, 1, HARD_MAX_DEPTH),
            max_children_per_node=clamp(max_children_per_node, 1, HARD_MAX_CHILDREN),
        )


def truncate_name(name: str | None, limit: int = NAME_LIMIT) -> str:
    if not name:
        return ""
    if len(name) <= limit:
        return name
    return name[: limit - 1] + "…"


def truncate_text(text: str, *, max_chars: int, offset: int = 0) -> tuple[str, bool, int | None]:
    """Retorna (fatia, truncado, next_offset)."""
    limite = clamp(max_chars, 1, HARD_MAX_CHARS)
    fatia = text[offset : offset + limite]
    fim = offset + len(fatia)
    truncado = fim < len(text)
    return fatia, truncado, (fim if truncado else None)


def encode_cursor(
    window_ref: str, *, tree_version: int, position: int, now: float | None = None
) -> str:
    payload = {
        "w": window_ref,
        "v": tree_version,
        "p": position,
        "t": time.time() if now is None else now,
    }
    crua = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(crua).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, *, now: float | None = None) -> tuple[str, int, int]:
    """Retorna (window_ref, tree_version, position). INVALID_ARGUMENT se invalido."""
    agora = time.time() if now is None else now
    try:
        preenchido = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(preenchido.encode("ascii")))
        window_ref = str(payload["w"])
        tree_version = int(payload["v"])
        position = int(payload["p"])
        emitido = float(payload["t"])
    except (KeyError, ValueError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise ToolError(
            Code.INVALID_ARGUMENT,
            "The cursor is malformed and cannot be decoded.",
            hint="Drop the cursor and call uia_get_tree again from the start.",
        ) from exc

    if agora - emitido > CURSOR_TTL_S:
        raise ToolError(
            Code.INVALID_ARGUMENT,
            f"The cursor has expired (valid for {int(CURSOR_TTL_S)} s).",
            hint="Call uia_get_tree again without a cursor to restart the capture.",
            age_s=round(agora - emitido, 1),
        )
    return window_ref, tree_version, position
```

- [ ] **Step 4: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_budget.py -q`
Expected: PASS — 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_windows_uia/budget.py tests/test_budget.py
git commit -m "feat(budget): limites duros, truncamento e cursores opacos"
```

---

### Task 6: `refs.py` — RefStore com TTL, LRU e dedup

**Files:**
- Create: `src/mcp_windows_uia/refs.py`
- Test: `tests/test_refs.py`

Implementa a spec §7.1 e a parte de armazenamento da §7.2. O **rebind** (busca por `AutomationId`/`Name`/`index_path`) fica no Plano 2 — depende de busca na árvore.

Decisão de projeto não explícita na spec, mas obrigatória: o store **deduplica por `(hwnd, runtime_id)`**. Sem isso, cada `uia_get_tree` criaria 200 refs novas para os mesmos elementos e o LRU giraria à toa, invalidando refs que o agente acabou de receber.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_refs.py`:

```python
from __future__ import annotations

import pytest

from mcp_windows_uia.errors import Code, ToolError
from mcp_windows_uia.refs import ElementIdentity, RefStore


class FakeElement:
    """Stand-in para IUIAutomationElement. O RefStore so o guarda, nunca desreferencia."""

    def __init__(self, tag: str) -> None:
        self.tag = tag


def _ident(aid: str = "SaveButton", nome: str = "Salvar") -> ElementIdentity:
    return ElementIdentity(
        automation_id=aid,
        control_type="Button",
        name=nome,
        class_name="Button",
        index_path=(0, 3, 1),
    )


def _store(**kw: object) -> RefStore:
    kw.setdefault("clock", lambda: 1000.0)
    return RefStore(**kw)  # type: ignore[arg-type]


def test_window_ref_tem_o_formato_da_spec() -> None:
    store = _store()
    assert store.window_ref(hwnd=723918) == "w1"
    assert store.window_ref(hwnd=198442) == "w2"


def test_window_ref_e_estavel_para_o_mesmo_hwnd() -> None:
    store = _store()
    assert store.window_ref(hwnd=723918) == store.window_ref(hwnd=723918)


def test_element_ref_tem_o_formato_da_spec() -> None:
    store = _store()
    wref = store.window_ref(hwnd=1)
    ref = store.put(
        FakeElement("a"), runtime_id=(42, 7), hwnd=1, window_ref=wref,
        identity=_ident(), tree_version=1,
    )
    assert ref.startswith("w1-e")


def test_put_deduplica_por_runtime_id() -> None:
    """Recapturar a arvore nao pode inflar o store nem trocar as refs ja entregues."""
    store = _store()
    wref = store.window_ref(hwnd=1)
    a = store.put(FakeElement("a"), runtime_id=(42, 7), hwnd=1, window_ref=wref,
                  identity=_ident(), tree_version=1)
    b = store.put(FakeElement("b"), runtime_id=(42, 7), hwnd=1, window_ref=wref,
                  identity=_ident(), tree_version=2)
    assert a == b
    assert len(store) == 1
    assert store.get(a).element.tag == "b"
    assert store.get(a).tree_version == 2


def test_runtime_id_igual_em_janelas_diferentes_nao_colide() -> None:
    store = _store()
    w1, w2 = store.window_ref(hwnd=1), store.window_ref(hwnd=2)
    a = store.put(FakeElement("a"), runtime_id=(42, 7), hwnd=1, window_ref=w1,
                  identity=_ident(), tree_version=1)
    b = store.put(FakeElement("b"), runtime_id=(42, 7), hwnd=2, window_ref=w2,
                  identity=_ident(), tree_version=1)
    assert a != b


def test_get_de_ref_desconhecida_e_ref_not_found() -> None:
    store = _store()
    with pytest.raises(ToolError) as exc:
        store.get("w9-e99")
    assert exc.value.code is Code.REF_NOT_FOUND


def test_ref_expirada_vira_stale_ref_com_motivo_expired() -> None:
    agora = [1000.0]
    store = RefStore(ttl_s=300, clock=lambda: agora[0])
    wref = store.window_ref(hwnd=1)
    ref = store.put(FakeElement("a"), runtime_id=(1,), hwnd=1, window_ref=wref,
                    identity=_ident(), tree_version=1)
    agora[0] += 301.0
    with pytest.raises(ToolError) as exc:
        store.get(ref)
    assert exc.value.code is Code.STALE_REF
    assert exc.value.details["reason"] == "expired"


def test_get_bem_sucedido_renova_o_ttl() -> None:
    agora = [1000.0]
    store = RefStore(ttl_s=300, clock=lambda: agora[0])
    wref = store.window_ref(hwnd=1)
    ref = store.put(FakeElement("a"), runtime_id=(1,), hwnd=1, window_ref=wref,
                    identity=_ident(), tree_version=1)
    agora[0] += 200.0
    store.get(ref)
    agora[0] += 200.0
    assert store.get(ref).ref == ref


def test_invalidate_window_derruba_todas_as_refs_da_janela() -> None:
    store = _store()
    w1, w2 = store.window_ref(hwnd=1), store.window_ref(hwnd=2)
    a = store.put(FakeElement("a"), runtime_id=(1,), hwnd=1, window_ref=w1,
                  identity=_ident(), tree_version=1)
    b = store.put(FakeElement("b"), runtime_id=(2,), hwnd=2, window_ref=w2,
                  identity=_ident(), tree_version=1)
    assert store.invalidate_window(w1) == 1
    with pytest.raises(ToolError):
        store.get(a)
    assert store.get(b).ref == b


def test_evicao_lru_respeita_a_capacidade() -> None:
    store = RefStore(max_entries=3, clock=lambda: 1000.0)
    wref = store.window_ref(hwnd=1)
    refs = [
        store.put(FakeElement(str(i)), runtime_id=(i,), hwnd=1, window_ref=wref,
                  identity=_ident(), tree_version=1)
        for i in range(4)
    ]
    assert len(store) == 3
    with pytest.raises(ToolError) as exc:
        store.get(refs[0])
    assert exc.value.code is Code.REF_NOT_FOUND
    assert store.get(refs[3]).ref == refs[3]


def test_get_promove_a_entrada_no_lru() -> None:
    store = RefStore(max_entries=2, clock=lambda: 1000.0)
    wref = store.window_ref(hwnd=1)
    a = store.put(FakeElement("a"), runtime_id=(1,), hwnd=1, window_ref=wref,
                  identity=_ident(), tree_version=1)
    b = store.put(FakeElement("b"), runtime_id=(2,), hwnd=1, window_ref=wref,
                  identity=_ident(), tree_version=1)
    store.get(a)
    c = store.put(FakeElement("c"), runtime_id=(3,), hwnd=1, window_ref=wref,
                  identity=_ident(), tree_version=1)
    assert store.get(a).ref == a
    assert store.get(c).ref == c
    with pytest.raises(ToolError):
        store.get(b)


def test_bump_tree_version_incrementa_por_janela() -> None:
    store = _store()
    w1 = store.window_ref(hwnd=1)
    assert store.bump_tree_version(w1) == 1
    assert store.bump_tree_version(w1) == 2
    assert store.tree_version(w1) == 2
    assert store.tree_version(store.window_ref(hwnd=2)) == 0


def test_hwnd_de_window_ref() -> None:
    store = _store()
    w1 = store.window_ref(hwnd=723918)
    assert store.hwnd_for(w1) == 723918
    with pytest.raises(ToolError) as exc:
        store.hwnd_for("w404")
    assert exc.value.code is Code.WINDOW_NOT_FOUND
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_refs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.refs'`

- [ ] **Step 3: Implementar `refs.py`**

```python
"""RefStore: refs opacas e estaveis para janelas e elementos. Spec §7.1 e §7.2.

O rebind (re-resolucao por AutomationId / Name / index_path quando o RuntimeId morre)
NAO vive aqui: precisa de busca na arvore UIA e chega no Plano 2. Este modulo e puro —
guarda, expira, invalida, e nunca desreferencia o ponteiro COM que carrega.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .errors import Code, ToolError

DEFAULT_TTL_S = 300
DEFAULT_MAX_ENTRIES = 5000


@dataclass(frozen=True, slots=True)
class ElementIdentity:
    """Material de re-resolucao quando o RuntimeId morre (spec §7.2, passo 5)."""

    automation_id: str = ""
    control_type: str = ""
    name: str = ""
    class_name: str = ""
    index_path: tuple[int, ...] = ()


@dataclass(slots=True)
class RefEntry:
    ref: str
    element: Any                      # IUIAutomationElement: so a UiaWorker toca
    runtime_id: tuple[int, ...]
    hwnd: int
    window_ref: str
    identity: ElementIdentity
    tree_version: int
    created_at: float
    last_ok_at: float


@dataclass(slots=True)
class _WindowEntry:
    window_ref: str
    hwnd: int
    tree_version: int = 0
    element_refs: set[str] = field(default_factory=set)


class RefStore:
    """Cache de refs por sessao do servidor. Contadores monotonicos, evicao LRU."""

    def __init__(
        self,
        *,
        ttl_s: int = DEFAULT_TTL_S,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_s
        self._max = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, RefEntry] = OrderedDict()
        self._windows: dict[str, _WindowEntry] = {}
        self._by_hwnd: dict[int, str] = {}
        self._by_runtime: dict[tuple[int, tuple[int, ...]], str] = {}
        self._window_seq = 0
        self._element_seq = 0

    def __len__(self) -> int:
        return len(self._entries)

    # ----------------------------------------------------------------- janelas

    def window_ref(self, *, hwnd: int) -> str:
        """Ref estavel para um hwnd, criada na primeira vez que a janela e vista."""
        existente = self._by_hwnd.get(hwnd)
        if existente is not None:
            return existente
        self._window_seq += 1
        ref = f"w{self._window_seq}"
        self._by_hwnd[hwnd] = ref
        self._windows[ref] = _WindowEntry(window_ref=ref, hwnd=hwnd)
        return ref

    def known_window(self, window_ref: str) -> bool:
        return window_ref in self._windows

    def hwnd_for(self, window_ref: str) -> int:
        entrada = self._windows.get(window_ref)
        if entrada is None:
            raise ToolError(
                Code.WINDOW_NOT_FOUND,
                f"Unknown window ref {window_ref!r}.",
                window_ref=window_ref,
            )
        return entrada.hwnd

    def tree_version(self, window_ref: str) -> int:
        entrada = self._windows.get(window_ref)
        return 0 if entrada is None else entrada.tree_version

    def bump_tree_version(self, window_ref: str) -> int:
        entrada = self._windows.get(window_ref)
        if entrada is None:
            raise ToolError(
                Code.WINDOW_NOT_FOUND,
                f"Unknown window ref {window_ref!r}.",
                window_ref=window_ref,
            )
        entrada.tree_version += 1
        return entrada.tree_version

    def invalidate_window(self, window_ref: str) -> int:
        """Invalida em lote todas as refs da janela. Retorna quantas cairam."""
        entrada = self._windows.get(window_ref)
        if entrada is None:
            return 0
        refs = list(entrada.element_refs)
        for ref in refs:
            self._drop(ref)
        entrada.element_refs.clear()
        return len(refs)

    # --------------------------------------------------------------- elementos

    def put(
        self,
        element: Any,
        *,
        runtime_id: tuple[int, ...],
        hwnd: int,
        window_ref: str,
        identity: ElementIdentity,
        tree_version: int,
    ) -> str:
        """Registra (ou atualiza) um elemento e devolve sua ref.

        Deduplica por (hwnd, runtime_id): recapturar a arvore reaproveita as refs ja
        entregues ao agente em vez de criar novas.
        """
        agora = self._clock()
        chave = (hwnd, runtime_id)

        existente = self._by_runtime.get(chave)
        if existente is not None and existente in self._entries:
            entrada = self._entries[existente]
            entrada.element = element
            entrada.identity = identity
            entrada.tree_version = tree_version
            entrada.last_ok_at = agora
            self._entries.move_to_end(existente)
            return existente

        self._element_seq += 1
        ref = f"{window_ref}-e{self._element_seq}"
        self._entries[ref] = RefEntry(
            ref=ref,
            element=element,
            runtime_id=runtime_id,
            hwnd=hwnd,
            window_ref=window_ref,
            identity=identity,
            tree_version=tree_version,
            created_at=agora,
            last_ok_at=agora,
        )
        self._by_runtime[chave] = ref
        janela = self._windows.get(window_ref)
        if janela is not None:
            janela.element_refs.add(ref)
        self._evict()
        return ref

    def get(self, ref: str) -> RefEntry:
        """Recupera uma entrada viva, renovando seu TTL. Nao valida o elemento COM."""
        entrada = self._entries.get(ref)
        if entrada is None:
            raise ToolError(
                Code.REF_NOT_FOUND,
                f"Ref {ref!r} is not known to this server (never issued, or evicted).",
                ref=ref,
            )
        agora = self._clock()
        if agora - entrada.last_ok_at > self._ttl:
            self._drop(ref)
            raise ToolError(
                Code.STALE_REF,
                f"Ref {ref} expired after {self._ttl} s without use.",
                ref=ref,
                window_ref=entrada.window_ref,
                reason="expired",
            )
        entrada.last_ok_at = agora
        self._entries.move_to_end(ref)
        return entrada

    def touch(self, entry: RefEntry) -> None:
        entry.last_ok_at = self._clock()
        self._entries.move_to_end(entry.ref)

    # ------------------------------------------------------------------ interno

    def _drop(self, ref: str) -> None:
        entrada = self._entries.pop(ref, None)
        if entrada is None:
            return
        self._by_runtime.pop((entrada.hwnd, entrada.runtime_id), None)
        janela = self._windows.get(entrada.window_ref)
        if janela is not None:
            janela.element_refs.discard(ref)

    def _evict(self) -> None:
        while len(self._entries) > self._max:
            ref = next(iter(self._entries))
            self._drop(ref)
```

- [ ] **Step 4: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_refs.py -q`
Expected: PASS — 13 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_windows_uia/refs.py tests/test_refs.py
git commit -m "feat(refs): RefStore com TTL, LRU e dedup por RuntimeId"
```

---

### Task 7: `audit.py` — log JSONL com redação

**Files:**
- Create: `src/mcp_windows_uia/audit.py`
- Test: `tests/test_audit.py`

Implementa a spec §10.3. Requisito absoluto (CA-15): valor de campo `password` **nunca** vai para o disco, em nenhum modo.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_audit.py`:

```python
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from mcp_windows_uia.audit import AuditLog
from mcp_windows_uia.config import AuditConfig


def _ler(dir_: Path) -> list[dict]:
    linhas: list[dict] = []
    for arquivo in sorted(dir_.glob("*.jsonl")):
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            linhas.append(json.loads(linha))
    return linhas


def test_escreve_uma_linha_json_valida_por_chamada(tmp_path: Path) -> None:
    log = AuditLog(AuditConfig(dir=tmp_path, log_values="redacted"))
    log.log_call(tool="uia_list_windows", result="ok", duration_ms=42)
    log.log_call(tool="uia_get_tree", result="ok", duration_ms=118)
    registros = _ler(tmp_path)
    assert [r["tool"] for r in registros] == ["uia_list_windows", "uia_get_tree"]
    assert [r["seq"] for r in registros] == [1, 2]


def test_nome_do_arquivo_e_diario(tmp_path: Path) -> None:
    log = AuditLog(AuditConfig(dir=tmp_path))
    log.log_call(tool="uia_get_tree", result="ok")
    (arquivo,) = list(tmp_path.glob("*.jsonl"))
    assert arquivo.name.startswith("uia-")
    assert len(arquivo.stem) == len("uia-2026-08-19")


def test_modo_redacted_guarda_hash_e_tamanho_mas_nao_o_valor(tmp_path: Path) -> None:
    log = AuditLog(AuditConfig(dir=tmp_path, log_values="redacted"))
    log.log_call(tool="uia_set_value", result="ok", value="Relatorio trimestral")
    (registro,) = _ler(tmp_path)
    assert registro["value"] == "«redacted»"
    assert registro["value_len"] == len("Relatorio trimestral")
    assert len(registro["value_sha256"]) == 8
    assert "Relatorio" not in json.dumps(registro, ensure_ascii=False)


def test_modo_full_guarda_o_valor(tmp_path: Path) -> None:
    log = AuditLog(AuditConfig(dir=tmp_path, log_values="full"))
    log.log_call(tool="uia_set_value", result="ok", value="texto claro")
    (registro,) = _ler(tmp_path)
    assert registro["value"] == "texto claro"


def test_modo_none_omite_o_campo_valor(tmp_path: Path) -> None:
    log = AuditLog(AuditConfig(dir=tmp_path, log_values="none"))
    log.log_call(tool="uia_set_value", result="ok", value="texto claro")
    (registro,) = _ler(tmp_path)
    assert "value" not in registro


def test_senha_nunca_vai_para_o_disco_nem_em_modo_full(tmp_path: Path) -> None:
    """CA-15: em nenhum modo o valor de um campo password e gravado."""
    log = AuditLog(AuditConfig(dir=tmp_path, log_values="full"))
    log.log_call(tool="uia_set_value", result="ok", value="s3nh4-secreta", is_password=True)
    bruto = next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8")
    assert "s3nh4-secreta" not in bruto
    registro = json.loads(bruto)
    assert registro["value"] == "«redacted:password»"
    assert "value_sha256" not in registro


def test_negacao_de_policy_e_registrada_com_codigo(tmp_path: Path) -> None:
    log = AuditLog(AuditConfig(dir=tmp_path))
    log.log_call(
        tool="uia_get_tree", result="denied", code="APP_NOT_ALLOWED",
        target={"process": "Taskmgr.exe", "pid": 4412},
    )
    (registro,) = _ler(tmp_path)
    assert registro["result"] == "denied"
    assert registro["code"] == "APP_NOT_ALLOWED"
    assert registro["target"]["process"] == "Taskmgr.exe"


def test_timestamp_e_iso8601_com_offset(tmp_path: Path) -> None:
    log = AuditLog(AuditConfig(dir=tmp_path))
    log.log_call(tool="uia_get_tree", result="ok")
    (registro,) = _ler(tmp_path)
    assert datetime.fromisoformat(registro["ts"]).tzinfo is not None


def test_diretorio_e_criado_se_ausente(tmp_path: Path) -> None:
    destino = tmp_path / "fundo" / "do" / "poco"
    AuditLog(AuditConfig(dir=destino)).log_call(tool="uia_get_tree", result="ok")
    assert destino.is_dir()


def test_falha_de_escrita_nao_derruba_o_servidor(tmp_path: Path) -> None:
    """Auditoria e importante, mas nao pode matar a sessao do agente."""
    obstaculo = tmp_path / "sou-um-arquivo"
    obstaculo.write_text("x", encoding="utf-8")
    log = AuditLog(AuditConfig(dir=obstaculo / "audit"))  # pai e arquivo -> mkdir falha
    log.log_call(tool="uia_get_tree", result="ok")  # nao levanta


def test_purge_remove_arquivos_alem_da_retencao(tmp_path: Path) -> None:
    antigo = tmp_path / "uia-2020-01-01.jsonl"
    recente = tmp_path / "uia-2026-08-19.jsonl"
    antigo.write_text("{}\n", encoding="utf-8")
    recente.write_text("{}\n", encoding="utf-8")
    log = AuditLog(AuditConfig(dir=tmp_path, retain_days=30))
    assert log.purge_old(today="2026-08-19") == 1
    assert not antigo.exists()
    assert recente.exists()
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_audit.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.audit'`

- [ ] **Step 3: Implementar `audit.py`**

```python
"""Auditoria append-only em JSONL. Spec §10.3.

stdout e o canal JSON-RPC, entao nada daqui vai para stdout: auditoria vai para
arquivo, e falha de escrita vira aviso em stderr — nunca uma excecao que derrube
a chamada do agente.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import AuditConfig

_log = logging.getLogger(__name__)

REDACTED = "«redacted»"
REDACTED_PASSWORD = "«redacted:password»"


class AuditLog:
    def __init__(self, config: AuditConfig) -> None:
        self._cfg = config
        self._lock = threading.Lock()
        self._seq = 0
        self._path = config.dir

    # ----------------------------------------------------------------- escrita

    def _arquivo_de_hoje(self) -> Path:
        return self._path / f"uia-{date.today().isoformat()}.jsonl"

    def write(self, record: dict[str, Any]) -> None:
        try:
            self._path.mkdir(parents=True, exist_ok=True)
            linha = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            with self._arquivo_de_hoje().open("a", encoding="utf-8") as fh:
                fh.write(linha + "\n")
        except OSError as exc:
            _log.warning("audit write failed: %s", exc)

    def log_call(
        self,
        *,
        tool: str,
        result: str,
        code: str | None = None,
        target: dict[str, Any] | None = None,
        element: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        value: str | None = None,
        method_used: str | None = None,
        fallback_used: bool | None = None,
        duration_ms: int | float | None = None,
        read_only: bool = False,
        is_password: bool = False,
    ) -> None:
        with self._lock:
            self._seq += 1
            seq = self._seq

        registro: dict[str, Any] = {
            "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "seq": seq,
            "tool": tool,
            "result": result,
        }
        if code is not None:
            registro["code"] = code
        if target:
            registro["target"] = target
        if element:
            registro["element"] = element
        if params:
            registro["params"] = params
        if method_used is not None:
            registro["method_used"] = method_used
        if fallback_used is not None:
            registro["fallback_used"] = fallback_used
        if duration_ms is not None:
            registro["duration_ms"] = round(float(duration_ms))
        registro["read_only"] = read_only

        registro.update(self._campos_de_valor(value, is_password=is_password))
        self.write(registro)

    def _campos_de_valor(self, value: str | None, *, is_password: bool) -> dict[str, Any]:
        if value is None:
            return {}
        # Regra absoluta: senha nunca e gravada, nem com log_values='full'.
        if is_password:
            return {"value": REDACTED_PASSWORD}
        modo = self._cfg.log_values
        if modo == "none":
            return {}
        if modo == "full":
            return {"value": value}
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        return {"value": REDACTED, "value_len": len(value), "value_sha256": digest}

    # ---------------------------------------------------------------- retencao

    def purge_old(self, *, today: str | None = None) -> int:
        """Remove arquivos alem de retain_days. Chamado uma vez no startup."""
        if self._cfg.retain_days <= 0 or not self._path.is_dir():
            return 0
        hoje = date.fromisoformat(today) if today else date.today()
        corte = hoje - timedelta(days=self._cfg.retain_days)
        removidos = 0
        for arquivo in self._path.glob("uia-*.jsonl"):
            try:
                quando = date.fromisoformat(arquivo.stem.removeprefix("uia-"))
            except ValueError:
                continue
            if quando < corte:
                try:
                    arquivo.unlink()
                    removidos += 1
                except OSError as exc:
                    _log.warning("could not purge %s: %s", arquivo, exc)
        return removidos
```

- [ ] **Step 4: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_audit.py -q`
Expected: PASS — 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_windows_uia/audit.py tests/test_audit.py
git commit -m "feat(audit): JSONL diario com redacao e retencao"
```

---

### Task 8: `worker.py` — thread STA única

**Files:**
- Create: `src/mcp_windows_uia/worker.py`
- Test: `tests/test_worker.py`

Implementa a spec §2.2. Este módulo é a razão de o servidor não corromper memória: **todo** ponteiro COM nasce, vive e morre na mesma thread.

Acoplamento a documentar no código: o timeout daqui (15 s) só funciona porque o `ConnectionTimeout`/`TransactionTimeout` do cliente UIA é 10 s (Task 10). Sem eles, a chamada COM travada nunca retorna, a thread única fica ocupada, e a segunda metade do CA-23 ("a chamada seguinte funciona normalmente") falha.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_worker.py`:

```python
from __future__ import annotations

import threading
import time

import pytest

from mcp_windows_uia.errors import Code, ToolError
from mcp_windows_uia.worker import UiaWorker


@pytest.fixture()
def worker():
    w = UiaWorker(com_timeout_ms=1000)
    w.start()
    yield w
    w.shutdown()


async def test_executa_a_funcao_e_devolve_o_resultado(worker: UiaWorker) -> None:
    assert await worker.run(lambda a, b: a + b, 2, 3) == 5


async def test_todas_as_chamadas_correm_na_mesma_thread(worker: UiaWorker) -> None:
    ids = {await worker.run(threading.get_ident) for _ in range(10)}
    assert len(ids) == 1


async def test_a_thread_do_worker_nao_e_a_do_chamador(worker: UiaWorker) -> None:
    assert await worker.run(threading.get_ident) != threading.get_ident()


async def test_tool_error_atravessa_intacto(worker: UiaWorker) -> None:
    def explode() -> None:
        raise ToolError(Code.WINDOW_CLOSED, "sumiu")

    with pytest.raises(ToolError) as exc:
        await worker.run(explode)
    assert exc.value.code is Code.WINDOW_CLOSED


async def test_excecao_inesperada_vira_uia_com_error(worker: UiaWorker) -> None:
    def explode() -> None:
        raise ValueError("boom")

    with pytest.raises(ToolError) as exc:
        await worker.run(explode)
    assert exc.value.code is Code.UIA_COM_ERROR
    assert "boom" in exc.value.message


async def test_estouro_de_timeout_vira_timeout(worker: UiaWorker) -> None:
    with pytest.raises(ToolError) as exc:
        await worker.run(lambda: time.sleep(3.0))
    assert exc.value.code is Code.TIMEOUT
    assert exc.value.details["retryable"] is True


async def test_worker_nao_iniciado_e_erro_claro() -> None:
    w = UiaWorker()
    with pytest.raises(ToolError) as exc:
        await w.run(lambda: 1)
    assert exc.value.code is Code.SESSION_UNAVAILABLE


async def test_sta_foi_inicializada_na_thread(worker: UiaWorker) -> None:
    """CoInitializeEx com COINIT_APARTMENTTHREADED tem de ter rodado no initializer."""
    assert await worker.run(lambda: worker.sta_ready) is True
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_worker.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.worker'`

- [ ] **Step 3: Implementar `worker.py`**

```python
"""UiaWorker: thread STA unica onde toda chamada COM acontece. Spec §2.2.

Por que uma thread so: IUIAutomationElement tem afinidade de apartamento. Usar um
ponteiro obtido na thread A a partir da thread B produz RPC_E_WRONG_THREAD ou, pior,
corrupcao silenciosa. O RefStore guarda os ponteiros, mas so esta thread os toca.

Por que o timeout funciona: o cliente UIA e criado com ConnectionTimeout e
TransactionTimeout de 10 s (ver uia/core.py). Uma chamada a um app travado retorna
com erro em ~10 s, antes do nosso teto de 15 s — entao a thread se libera sozinha e
a chamada seguinte funciona (CA-23). Sem aqueles timeouts, este asyncio.wait_for
apenas devolveria o controle ao agente enquanto a thread seguiria presa para sempre.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from .errors import Code, ToolError, from_com_error

_log = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_COM_TIMEOUT_MS = 15000


class UiaWorker:
    def __init__(self, *, com_timeout_ms: int = DEFAULT_COM_TIMEOUT_MS) -> None:
        self._timeout_s = com_timeout_ms / 1000.0
        self._executor: ThreadPoolExecutor | None = None
        self._thread_id: int | None = None
        self.sta_ready = False

    # ------------------------------------------------------------------- ciclo

    def _initializer(self) -> None:
        self._thread_id = threading.get_ident()
        try:
            import comtypes

            comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
            self.sta_ready = True
        except Exception as exc:  # pragma: no cover - so falha em ambiente quebrado
            _log.error("CoInitializeEx failed on the UIA thread: %s", exc)
            self.sta_ready = False

    def start(self) -> None:
        if self._executor is not None:
            return
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="uia", initializer=self._initializer
        )

    def shutdown(self) -> None:
        if self._executor is None:
            return
        executor, self._executor = self._executor, None
        executor.shutdown(wait=False, cancel_futures=True)

    # ---------------------------------------------------------------- execucao

    async def run(self, fn: Callable[..., T], *args: Any) -> T:
        executor = self._executor
        if executor is None:
            raise ToolError(
                Code.SESSION_UNAVAILABLE,
                "The UI Automation worker thread is not running.",
                hint="This is a server bug — ask the user to restart the MCP server.",
            )

        loop = asyncio.get_running_loop()
        futuro = loop.run_in_executor(executor, lambda: self._call(fn, *args))
        try:
            return await asyncio.wait_for(futuro, timeout=self._timeout_s)
        except asyncio.TimeoutError as exc:
            raise ToolError(
                Code.TIMEOUT,
                f"The UI Automation call did not return within {self._timeout_s:.0f} s.",
                hint=(
                    "The target application is likely hung or not pumping messages. "
                    "Ask the user to check it, then retry."
                ),
                timeout_s=self._timeout_s,
            ) from exc

    @staticmethod
    def _call(fn: Callable[..., T], *args: Any) -> T:
        try:
            return fn(*args)
        except ToolError:
            raise
        except Exception as exc:
            if hasattr(exc, "hresult"):
                raise from_com_error(exc) from exc
            raise ToolError(
                Code.UIA_COM_ERROR,
                f"Unexpected failure inside the UI Automation worker: {exc!r}",
            ) from exc
```

- [ ] **Step 4: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_worker.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_windows_uia/worker.py tests/test_worker.py
git commit -m "feat(worker): thread STA unica com timeout de chamada COM"
```

---

### Task 9: `uia/dpi.py` — DPI awareness e desktop virtual

**Files:**
- Create: `src/mcp_windows_uia/uia/__init__.py`
- Create: `src/mcp_windows_uia/uia/dpi.py`
- Test: `tests/test_dpi.py`

Implementa a spec §4.1. A normalização para `SendInput` só é usada no Plano 3, mas a matemática é pura, barata de testar agora, e errá-la é a causa clássica de clique no lugar errado (CA-17).

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_dpi.py`:

```python
from __future__ import annotations

import pytest

from mcp_windows_uia.uia.dpi import (
    VirtualDesktop,
    center_of,
    init_dpi_awareness,
    to_absolute_normalized,
)


def test_center_of_um_retangulo() -> None:
    assert center_of((100, 200, 300, 400)) == (200, 300)


def test_center_of_com_coordenadas_negativas() -> None:
    """Monitor a esquerda do primario produz X negativo. Spec §4.1."""
    assert center_of((-1920, 0, -920, 500)) == (-1420, 250)


def test_normalizacao_mapeia_canto_superior_esquerdo_para_zero() -> None:
    vd = VirtualDesktop(left=0, top=0, width=1920, height=1080)
    assert to_absolute_normalized(0, 0, vd) == (0, 0)


def test_normalizacao_mapeia_canto_inferior_direito_para_65535() -> None:
    vd = VirtualDesktop(left=0, top=0, width=1920, height=1080)
    assert to_absolute_normalized(1919, 1079, vd) == (65535, 65535)


def test_normalizacao_respeita_origem_negativa() -> None:
    """Desktop virtual comecando em -1920: esse ponto e o zero normalizado."""
    vd = VirtualDesktop(left=-1920, top=0, width=3840, height=1080)
    assert to_absolute_normalized(-1920, 0, vd) == (0, 0)
    nx, _ = to_absolute_normalized(1919, 0, vd)
    assert nx == 65535


def test_normalizacao_do_ponto_medio() -> None:
    vd = VirtualDesktop(left=0, top=0, width=1921, height=1081)
    assert to_absolute_normalized(960, 540, vd) == (32767, 32767)


def test_desktop_de_largura_um_nao_divide_por_zero() -> None:
    vd = VirtualDesktop(left=0, top=0, width=1, height=1)
    assert to_absolute_normalized(0, 0, vd) == (0, 0)


def test_normalizacao_prende_fora_dos_limites() -> None:
    vd = VirtualDesktop(left=0, top=0, width=1920, height=1080)
    assert to_absolute_normalized(-500, -500, vd) == (0, 0)
    assert to_absolute_normalized(99999, 99999, vd) == (65535, 65535)


@pytest.mark.e2e
def test_init_dpi_awareness_reporta_o_metodo_usado() -> None:
    assert init_dpi_awareness() in {"PerMonitorV2", "PerMonitor", "already-set", "unavailable"}


@pytest.mark.e2e
def test_virtual_desktop_real_tem_area_positiva() -> None:
    vd = VirtualDesktop.current()
    assert vd.width > 0 and vd.height > 0
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dpi.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.uia'`

- [ ] **Step 3: Implementar**

`src/mcp_windows_uia/uia/__init__.py`:

```python
"""Camada COM/Win32. Nada aqui pode ser chamado fora da thread do UiaWorker."""
```

`src/mcp_windows_uia/uia/dpi.py`:

```python
"""DPI awareness, desktop virtual e conversao de coordenadas. Spec §4.1.

init_dpi_awareness() DEVE rodar antes de qualquer chamada Win32/UIA que envolva
coordenadas. Sem Per-Monitor V2, BoundingRectangle vem virtualizado e o clique de
fallback erra o alvo em telas com escala diferente de 100%.
"""

from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass

_log = logging.getLogger(__name__)

# Indices de GetSystemMetrics, spec §4.1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
PROCESS_PER_MONITOR_DPI_AWARE = 2
E_ACCESSDENIED = -2147024891  # ja definido: chamada repetida


def init_dpi_awareness() -> str:
    """Declara Per-Monitor V2. Retorna qual caminho funcionou (para diagnostico)."""
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ):
            return "PerMonitorV2"
    except (AttributeError, OSError) as exc:
        _log.debug("SetProcessDpiAwarenessContext unavailable: %s", exc)

    try:
        hr = ctypes.windll.shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
        if hr == 0:
            return "PerMonitor"
        if hr == E_ACCESSDENIED:
            return "already-set"
    except (AttributeError, OSError) as exc:
        _log.debug("SetProcessDpiAwareness unavailable: %s", exc)

    _log.warning("Could not set DPI awareness; coordinates may be virtualized.")
    return "unavailable"


@dataclass(frozen=True, slots=True)
class VirtualDesktop:
    left: int
    top: int
    width: int
    height: int

    @classmethod
    def current(cls) -> VirtualDesktop:
        gsm = ctypes.windll.user32.GetSystemMetrics
        return cls(
            left=gsm(SM_XVIRTUALSCREEN),
            top=gsm(SM_YVIRTUALSCREEN),
            width=gsm(SM_CXVIRTUALSCREEN),
            height=gsm(SM_CYVIRTUALSCREEN),
        )


def center_of(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = rect
    return ((left + right) // 2, (top + bottom) // 2)


def to_absolute_normalized(
    x: int, y: int, desktop: VirtualDesktop | None = None
) -> tuple[int, int]:
    """Converte px fisicos do desktop virtual para o espaco 0..65535 do SendInput.

    Necessario com MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK. Spec §4.1.
    """
    vd = desktop if desktop is not None else VirtualDesktop.current()
    nx = round((x - vd.left) * 65535 / max(vd.width - 1, 1))
    ny = round((y - vd.top) * 65535 / max(vd.height - 1, 1))
    return (max(0, min(65535, nx)), max(0, min(65535, ny)))


def get_dpi_for_window(hwnd: int) -> int:
    """DPI efetivo da janela. 96 = 100%. Cai para 96 em builds sem a API."""
    try:
        dpi = ctypes.windll.user32.GetDpiForWindow(ctypes.c_void_p(hwnd))
        return int(dpi) if dpi else 96
    except (AttributeError, OSError):
        return 96
```

- [ ] **Step 4: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dpi.py -q`
Expected: PASS — 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_windows_uia/uia tests/test_dpi.py
git commit -m "feat(dpi): Per-Monitor V2 e normalizacao do desktop virtual"
```

---

### Task 10: `uia/core.py` — cliente `CUIAutomation8`

**Files:**
- Create: `src/mcp_windows_uia/uia/core.py`
- Test: `tests/test_uia_core.py`

Implementa a spec §2.2 e §3.3. Este é o módulo que substitui o que `uiautomation` faria — e faz o que ela comprovadamente **não** consegue: `CUIAutomation8` com timeouts.

As tabelas de nomes (`ControlType`, patterns) são **derivadas por reflexão do módulo gerado**, não escritas à mão. Isso as mantém corretas por construção.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_uia_core.py`:

```python
from __future__ import annotations

import pytest

from mcp_windows_uia.uia import core

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def sta():
    """Estes testes tocam COM: precisam de STA nesta thread."""
    import comtypes

    comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
    yield
    core.reset_for_tests()


def test_control_type_names_derivados_do_typelib(sta: None) -> None:
    nomes = core.control_type_names()
    assert nomes[50000] == "Button"
    assert nomes[50004] == "Edit"
    assert nomes[50011] == "MenuItem"
    assert nomes[50032] == "Window"
    assert len(nomes) >= 40


def test_control_type_name_desconhecido_nao_explode(sta: None) -> None:
    assert core.control_type_name(999999) == "Unknown"


def test_pattern_availability_props_cobre_os_patterns_da_spec(sta: None) -> None:
    props = core.pattern_availability_props()
    esperados = {
        "Invoke", "Toggle", "SelectionItem", "ExpandCollapse", "Value",
        "RangeValue", "Scroll", "ScrollItem", "Text", "Grid", "Table", "Window",
    }
    assert esperados <= set(props)


def test_cliente_e_cuiautomation8_com_timeouts(sta: None) -> None:
    """A razao de existir deste modulo: o CUIAutomation legado nao expoe isto."""
    a = core.automation()
    assert a.iuia.ConnectionTimeout == 10000
    assert a.iuia.TransactionTimeout == 10000


def test_automation_e_singleton_por_thread(sta: None) -> None:
    assert core.automation() is core.automation()


def test_root_element_e_um_pane(sta: None) -> None:
    raiz = core.automation().root
    assert core.control_type_name(raiz.CurrentControlType) == "Pane"


def test_cache_request_aceita_as_propriedades_da_spec(sta: None) -> None:
    a = core.automation()
    assert a.build_cache_request(a.tree_props()) is not None


def test_condicoes_nativas_sao_construiveis(sta: None) -> None:
    a = core.automation()
    c1 = a.property_condition(a.UIA.UIA_ControlTypePropertyId, 50000)
    c2 = a.property_condition(a.UIA.UIA_NamePropertyId, "Salvar")
    assert a.and_conditions(c1, c2) is not None
    assert a.and_conditions(c1) is c1
    assert a.and_conditions() is a.true_condition


def test_element_from_handle_devolve_a_janela(sta: None) -> None:
    import ctypes

    hwnd = ctypes.windll.user32.GetDesktopWindow()
    assert core.automation().element_from_handle(hwnd) is not None


def test_captura_cacheada_le_propriedades_sem_rpc_extra(sta: None) -> None:
    """Regra dura da spec §3.3: FindAllBuildCache + propriedades Cached*."""
    a = core.automation()
    cr = a.build_cache_request(a.tree_props())
    filhos = a.root.FindAllBuildCache(a.UIA.TreeScope_Children, a.true_condition, cr)
    assert filhos.Length > 0
    primeiro = filhos.GetElement(0)
    _ = primeiro.CachedName
    _ = primeiro.CachedControlType
    _ = primeiro.CachedBoundingRectangle


def test_runtime_id_of_devolve_tupla(sta: None) -> None:
    rid = core.automation().runtime_id_of(core.automation().root)
    assert isinstance(rid, tuple) and len(rid) >= 1
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_core.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.uia.core'`

- [ ] **Step 3: Implementar `uia/core.py`**

```python
"""Cliente UI Automation cru sobre comtypes. Spec §2.2 e §3.3.

Decisao de projeto (spec §3.3): NAO usamos a lib `uiautomation`. Ela instancia o
coclass CUIAutomation legado, que nao expoe ConnectionTimeout/TransactionTimeout —
sem os quais um app travado pendura a thread do worker e o CA-23 e impossivel.
Aqui criamos CUIAutomation8/IUIAutomation6 diretamente.

As tabelas de nomes sao derivadas por reflexao do modulo gerado pelo typelib, e nao
escritas a mao: se a Microsoft acrescentar um ControlType, ele aparece sozinho.
"""

from __future__ import annotations

import re
import threading
from typing import Any

import comtypes
import comtypes.client

CONNECTION_TIMEOUT_MS = 10000
TRANSACTION_TIMEOUT_MS = 10000

_local = threading.local()
_uia_module: Any = None
_module_lock = threading.Lock()

_RE_CONTROL_TYPE = re.compile(r"^UIA_(\w+)ControlTypeId$")
_RE_PATTERN_AVAIL = re.compile(r"^UIA_Is(\w+)PatternAvailablePropertyId$")


def uia_module() -> Any:
    """Gera (uma vez) e devolve o modulo comtypes do typelib do UIAutomationCore."""
    global _uia_module
    if _uia_module is None:
        with _module_lock:
            if _uia_module is None:
                _uia_module = comtypes.client.GetModule("UIAutomationCore.dll")
    return _uia_module


def control_type_names() -> dict[int, str]:
    """{50000: 'Button', ...} derivado do typelib."""
    cache = getattr(_local, "control_type_names", None)
    if cache is None:
        UIA = uia_module()
        cache = {
            getattr(UIA, nome): m.group(1)
            for nome in dir(UIA)
            if (m := _RE_CONTROL_TYPE.match(nome))
        }
        _local.control_type_names = cache
    return cache


def control_type_name(control_type_id: int) -> str:
    return control_type_names().get(control_type_id, "Unknown")


def pattern_availability_props() -> dict[str, int]:
    """{'Invoke': UIA_IsInvokePatternAvailablePropertyId, ...} derivado do typelib."""
    cache = getattr(_local, "pattern_props", None)
    if cache is None:
        UIA = uia_module()
        cache = {
            m.group(1): getattr(UIA, nome)
            for nome in dir(UIA)
            if (m := _RE_PATTERN_AVAIL.match(nome))
        }
        _local.pattern_props = cache
    return cache


class Automation:
    """Fachada do IUIAutomation6. Instancia por thread — nunca compartilhar."""

    def __init__(self) -> None:
        self.UIA = uia_module()
        self.iuia = comtypes.client.CreateObject(
            self.UIA.CUIAutomation8, interface=self.UIA.IUIAutomation6
        )
        # Sem isto, uma chamada a um app travado nunca retorna. Spec §2.2 / CA-23.
        self.iuia.ConnectionTimeout = CONNECTION_TIMEOUT_MS
        self.iuia.TransactionTimeout = TRANSACTION_TIMEOUT_MS

        self.root = self.iuia.GetRootElement()
        self.true_condition = self.iuia.CreateTrueCondition()
        self.control_walker = self.iuia.ControlViewWalker
        self.raw_walker = self.iuia.RawViewWalker

    # ---------------------------------------------------------- cache requests

    def tree_props(self) -> tuple[int, ...]:
        """Propriedades exigidas pela spec §5.2 para a captura de arvore."""
        U = self.UIA
        base = (
            U.UIA_NamePropertyId,
            U.UIA_AutomationIdPropertyId,
            U.UIA_ClassNamePropertyId,
            U.UIA_ControlTypePropertyId,
            U.UIA_IsEnabledPropertyId,
            U.UIA_IsOffscreenPropertyId,
            U.UIA_IsKeyboardFocusablePropertyId,
            U.UIA_HasKeyboardFocusPropertyId,
            U.UIA_BoundingRectanglePropertyId,
            U.UIA_RuntimeIdPropertyId,
            U.UIA_ProcessIdPropertyId,
            U.UIA_IsPasswordPropertyId,
            U.UIA_ToggleToggleStatePropertyId,
            U.UIA_ExpandCollapseExpandCollapseStatePropertyId,
            U.UIA_SelectionItemIsSelectedPropertyId,
            U.UIA_ValueValuePropertyId,
            U.UIA_ValueIsReadOnlyPropertyId,
            U.UIA_RangeValueValuePropertyId,
        )
        return base + tuple(pattern_availability_props().values())

    def build_cache_request(
        self,
        properties: tuple[int, ...],
        *,
        scope: int | None = None,
        element_mode: int | None = None,
    ) -> Any:
        U = self.UIA
        cr = self.iuia.CreateCacheRequest()
        for prop in properties:
            cr.AddProperty(prop)
        cr.TreeScope = U.TreeScope_Subtree if scope is None else scope
        # ControlView, nao RawView: RawView infla a arvore com nos irrelevantes (§5.2).
        cr.TreeFilter = self.iuia.ControlViewCondition
        cr.AutomationElementMode = (
            U.AutomationElementMode_Full if element_mode is None else element_mode
        )
        return cr

    # --------------------------------------------------------------- condicoes

    def property_condition(self, property_id: int, value: Any) -> Any:
        return self.iuia.CreatePropertyCondition(property_id, value)

    def and_conditions(self, *conditions: Any) -> Any:
        if not conditions:
            return self.true_condition
        resultado = conditions[0]
        for cond in conditions[1:]:
            resultado = self.iuia.CreateAndCondition(resultado, cond)
        return resultado

    # --------------------------------------------------------------- elementos

    def element_from_handle(self, hwnd: int) -> Any:
        return self.iuia.ElementFromHandle(hwnd)

    def runtime_id_of(self, element: Any) -> tuple[int, ...]:
        try:
            return tuple(element.GetRuntimeId())
        except Exception:
            return ()


def automation() -> Automation:
    """Instancia por thread. Chamar so de dentro da thread do UiaWorker."""
    inst = getattr(_local, "automation", None)
    if inst is None:
        inst = Automation()
        _local.automation = inst
    return inst


def reset_for_tests() -> None:
    """Limpa o estado thread-local. So para testes."""
    for attr in ("automation", "control_type_names", "pattern_props"):
        if hasattr(_local, attr):
            delattr(_local, attr)
```

- [ ] **Step 4: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_core.py -q`
Expected: PASS — 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_windows_uia/uia/core.py tests/test_uia_core.py
git commit -m "feat(uia): cliente CUIAutomation8 com timeouts e tabelas do typelib"
```

---

### Task 11: `uia/windows.py` — enumeração de janelas

**Files:**
- Create: `src/mcp_windows_uia/uia/windows.py`
- Test: `tests/test_uia_windows.py`

Implementa a spec §8.1, §4.1 e §4.2. Usa `EnumWindows` (Win32) e não a árvore UIA: só o Win32 entrega `WS_VISIBLE`, `IsIconic` e placement de forma barata, e a spec §8.1 exige esses campos.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_uia_windows.py`:

```python
from __future__ import annotations

import os

import pytest

from mcp_windows_uia.uia.windows import (
    enumerate_windows,
    is_session_interactive,
    process_elevated,
    server_is_elevated,
    window_is_alive,
)

pytestmark = pytest.mark.e2e


def test_sessao_interativa_e_detectada() -> None:
    assert is_session_interactive() is True


def test_elevacao_do_servidor_e_um_bool() -> None:
    assert isinstance(server_is_elevated(), bool)


def test_processo_do_proprio_servidor_nao_e_inacessivel() -> None:
    assert process_elevated(os.getpid()) is False


def test_pid_inexistente_e_tratado_sem_excecao() -> None:
    assert process_elevated(999999) in (True, False)


def test_enumerate_devolve_janelas_com_campos_preenchidos() -> None:
    janelas = enumerate_windows()
    assert janelas, "nenhuma janela visivel — a sessao esta desbloqueada?"
    for j in janelas:
        assert j.hwnd > 0
        assert j.pid > 0
        assert len(j.rect) == 4
        assert j.dpi > 0


def test_enumerate_sem_hidden_exige_titulo() -> None:
    assert all(j.title for j in enumerate_windows(include_hidden=False))


def test_include_hidden_retorna_pelo_menos_tantas_janelas() -> None:
    assert len(enumerate_windows(include_hidden=True)) >= len(enumerate_windows())


def test_window_is_alive_para_janela_real_e_para_lixo() -> None:
    janelas = enumerate_windows()
    assert window_is_alive(janelas[0].hwnd) is True
    assert window_is_alive(1) is False
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_windows.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.uia.windows'`

- [ ] **Step 3: Implementar `uia/windows.py`**

```python
"""Enumeracao de janelas top-level. Spec §8.1, §4.1 e §4.2.

Usa EnumWindows em vez da arvore UIA: WS_VISIBLE, IsIconic e o placement da janela
saem do Win32 de graca, e a spec §8.1 exige esses campos. A arvore UIA entra depois,
nas tools de leitura (Plano 2).
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass

import psutil

from ..budget import truncate_name
from ..errors import Code, ToolError
from .dpi import get_dpi_for_window

_log = logging.getLogger(__name__)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
SW_SHOWMAXIMIZED = 3
SW_SHOWMINIMIZED = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ACCESS_DENIED = 5
MONITOR_DEFAULTTONEAREST = 2
DESKTOP_READOBJECTS = 0x0001

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.UINT),
        ("flags", wintypes.UINT),
        ("showCmd", wintypes.UINT),
        ("ptMinPosition", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("rcNormalPosition", wintypes.RECT),
    ]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


@dataclass(frozen=True, slots=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    process: str
    rect: tuple[int, int, int, int]
    visible: bool
    minimized: bool
    maximized: bool
    focused: bool
    dpi: int
    monitor: int
    monitor_device: str
    elevated: bool


# ------------------------------------------------------------------- ambiente


def is_session_interactive() -> bool:
    """Falso quando a estacao esta bloqueada ou a sessao RDP esta desconectada."""
    hdesk = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not hdesk:
        return False
    user32.CloseDesktop(hdesk)
    return True


def require_interactive_session() -> None:
    if not is_session_interactive():
        raise ToolError(
            Code.SESSION_UNAVAILABLE,
            "The desktop session is locked, disconnected or not interactive.",
        )


def server_is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def process_elevated(pid: int) -> bool:
    """Heuristica da spec §4.2: OpenProcess negado => integridade superior."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return False
    return kernel32.GetLastError() == ERROR_ACCESS_DENIED


# -------------------------------------------------------------------- janelas


def _title(hwnd: int) -> str:
    tamanho = user32.GetWindowTextLengthW(hwnd)
    if tamanho <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(tamanho + 1)
    user32.GetWindowTextW(hwnd, buf, tamanho + 1)
    return buf.value


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return (0, 0, 0, 0)
    return (r.left, r.top, r.right, r.bottom)


def _placement(hwnd: int) -> tuple[bool, bool]:
    wp = _WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(_WINDOWPLACEMENT)
    if not user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
        return (False, False)
    return (wp.showCmd == SW_SHOWMINIMIZED, wp.showCmd == SW_SHOWMAXIMIZED)


def _monitor(hwnd: int, indices: dict[str, int]) -> tuple[int, str]:
    hmon = user32.MonitorFromWindow(ctypes.c_void_p(hwnd), MONITOR_DEFAULTTONEAREST)
    info = _MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
    if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
        return (0, "")
    device = info.szDevice
    return (indices.setdefault(device, len(indices)), device)


def _process_name(pid: int) -> str:
    try:
        return psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return ""


def _is_tool_window(hwnd: int) -> bool:
    return bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW)


def enumerate_windows(*, include_hidden: bool = False) -> list[WindowInfo]:
    """Lista janelas top-level. Sem filtro de allowlist — isso e decisao da policy."""
    require_interactive_session()

    handles: list[int] = []

    def _coletar(hwnd: int, _lparam: int) -> bool:
        handles.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(_coletar), 0)

    foreground = user32.GetForegroundWindow()
    indices_monitor: dict[str, int] = {}
    resultado: list[WindowInfo] = []

    for hwnd in handles:
        visivel = bool(user32.IsWindowVisible(hwnd))
        titulo = _title(hwnd)
        if not include_hidden:
            if not visivel or not titulo:
                continue
            if user32.GetWindow(hwnd, GW_OWNER):
                continue  # pertencente a outra janela: nao e top-level de verdade
            if _is_tool_window(hwnd):
                continue

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        minimizada, maximizada = _placement(hwnd)
        indice, device = _monitor(hwnd, indices_monitor)

        resultado.append(
            WindowInfo(
                hwnd=hwnd,
                title=truncate_name(titulo),
                pid=int(pid.value),
                process=_process_name(int(pid.value)),
                rect=_rect(hwnd),
                visible=visivel,
                minimized=minimizada,
                maximized=maximizada,
                focused=(hwnd == foreground),
                dpi=get_dpi_for_window(hwnd),
                monitor=indice,
                monitor_device=device,
                elevated=process_elevated(int(pid.value)),
            )
        )
    return resultado


def window_is_alive(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))
```

- [ ] **Step 4: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_uia_windows.py -q`
Expected: PASS — 8 passed

Verificação manual rápida:

```bash
.venv/Scripts/python.exe -c "from mcp_windows_uia.uia.windows import enumerate_windows; [print(w.process, w.title[:40], w.rect, 'dpi', w.dpi) for w in enumerate_windows()[:8]]"
```

- [ ] **Step 5: Commit**

```bash
git add src/mcp_windows_uia/uia/windows.py tests/test_uia_windows.py
git commit -m "feat(uia): enumeracao de janelas com dpi, monitor e elevacao"
```

---

### Task 12: `context.py`, `server.py` e `__main__.py` — FastMCP e `uia_list_windows`

**Files:**
- Create: `src/mcp_windows_uia/context.py`
- Create: `src/mcp_windows_uia/server.py`
- Create: `src/mcp_windows_uia/__main__.py`
- Test: `tests/test_server_list_windows.py`

Implementa a spec §8.1 e §11.1. O `context.py` existe para que `server.py` não vire depósito de singletons.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_server_list_windows.py`:

```python
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_server_list_windows.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_windows_uia.context'`

- [ ] **Step 3: Implementar `context.py`**

```python
"""Estado unico do processo: config, policy, refs, auditoria e worker."""

from __future__ import annotations

from .audit import AuditLog
from .config import Config
from .policy import Policy
from .refs import RefStore
from .worker import UiaWorker


class ServerContext:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.policy = Policy(config)
        self.refs = RefStore(
            ttl_s=config.server.ref_ttl_s,
            max_entries=config.server.ref_cache_max,
        )
        self.audit = AuditLog(config.audit)
        self.worker = UiaWorker(com_timeout_ms=config.server.com_timeout_ms)

    def start(self) -> None:
        self.worker.start()
        self.audit.purge_old()

    def shutdown(self) -> None:
        self.worker.shutdown()


_context: ServerContext | None = None


def set_context(ctx: ServerContext) -> None:
    global _context
    _context = ctx


def context() -> ServerContext:
    if _context is None:  # pragma: no cover - so acontece se main() nao rodou
        raise RuntimeError("ServerContext not initialised; call set_context() first.")
    return _context
```

- [ ] **Step 4: Implementar `server.py`**

```python
"""Definicao das tools MCP. Spec §8 e §11.1.

Padrao de toda tool:
  1. valida argumentos
  2. valida policy (allowlist -> read-only -> rate limit)
  3. executa na thread do worker
  4. audita, sempre — sucesso e falha
Nenhuma excecao sai daqui como traceback: o decorator converte tudo para o §9.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .context import ServerContext, context
from .errors import Code, ToolError
from .uia.windows import enumerate_windows, server_is_elevated

_log = logging.getLogger(__name__)

mcp = FastMCP("windows-uia")


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


# ------------------------------------------------------------------------ 8.1


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
```

> **Se o FastMCP não conseguir montar o schema** por causa do `functools.wraps` no
> decorator, inverta a ordem: registre uma função de assinatura explícita e chame o
> corpo decorado por dentro. Verifique com o Step 7 antes de mexer — na maioria das
> versões o `inspect.signature` segue o `__wrapped__` e funciona como está.

- [ ] **Step 5: Implementar `__main__.py`**

```python
"""Entrypoint. Spec §11.1.

Ordem obrigatoria: DPI awareness -> logging em stderr -> config -> contexto -> stdio.
stdout e o canal JSON-RPC e nao pode receber nem um byte fora do protocolo (CA-22).
"""

from __future__ import annotations

import argparse
import logging
import sys

from .uia.dpi import init_dpi_awareness


def _configurar_logging(nivel: str) -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, nivel.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # comtypes e barulhento em debug e escreveria no logger raiz.
    logging.getLogger("comtypes").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(prog="mcp-windows-uia")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    _configurar_logging(args.log_level)
    metodo_dpi = init_dpi_awareness()

    from .config import load_config
    from .context import ServerContext, set_context
    from .server import mcp

    config = load_config(args.config, force_read_only=args.read_only)
    ctx = ServerContext(config)
    ctx.start()
    set_context(ctx)

    logging.info(
        "mcp-windows-uia starting (read_only=%s, dpi=%s, allowlist=%d processes, config=%s)",
        config.server.read_only,
        metodo_dpi,
        len(config.allowlist.processes),
        config.source_path or "<defaults>",
    )
    if not config.allowlist.processes and config.allowlist.mode == "allow":
        logging.warning(
            "Allowlist is EMPTY: every tool will return APP_NOT_ALLOWED. "
            "Add executables to [allowlist] processes in config.toml and restart."
        )

    try:
        mcp.run(transport="stdio")
    finally:
        ctx.shutdown()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Rodar os testes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_server_list_windows.py -q`
Expected: PASS — 9 passed

- [ ] **Step 7: Verificar que o servidor sobe sem traceback**

```bash
.venv/Scripts/python.exe -u -m mcp_windows_uia --config config.example.toml --read-only --log-level WARNING < /dev/null
```
Expected: encerra silenciosamente (stdin fechado). Qualquer traceback aqui é bug.

- [ ] **Step 8: Commit**

```bash
git add src/mcp_windows_uia/context.py src/mcp_windows_uia/server.py \
        src/mcp_windows_uia/__main__.py tests/test_server_list_windows.py
git commit -m "feat(server): FastMCP stdio, contexto do servidor e uia_list_windows"
```

---

### Task 13: CA-01 e CA-22 — aceitação via cliente MCP real

**Files:**
- Create: `tests/e2e/__init__.py`
- Create: `tests/e2e/test_ca01_ca22.py`

A spec §12 exige que os cenários rodem **contra o servidor via cliente MCP stdio real**, não chamando funções Python. É isso que valida handshake, schemas e higiene de stdout.

- [ ] **Step 1: Escrever o teste de aceitação**

`tests/e2e/__init__.py`: arquivo vazio.

`tests/e2e/test_ca01_ca22.py`:

```python
"""CA-01 (descoberta de janelas) e CA-22 (higiene de stdout). Spec §12."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

RAIZ = Path(__file__).resolve().parents[2]
PYTHON = RAIZ / ".venv" / "Scripts" / "python.exe"


def _config(tmp_path: Path) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(
        '[allowlist]\nmode = "allow"\n'
        'processes = ["notepad.exe", "explorer.exe"]\n'
        f'[audit]\ndir = "{tmp_path.as_posix()}/audit"\n',
        encoding="utf-8",
    )
    return p


def _framed(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8") + b"\n"


def _conversar(config: Path, mensagens: list[dict], espera_s: float = 30.0) -> tuple[bytes, bytes]:
    """Sobe o servidor, envia as mensagens por stdin e devolve (stdout, stderr) crus."""
    env = dict(os.environ, PYTHONUTF8="1")
    proc = subprocess.Popen(
        [str(PYTHON), "-u", "-m", "mcp_windows_uia", "--config", str(config)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(RAIZ),
        env=env,
    )
    try:
        out, err = proc.communicate(input=b"".join(_framed(m) for m in mensagens), timeout=espera_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        pytest.fail(f"servidor nao respondeu em {espera_s}s. stderr:\n{err.decode(errors='replace')}")
    return out, err


INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "ca-tests", "version": "0"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
CALL = {
    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
    "params": {"name": "uia_list_windows", "arguments": {"max_results": 40}},
}


def _respostas(stdout: bytes) -> dict[int, dict]:
    saida: dict[int, dict] = {}
    for linha in stdout.decode("utf-8").splitlines():
        if not linha.strip():
            continue
        msg = json.loads(linha)  # CA-22: toda linha tem de ser JSON valido
        if "id" in msg:
            saida[msg["id"]] = msg
    return saida


def test_ca22_todo_byte_de_stdout_e_jsonrpc(tmp_path: Path) -> None:
    """CA-22: nenhum log, warning de comtypes, print ou traceback contamina stdout."""
    out, err = _conversar(_config(tmp_path), [INIT, INITIALIZED, TOOLS_LIST, CALL])
    assert out.strip(), f"stdout vazio. stderr:\n{err.decode(errors='replace')}"
    for linha in out.decode("utf-8").splitlines():
        if linha.strip():
            json.loads(linha)


def test_ca01_descoberta_de_janelas(tmp_path: Path) -> None:
    """CA-01: uia_list_windows devolve refs w<n>, pid correto e rect nao vazio."""
    inicio = time.perf_counter()
    out, err = _conversar(_config(tmp_path), [INIT, INITIALIZED, CALL])
    decorrido = time.perf_counter() - inicio

    respostas = _respostas(out)
    assert 3 in respostas, f"sem resposta para tools/call. stderr:\n{err.decode(errors='replace')}"

    dados = json.loads(respostas[3]["result"]["content"][0]["text"])
    assert dados["ok"] is True
    assert isinstance(dados["server_elevated"], bool)
    assert dados["windows"], "nenhuma janela listada — a sessao esta desbloqueada?"
    for janela in dados["windows"]:
        assert re.fullmatch(r"w\d+", janela["ref"])
        assert janela["pid"] > 0
        assert len(janela["rect"]) == 4
    # o teto de 1,5 s da spec e da chamada; aqui o tempo inclui o boot do interpretador
    assert decorrido < 15.0


def test_tools_list_expoe_uia_list_windows_com_a_descricao_da_spec(tmp_path: Path) -> None:
    """Neste plano so uia_list_windows existe. Os Planos 2 e 3 elevam este numero a 11."""
    out, _ = _conversar(_config(tmp_path), [INIT, INITIALIZED, TOOLS_LIST])
    ferramentas = _respostas(out)[2]["result"]["tools"]
    nomes = {t["name"] for t in ferramentas}
    assert "uia_list_windows" in nomes
    descricao = next(t for t in ferramentas if t["name"] == "uia_list_windows")["description"]
    assert "top-level windows" in descricao
```

- [ ] **Step 2: Rodar o teste**

Run: `.venv/Scripts/python.exe -m pytest tests/e2e/test_ca01_ca22.py -q -s`
Expected: PASS — 3 passed

Se falhar por timeout em `_conversar`, o problema é o enquadramento das mensagens. O SDK do MCP em stdio usa **uma mensagem JSON por linha**, sem cabeçalhos `Content-Length`. Se a versão instalada do `mcp` exigir outro enquadramento, ajuste `_framed` — e **só** `_framed`.

- [ ] **Step 3: Rodar a suíte inteira**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — todos os testes das Tasks 2–13 verdes.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e
git commit -m "test(e2e): CA-01 descoberta de janelas e CA-22 higiene de stdout"
```

---

## Definição de pronto (Plano 1)

- [ ] `pytest -q` verde
- [ ] `tools/list` expõe `uia_list_windows` com a descrição da spec §8.1
- [ ] CA-01 e CA-22 passam via cliente MCP stdio real
- [ ] Nenhum byte não-JSON-RPC em stdout
- [ ] `config.example.toml` com allowlist vazia (falha fechada) e aviso no stderr ao subir
- [ ] Nada de `uiautomation` em `pyproject.toml` nem nos imports
- [ ] `Automation.iuia.ConnectionTimeout == 10000` verificado por teste

## Cobertura de critérios de aceitação

| CA | Status neste plano |
|---|---|
| CA-01 descoberta de janelas | ✅ Task 13 |
| CA-09 limite absoluto de nós | ⚠️ parcial — `Limits.build` (Task 5); o teste ponta-a-ponta é do Plano 2 |
| CA-13 allowlist bloqueia | ⚠️ parcial — `Policy` (Task 4) e auditoria de negação (Task 7); o cenário completo depende de `uia_get_tree`, Plano 2 |
| CA-14 modo somente-leitura | ⚠️ parcial — `check_mutating` (Task 4); precisa de uma tool mutante, Plano 3 |
| CA-15 redação de senha | ⚠️ parcial — auditoria (Task 7); a metade de `uia_get_value` é do Plano 2 |
| CA-22 higiene de stdout | ✅ Task 13 |
| CA-23 robustez a app travado | ⚠️ parcial — timeouts em Task 8 e Task 10; o cenário é do Plano 2 |
| Demais CAs | Planos 2 e 3 |

**Próximo:** Plano 2 — captura de árvore cacheada, filtros, poda, orçamento, rebind de refs (spec §7.2) e as cinco tools de leitura (`uia_get_tree`, `uia_get_text`, `uia_find_elements`, `uia_get_value`, `uia_wait_for`).
