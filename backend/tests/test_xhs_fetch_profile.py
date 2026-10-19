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
