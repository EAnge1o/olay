"""Pytest fixtures.

Environment is redirected to a throwaway SQLite file + storage dir *before*
``app.main`` is imported, so the real ``get_settings`` cache and the module-level
engine bind to test paths.
"""

from __future__ import annotations

import base64
import os
import pathlib
import sys
import tempfile

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="tg-test-"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["STORAGE_DIR"] = str(_TMP / "uploads")
os.environ["SQL_ECHO"] = "false"
os.environ["ENABLE_LLM"] = "false"
os.environ["DEBUG"] = "false"

# Hermetic environment. pydantic-settings ranks real env vars ABOVE the `.env`
# file, so these overrides win — a developer's local `.env` (real provider keys,
# engine on, crawler on) must never leak into the suite and turn a unit test into
# a paid API call or a browser launch.
os.environ["TRUST_ENGINE_ENABLED"] = "false"
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["GLM_API_KEY"] = ""
os.environ["QWEN_API_KEY"] = ""
os.environ["DASHSCOPE_API_KEY"] = ""
os.environ["XHS_ENABLED"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# A valid 1x1 PNG — enough to exercise the image ingest path.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def sample_payload() -> dict:
    import json

    path = pathlib.Path(__file__).parent / "fixtures" / "sample_payload.json"
    return json.loads(path.read_text(encoding="utf-8"))
