from __future__ import annotations

from pathlib import Path

import pytest

import app.core.retry as retry

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "data" / "sample"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real backoff timing is irrelevant in unit tests; keep them fast."""
    monkeypatch.setattr(retry, "DEFAULT_INITIAL_WAIT", 0)
    monkeypatch.setattr(retry, "DEFAULT_MAX_WAIT", 0)
    monkeypatch.setattr(retry, "DEFAULT_JITTER", 0)


@pytest.fixture
def sample_dir() -> Path:
    return SAMPLE_DIR
