"""Estado unico do processo: config, policy, refs, auditoria e worker.

Existe para que server.py nao vire um deposito de singletons soltos.
"""

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
