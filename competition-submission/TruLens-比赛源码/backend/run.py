"""Convenience entrypoint: ``python run.py``."""

from __future__ import annotations

import uvicorn

from app.core.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
        # Without this the watcher also sees .venv and storage: installing a
        # package or writing an upload restarts the server (and floods the log
        # with thousands of change notifications).
        reload_excludes=[".venv*", "storage*", "*.db", "__pycache__*"],
    )


if __name__ == "__main__":
    main()
