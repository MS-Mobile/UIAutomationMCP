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
