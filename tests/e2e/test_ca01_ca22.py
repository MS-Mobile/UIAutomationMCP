"""CA-01 (descoberta de janelas) e CA-22 (higiene de stdout). Spec §12.

A spec exige que estes cenarios rodem contra o servidor via cliente MCP stdio real,
nao chamando funcoes Python — e isso que valida handshake, schemas e stdout.
"""

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


def _conversar(config: Path, mensagens: list[dict], espera_s: float = 40.0) -> tuple[bytes, bytes]:
    """Sobe o servidor, conversa por stdin/stdout e devolve (stdout, stderr) crus.

    Mantem o stdin ABERTO ate ter lido a resposta de cada requisicao com id. Fechar
    o stdin antes disso faz o servidor iniciar o shutdown com a chamada ainda em voo,
    e a requisicao volta como -32000 'Connection closed'.
    """
    env = dict(os.environ, PYTHONUTF8="1")
    proc = subprocess.Popen(
        [str(PYTHON), "-u", "-m", "mcp_windows_uia", "--config", str(config)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(RAIZ),
        env=env,
    )
    assert proc.stdin is not None and proc.stdout is not None

    esperados = {m["id"] for m in mensagens if "id" in m}
    linhas: list[bytes] = []
    limite = time.monotonic() + espera_s

    try:
        proc.stdin.write(b"".join(_framed(m) for m in mensagens))
        proc.stdin.flush()

        vistos: set[int] = set()
        while vistos != esperados:
            if time.monotonic() > limite:
                raise subprocess.TimeoutExpired(proc.args, espera_s)
            linha = proc.stdout.readline()
            if not linha:  # servidor fechou stdout
                break
            linhas.append(linha)
            if linha.strip():
                try:
                    msg = json.loads(linha)
                except json.JSONDecodeError:
                    continue  # sujeira em stdout: o teste do CA-22 e quem acusa
                if isinstance(msg, dict) and "id" in msg:
                    vistos.add(msg["id"])
    except (subprocess.TimeoutExpired, OSError):
        proc.kill()
        _, err = proc.communicate()
        pytest.fail(
            f"servidor nao respondeu em {espera_s}s. "
            f"stdout parcial:\n{b''.join(linhas).decode(errors='replace')}\n"
            f"stderr:\n{err.decode(errors='replace')}"
        )

    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        resto, err = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        resto, err = proc.communicate()
    return b"".join(linhas) + resto, err


INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "ca-tests", "version": "0"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
CALL = {
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
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


def _payload_da_tool(resposta: dict) -> dict:
    """Extrai o dict da tool, aceitando conteudo estruturado ou texto."""
    resultado = resposta["result"]
    if "structuredContent" in resultado and resultado["structuredContent"]:
        return resultado["structuredContent"]
    return json.loads(resultado["content"][0]["text"])


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

    dados = _payload_da_tool(respostas[3])
    assert dados["ok"] is True
    assert isinstance(dados["server_elevated"], bool)
    assert dados["windows"], "nenhuma janela listada — a sessao esta desbloqueada?"
    for janela in dados["windows"]:
        assert re.fullmatch(r"w\d+", janela["ref"])
        assert janela["pid"] > 0
        assert len(janela["rect"]) == 4
    # o teto de 1,5 s da spec e da chamada; aqui o tempo inclui o boot do interpretador
    assert decorrido < 20.0


def test_tools_list_expoe_uia_list_windows_com_a_descricao_da_spec(tmp_path: Path) -> None:
    """Neste plano so uia_list_windows existe. Os Planos 2 e 3 elevam este numero a 11."""
    out, err = _conversar(_config(tmp_path), [INIT, INITIALIZED, TOOLS_LIST])
    respostas = _respostas(out)
    assert 2 in respostas, f"sem resposta para tools/list. stderr:\n{err.decode(errors='replace')}"
    ferramentas = respostas[2]["result"]["tools"]
    nomes = {t["name"] for t in ferramentas}
    assert "uia_list_windows" in nomes
    ferramenta = next(t for t in ferramentas if t["name"] == "uia_list_windows")
    assert "top-level windows" in ferramenta["description"]
    # o schema tem de expor os 4 parametros da spec §8.1
    propriedades = set(ferramenta["inputSchema"]["properties"])
    assert propriedades == {
        "include_minimized",
        "include_hidden",
        "process_filter",
        "max_results",
    }
