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

    # ------------------------------------------------------------------ escrita

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
        # Regra absoluta (CA-15): senha nunca e gravada, nem com log_values='full'.
        if is_password:
            return {"value": REDACTED_PASSWORD}
        modo = self._cfg.log_values
        if modo == "none":
            return {}
        if modo == "full":
            return {"value": value}
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        return {"value": REDACTED, "value_len": len(value), "value_sha256": digest}

    # ----------------------------------------------------------------- retencao

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
