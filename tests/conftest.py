"""Shared fixtures.

App tests run against a fresh SQLite file per test, created by running the real
Alembic migrations, so the migration chain is exercised on every run.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

# Set BEFORE app.* imports so get_settings() reads them.
os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("PARENT_PASSWORD", "parent-pw")
os.environ.setdefault("SECRET_KEY", "test-secret-key-test-secret-key!")
os.environ.setdefault("STT_TYPED_FALLBACK", "1")
os.environ.setdefault("WHISPER_URL", "http://whisper.invalid:9000")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    from alembic.config import Config
    from fastapi.testclient import TestClient

    from alembic import command
    from app.core.config import get_settings
    from app.db import session as db_session

    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    db_session._engine = None
    db_session._session_factory = None

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(cfg, "head")

    from app.main import create_app
    from app.web import login_throttle

    login_throttle.reset()
    with TestClient(create_app()) as c:
        yield c

    if db_session._engine is not None:
        db_session._engine.dispose()
    db_session._engine = None
    db_session._session_factory = None
    get_settings.cache_clear()
