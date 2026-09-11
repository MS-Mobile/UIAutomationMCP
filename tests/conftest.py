from __future__ import annotations

import sys

import pytest


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Pula testes marcados como e2e fora do Windows."""
    if "e2e" in item.keywords and not sys.platform.startswith("win"):
        pytest.skip("e2e exige Windows")
