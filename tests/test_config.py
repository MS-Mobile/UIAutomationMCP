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
