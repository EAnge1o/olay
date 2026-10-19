"""Xiaohongshu link-ingestion unit tests.

Everything here is pure — no browser, no subprocess — so this suite stays fast
and green even on a machine without Chrome. The live crawl is exercised by
``scripts/xhs_smoke_test.py`` instead.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.xhs_service import (
    RESULT_MARKER,
    XhsError,
    XhsService,
    _normalize_comments,
    _parse_marker,
    canonical_note_url,
    canonical_profile_url,
    extract_note_id,
    extract_user_id,
    extract_xsec_token,
)

NOTE_ID = "674a1b2c3d4e5f60718293a4"
TOKEN = "ABCdef123456"


def _service(enabled: bool = True) -> XhsService:
    return XhsService(enabled=enabled, timeout_seconds=30, max_comments=10)


# -- URL normalisation -------------------------------------------------------


def test_extracts_note_id_from_every_share_form():
    assert extract_note_id(f"https://www.xiaohongshu.com/explore/{NOTE_ID}") == NOTE_ID
    assert extract_note_id(f"https://www.xiaohongshu.com/discovery/item/{NOTE_ID}?xsec_token=abc") == NOTE_ID
    # Share text wraps the URL in prose, so the bare-id fallback has to work.
    assert extract_note_id(f"看看这篇笔记 {NOTE_ID} 超好用") == NOTE_ID
    assert extract_note_id("nothing here") is None
    assert extract_note_id(None) is None


def test_explore_url_is_used_verbatim_so_the_xsec_token_survives():
    """Stripping the query would drop the token and bounce us to the feed."""
    url = f"https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token={TOKEN}&xsec_source=pc_share"
    assert _service().normalize(f"看看这个 {url} 分享给你") == url


def test_discovery_item_url_is_rewritten_and_keeps_the_token():
    url = _service().normalize(
        f"https://www.xiaohongshu.com/discovery/item/{NOTE_ID}?xsec_token={TOKEN}&xsec_source=pc_share"
    )
    assert url == canonical_note_url(NOTE_ID, TOKEN)


def test_bare_id_share_text_rebuilds_the_canonical_url():
    assert _service().normalize(f"9.9 复制打开小红书 {NOTE_ID}") == canonical_note_url(NOTE_ID)


def test_profile_url_is_normalised_and_keeps_the_token():
    user_id = "64f0a1b2c3d4e5f60718293a"
    url = f"https://www.xiaohongshu.com/user/profile/{user_id}?xsec_token={TOKEN}&xsec_source=pc_share"
    assert extract_user_id(url) == user_id
    assert _service().normalize_account(f"看看主页 {url}") == url
    assert canonical_profile_url(user_id, TOKEN) == (
        f"https://www.xiaohongshu.com/user/profile/{user_id}"
        f"?xsec_token={TOKEN}&xsec_source=pc_share"
    )


def test_extract_xsec_token():
    assert extract_xsec_token(f"https://x.com/a?xsec_token={TOKEN}&b=1") == TOKEN
    assert extract_xsec_token("no token here") is None
    assert extract_xsec_token(None) is None


def test_unrecognised_input_raises_xhs_error():
    with pytest.raises(XhsError) as excinfo:
        _service().normalize("https://example.com/whatever")
    assert "无法识别" in excinfo.value.reason


def test_empty_input_raises_xhs_error():
    with pytest.raises(XhsError):
        _service().normalize("   ")


# -- availability ------------------------------------------------------------


def test_disabled_service_reports_a_reason_and_refuses_to_fetch():
    service = _service(enabled=False)
    assert service.available is False
    assert "XHS_ENABLED" in service.unavailable_reason()


def test_fetch_refuses_when_disabled():
    with pytest.raises(XhsError) as excinfo:
        asyncio.run(_service(enabled=False).fetch(f"https://www.xiaohongshu.com/explore/{NOTE_ID}"))
    assert "XHS_ENABLED" in excinfo.value.reason


# -- runner contract ---------------------------------------------------------


def test_parse_marker_reads_the_last_result_and_ignores_crawler_noise():
    stdout = "\n".join(
        [
            "没有找到关闭按钮，自动重试 1",
            f"{RESULT_MARKER}{{\"ok\": false, \"error\": \"superseded\"}}",
            f"{RESULT_MARKER}{{\"ok\": true, \"content\": \"正文\"}}",
        ]
    )
    assert _parse_marker(stdout) == {"ok": True, "content": "正文"}


def test_parse_marker_returns_none_without_a_marker():
    assert _parse_marker("just logs\n") is None
    assert _parse_marker(None) is None


def test_normalize_comments_yields_the_pipeline_shape():
    comments = _normalize_comments(
        [
            {"content": "好用", "author": "u1"},
            {"content": "   "},          # blank -> dropped
            "not-a-dict",                # wrong type -> dropped
            {"content": "回购了"},
        ]
    )
    assert [c["content"] for c in comments] == ["好用", "回购了"]
    assert set(comments[0]) == {"index", "content", "author", "likes", "created_at"}
    assert comments[0]["author"] == "u1"
    assert comments[1]["author"] is None


def test_fetch_attaches_author_homepage_snapshot(monkeypatch):
    service = _service()
    payload = {
        "ok": True,
        "title": "示例笔记",
        "content": "正文",
        "images": [],
        "comments": [],
        "author": {"handle": "作者A"},
        "account": {
            "profile_url": "https://www.xiaohongshu.com/user/profile/user-a",
            "handle": "作者A",
            "profile_text": "美妆分享",
            "follower_count": 12000,
            "total_post_count": 42,
            "posts": [
                {"text": "第一篇", "published_at": "2026-10-01T10:00:00+00:00"},
                {"text": "第二篇", "published_at": "2026-10-02T10:00:00+00:00"},
                {"text": "第三篇", "published_at": "2026-10-03T10:00:00+00:00"},
            ],
        },
        "account_error": None,
    }
    monkeypatch.setattr(service, "_run_runner", lambda *args, **kwargs: payload)

    note = asyncio.run(
        service.fetch(f"https://www.xiaohongshu.com/explore/{NOTE_ID}")
    )

    assert note.author_handle == "作者A"
    assert note.account is not None
    assert note.account.follower_count == 12000
    assert len(note.account.posts) == 3
