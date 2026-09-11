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
