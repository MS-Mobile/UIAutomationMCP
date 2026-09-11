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
    from .errors import ToolError
    from .server import mcp

    # Config invalida e erro do usuario, nao bug: mensagem acionavel em stderr,
    # nunca um traceback. O agente nem chega a ver isso — o cliente MCP so ve o
    # servidor morrer, entao a mensagem precisa ser legivel por gente.
    try:
        config = load_config(args.config, force_read_only=args.read_only)
    except ToolError as exc:
        logging.error("%s", exc.message)
        logging.error("%s", exc.hint)
        raise SystemExit(2) from None

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
