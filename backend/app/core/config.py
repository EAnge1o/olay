"""Application settings.

Single source of truth for configuration, loaded from environment variables
and an optional ``.env`` file. Never read ``os.environ`` directly elsewhere —
depend on :func:`get_settings` so tests can override by env var.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ — used to build absolute defaults so the app behaves identically
# regardless of the current working directory (uvicorn, pytest, docker).
BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- App ---------------------------------------------------------------
    # Brand name shared by the frontend, this API and the deck.
    app_name: str = "TruLens"
    environment: str = "development"
    debug: bool = True
    api_prefix: str = "/api"
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    # -- Database ----------------------------------------------------------
    # Development default: async SQLite. Production: postgresql+asyncpg://...
    database_url: str = (
        f"sqlite+aiosqlite:///{(BACKEND_ROOT / 'storage' / 'trulens.db').as_posix()}"
    )
    sql_echo: bool = False

    # -- Storage -----------------------------------------------------------
    storage_dir: Path = BACKEND_ROOT / "storage" / "uploads"
    max_upload_size_mb: int = 10
    allowed_image_types: list[str] = Field(
        default_factory=lambda: ["image/jpeg", "image/png", "image/webp"]
    )

    # -- LLM ---------------------------------------------------------------
    # "mock" keeps the platform fully runnable with no API key.
    llm_provider: str = "mock"
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 30
    enable_llm: bool = False

    # -- Trust engine (the Applied-AI delivery: content-trust-engine-v10) ----
    # When enabled, the real ``ContentTrustAgent`` produces the text / comment /
    # image / account dimensions instead of the built-in heuristics. If the
    # ``content_trust`` package is missing (or the engine fails) the service
    # silently falls back to the heuristics, so the demo never depends on it.
    # Default: off.
    trust_engine_enabled: bool = False
    # Credentials are read from the same .env and passed straight to the engine.
    # Without them the engine still runs in local-rule mode (text/comment only).
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    # -- Vision provider (the image / cross-modal role) ---------------------
    # GLM (Zhipu) is the team's choice and takes precedence. The engine's
    # built-in Qwen-VL client stays available as a fallback for anyone who still
    # holds a DashScope key. With neither configured, the image dimension
    # reports "unavailable" rather than inventing a verdict.
    # NOTE: the model must be multimodal — the image role sends the picture.
    glm_api_key: str | None = None
    glm_base_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    glm_model: str = "glm-5.3-flashx"
    glm_timeout_seconds: float = 180.0
    # Rich cross-modal JSON (observations + claims + evidence) can exceed 16384
    # tokens on full-page event listings and other long OCR-style images.
    # 32768 is accepted by the configured model and keeps the JSON complete.
    glm_max_tokens: int = 32768
    # Transport-level retries for the image role. The free GLM tiers rate-limit
    # aggressively (HTTP 429), and a 429 makes the dimension report
    # "unavailable" — the OpenAI SDK retries with exponential backoff and honours
    # `Retry-After`, which is exactly what a burst of calls needs.
    glm_max_retries: int = 3
    # Use the standard data URI by default. Bare base64 works for some JPEGs
    # but GLM can return an empty response for browser-ingested WebP images
    # unless the MIME type is included in the request.
    glm_image_url_format: str = "data_uri"

    # Qwen-VL — engine built-in fallback, used only when GLM_API_KEY is absent.
    qwen_api_key: str | None = None
    # Legacy DashScope alias still accepted by the engine's own .env.
    dashscope_api_key: str | None = None
    qwen_vl_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_vl_model: str = "qwen3.8-flash"
    qwen_vl_timeout_seconds: float = 180.0

    # -- Xiaohongshu link ingestion -----------------------------------------
    # Drives ``olay/xhs/XhsExtractor/Crawler.py`` (team-owned) through the
    # subprocess runner in ``scripts/xhs_fetch.py``. Needs Chrome plus
    # ``pip install -r requirements-xhs.txt``. When disabled, or when the crawl
    # fails, the task fails with a clear message rather than guessing content.
    xhs_enabled: bool = False
    xhs_timeout_seconds: int = 240
    xhs_max_comments: int = 80
    # Optional persistent Chrome profile. Left unset by default so the team's
    # crawler keeps its original behaviour (its own throwaway browser profile);
    # set it to reuse a session you logged into once. Note detail is gated on
    # login, and a logged-out automated session gets bounced to a "安全限制" page.
    xhs_user_data_path: Path | None = None
    xhs_headless: bool = False
    # Optional: attach to an already-running Chrome on this debug port instead of
    # launching one. Most reliable for demos — log into Xiaohongshu in your own
    # Chrome (started with --remote-debugging-port=9222) and we reuse that session.
    xhs_local_port: int | None = None

    # -- Derived -----------------------------------------------------------
    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def xhs_user_data_path_absolute(self) -> Path | None:
        """The persistent Chrome profile as an absolute path, or ``None``.

        A relative ``XHS_USER_DATA_PATH`` is resolved against the backend root
        rather than the process cwd: the crawl runner is spawned with a
        different cwd (and then chdirs into a temp dir), so a relative value
        would silently point Chrome at a brand-new empty profile and the logged-in
        session would never be reused.
        """
        if self.xhs_user_data_path is None:
            return None
        path = self.xhs_user_data_path.expanduser()
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        return path

    def ensure_directories(self) -> None:
        """Create storage directories. Safe to call repeatedly."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
