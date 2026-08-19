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
