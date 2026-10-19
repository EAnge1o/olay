from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_fetcher():
    path = Path(__file__).resolve().parents[1] / "scripts" / "xhs_fetch.py"
    spec = importlib.util.spec_from_file_location("xhs_fetch_profile_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_profile_state_normalises_counts_and_posts():
    fetcher = _load_fetcher()
    profile = fetcher._normalize_profile(
        {
            "basic_info": {
                "nickname": "测试作者",
                "desc": "美妆分享",
            },
            "interactions": [
                {"type": "fans", "count": "1.2万"},
                {"type": "notes", "count": "42"},
            ],
            "notes": [
                {"title": "第一篇", "published_at": 1760000000000, "like_count": "25"},
                {"title": "第二篇", "published_at": "2026-10-02", "like_count": 8},
                {"title": "第一篇", "published_at": 1760000000000, "like_count": 25},
                {"title": "第三篇", "published_at": None, "like_count": "1,024"},
            ],
        },
        profile_url="https://www.xiaohongshu.com/user/profile/user-a",
        max_posts=12,
    )

    assert profile["handle"] == "测试作者"
    assert profile["profile_text"] == "美妆分享"
    assert profile["follower_count"] == 12_000
    assert profile["total_post_count"] == 42
    assert [item["text"] for item in profile["posts"]] == ["第一篇", "第二篇", "第三篇"]
    assert profile["posts"][2]["like_count"] == 1024


def test_relative_profile_path_is_anchored_to_backend_root(monkeypatch):
    """A relative XHS_USER_DATA_PATH must not follow the runner's cwd.

    The crawl runner is spawned with ``cwd=backend/scripts`` and then chdirs into
    a temp dir before Chrome starts. Resolving a relative profile path against
    either of those hands Chrome an empty profile, so the session logged in by
    ``scripts/xhs_login.py`` is silently dropped.
    """
    fetcher = _load_fetcher()
    monkeypatch.setenv("XHS_USER_DATA_PATH", "storage/chrome-profile")
    monkeypatch.delenv("XHS_LOCAL_PORT", raising=False)

    captured: dict[str, str] = {}

    class _StubOptions:
        def set_user_data_path(self, path: str) -> None:
            captured["profile"] = path

        def headless(self, *_args, **_kwargs) -> None:  # pragma: no cover
            raise AssertionError("headless should not be set")

        def set_timeouts(self, *_args, **_kwargs) -> None:
            return None

    monkeypatch.setitem(
        __import__("sys").modules,
        "DrissionPage",
        type("_StubDrissionPage", (), {"ChromiumOptions": lambda: _StubOptions()}),
    )

    fetcher._build_browser_options()

    resolved = Path(captured["profile"])
    assert resolved.is_absolute()
    assert resolved == (fetcher.BACKEND_ROOT / "storage" / "chrome-profile").resolve()


def test_settings_expose_absolute_profile_path():
    """XhsService must hand the runner an absolute profile, never the raw value."""
    from app.core.config import BACKEND_ROOT, Settings
    from app.services.xhs_service import XhsService

    settings = Settings(xhs_user_data_path=Path("storage/chrome-profile"))
    resolved = settings.xhs_user_data_path_absolute
    assert resolved is not None
    assert resolved.is_absolute()
    assert resolved == (BACKEND_ROOT / "storage" / "chrome-profile").resolve()

    service = XhsService.from_settings(settings)
    assert Path(service._user_data_path).is_absolute()
