"""API contract tests — lock down the exact wire behavior the frontend depends on.

Each assertion here mirrors a statement in ``docs/API_CONTRACT.md``.
"""

from __future__ import annotations

import json

from tests.conftest import TINY_PNG

UPLOAD_KEYS = {
    "task_id",
    "sample_id",
    "status",
    "comment_count",
    "text_length",
    "has_image",
    "account_post_count",
    "source_url",
}
STATUS_KEYS = {
    "task_id",
    "sample_id",
    "status",
    "progress",
    "requested_agents",
    "error_message",
    "created_at",
    "started_at",
    "finished_at",
    "report_ready",
    "agents",
}
AGENT_STATUS_KEYS = {
    "agent",
    "code",
    "label",
    "status",
    "assessed",
    "risk_level",
    "risk_score",
    "trust_level",
    "trust_score",
    "trust_label",
    "duration_ms",
    "error_message",
}
REPORT_KEYS = {
    "id",
    "task_id",
    "overall_score",
    "trust_score",
    "risk_level",
    "trust_level",
    "trust_label",
    "summary",
    "evidence",
    "recommendations",
    "dimension_scores",
    "dimensions",
    "cross_checks",
    "decision",
    "confidence",
    "assessed_dimensions",
    "generated_at",
    "created_at",
}
DIMENSION_KEYS = {
    "key",
    "code",
    "label",
    "question",
    "input_label",
    "assessed",
    "trust_score",
    "risk_score",
    "risk_level",
    "trust_level",
    "trust_label",
    "summary",
    "evidence",
    "limitations",
}


def _upload(client, **kwargs):
    return client.post("/api/upload", **kwargs)


def test_upload_response_has_exact_documented_keys(client):
    resp = _upload(client, data={"text": "普通面霜，保湿不错，敏感肌可用，已回购两次。"})
    assert resp.status_code == 201
    assert set(resp.json().keys()) == UPLOAD_KEYS
    # status is always "pending" in the response, even with auto_analyze=true
    assert resp.json()["status"] == "pending"


def test_auto_analyze_false_leaves_task_pending(client):
    resp = _upload(client, data={"text": "普通面霜，保湿不错，已回购两次。", "auto_analyze": "false"})
    assert resp.status_code == 201
    task_id = resp.json()["task_id"]

    status = client.get(f"/api/status/{task_id}").json()
    assert status["status"] == "pending"
    assert status["progress"] == 0
    assert status["report_ready"] is False
    # Agent rows do not exist until the pipeline starts.
    assert status["agents"] == []
    assert status["started_at"] is None and status["finished_at"] is None


def test_status_and_report_use_exact_documented_keys(client):
    task_id = _upload(
        client,
        data={"text": "姐妹们！这支精华100%修复敏感肌，7天白一个度，皮肤科医生推荐。"},
    ).json()["task_id"]

    status = client.get(f"/api/status/{task_id}").json()
    assert set(status.keys()) == STATUS_KEYS
    assert set(status["agents"][0].keys()) == AGENT_STATUS_KEYS
    # Four dimensions in canonical T/R/U/S order.
    assert [a["agent"] for a in status["agents"]] == ["account", "image", "comment", "text"]
    assert [a["code"] for a in status["agents"]] == ["T", "R", "U", "S"]

    report = client.get(f"/api/report/{task_id}").json()
    assert set(report.keys()) == REPORT_KEYS
    assert set(report["evidence"][0].keys()) == {
        "source",
        "type",
        "evidence",
        "confidence",
        "severity",
        "reason",
    }
    assert [d["code"] for d in report["dimensions"]] == ["T", "R", "U", "S"]
    assert report["cross_checks"]
    assert report["decision"]["tier"] in {"pass", "verify", "review"}
    assert report["confidence"]["level"] in {"high", "medium", "low"}
    assert set(report["dimensions"][0].keys()) == DIMENSION_KEYS
    assert report["trust_score"] == round(100 - report["overall_score"], 1)
    assert report["risk_level"] in {"low", "medium", "high"}
    assert report["trust_level"] in {"low", "medium", "high"}
    assert report["trust_label"] in {"高可信", "存疑", "低可信"}


def test_timestamps_are_timezone_aware(client):
    """Naive timestamps would be parsed as *local* time by browsers."""
    task_id = _upload(client, data={"text": "普通面霜，保湿不错，已回购两次。"}).json()["task_id"]

    status = client.get(f"/api/status/{task_id}").json()
    for key in ("created_at", "started_at", "finished_at"):
        assert status[key].endswith("+00:00"), f"{key} is not UTC-offset: {status[key]}"

    report = client.get(f"/api/report/{task_id}").json()
    for key in ("created_at", "generated_at"):
        assert report[key].endswith("+00:00"), f"{key} is not UTC-offset: {report[key]}"


def test_sample_detail_and_image_are_retrievable(client):
    task_id = _upload(
        client,
        data={"text": "混合皮，夏天用有点黏。", "comments": json.dumps([{"content": "好用"}])},
        files={"image": ("product.png", TINY_PNG, "image/png")},
    ).json()
    sample_id = task_id["sample_id"]

    detail = client.get(f"/api/samples/{sample_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["text_content"] == "混合皮，夏天用有点黏。"
    assert body["comment_count"] == 1
    assert body["comments"][0]["content"] == "好用"
    assert body["image"]["content_type"] == "image/png"
    assert body["image"]["size"] == len(TINY_PNG)
    assert body["image"]["url"] == f"/api/samples/{sample_id}/image"

    image = client.get(body["image"]["url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content == TINY_PNG


def test_sample_without_image_returns_null_image(client):
    sample_id = _upload(client, data={"text": "普通面霜，保湿不错，已回购两次。"}).json()["sample_id"]
    assert client.get(f"/api/samples/{sample_id}").json()["image"] is None
    resp = client.get(f"/api/samples/{sample_id}/image")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_unknown_sample_returns_404(client):
    resp = client.get("/api/samples/nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_whitespace_only_text_is_rejected(client):
    resp = _upload(client, data={"text": "   \n  "})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_empty_comments_array_alone_is_rejected(client):
    resp = _upload(client, data={"comments": "[]"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_unsupported_image_type_returns_415(client):
    resp = _upload(
        client, data={"text": "普通面霜。"}, files={"image": ("x.txt", b"hi", "text/plain")}
    )
    assert resp.status_code == 415
    body = resp.json()
    assert body["error"]["code"] == "unsupported_media_type"
    assert "image/png" in body["error"]["details"]["allowed"]


def test_analyze_rejects_unknown_agent(client):
    task_id = _upload(
        client, data={"text": "普通面霜。", "auto_analyze": "false"}
    ).json()["task_id"]
    resp = client.post(f"/api/analyze/{task_id}", json={"agents": ["bogus"]})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"]["available"] == ["account", "image", "comment", "text"]


def test_analyze_accepts_empty_body_and_subset(client):
    task_id = _upload(
        client, data={"text": "普通面霜，保湿不错，已回购两次。", "auto_analyze": "false"}
    ).json()["task_id"]

    resp = client.post(f"/api/analyze/{task_id}", json={"agents": ["text"]})
    assert resp.status_code == 200
    assert resp.json()["requested_agents"] == ["text"]

    status = client.get(f"/api/status/{task_id}").json()
    assert status["status"] == "completed"
    assert [a["agent"] for a in status["agents"]] == ["text"]


def test_analyze_unknown_task_returns_404(client):
    resp = client.post("/api/analyze/nope", json={})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_report_not_ready_returns_404_with_status_detail(client):
    task_id = _upload(
        client, data={"text": "普通面霜。", "auto_analyze": "false"}
    ).json()["task_id"]
    resp = client.get(f"/api/report/{task_id}")
    assert resp.status_code == 404
    body = resp.json()["error"]
    assert body["code"] == "not_found"
    assert body["message"] == "report not ready yet"
    assert body["details"]["status"] == "pending"


def test_comments_are_normalized_to_a_uniform_shape(client):
    """Line-separated input must produce the exact same keys as JSON input."""
    sample_id = _upload(client, data={"text": "普通面霜。", "comments": "好用\n不错\n\n回购"}).json()[
        "sample_id"
    ]
    comments = client.get(f"/api/samples/{sample_id}").json()["comments"]

    assert [c["content"] for c in comments] == ["好用", "不错", "回购"]
    for comment in comments:
        assert set(comment.keys()) == {"index", "content", "author", "likes", "created_at"}


# -- T (账号可信度) dimension ------------------------------------------------


def test_account_history_is_stored_and_the_T_dimension_is_scored(client):
    history = {
        "handle": "demo-account",
        "follower_count": 35000,
        "total_post_count": 174,
        "posts": [
            {"text": "日常护肤记录：这款精华用了一周，脸颊泛红有缓解。", "published_at": "2026-09-01T09:00:00Z", "like_count": 120},
            {"text": "今天分享一个小众面霜，质地偏厚，冬天用不错。", "published_at": "2026-09-03T18:30:00Z", "like_count": 88},
            {"text": "入手了同款，链接放在橱窗，品牌方合作，优惠券下单更划算。", "published_at": "2026-09-08T20:05:00Z", "like_count": 30},
        ],
    }
    upload = _upload(
        client, data={"text": "普通面霜，保湿不错。", "account_history": json.dumps(history)}
    ).json()
    assert upload["account_post_count"] == 3

    sample = client.get(f"/api/samples/{upload['sample_id']}").json()
    assert sample["account"]["handle"] == "demo-account"
    assert sample["account"]["post_count"] == 3
    assert sample["account"]["follower_count"] == 35000
    assert sample["account"]["posts"][0]["id"] == "post-1"

    report = client.get(f"/api/report/{upload['task_id']}").json()
    account = next(d for d in report["dimensions"] if d["code"] == "T")
    assert account["assessed"] is True
    assert account["trust_score"] == round(100 - account["risk_score"], 1)
    assert account["label"] == "账号可信度"
    assert "account" in report["assessed_dimensions"]


def test_dimensions_without_input_are_not_scored(client):
    """No image -> R is 'assessed: false', so the UI renders 暂不评分, not a 0."""
    upload = _upload(client, data={"text": "普通面霜，保湿不错，已回购两次。"}).json()
    report = client.get(f"/api/report/{upload['task_id']}").json()

    image = next(d for d in report["dimensions"] if d["code"] == "R")
    assert image["assessed"] is False
    assert image["trust_score"] is None
    assert image["risk_score"] is None
    assert image["trust_label"] is None
    assert "image" not in report["assessed_dimensions"]

    account = next(d for d in report["dimensions"] if d["code"] == "T")
    assert account["assessed"] is False  # no history supplied at all


def test_account_history_below_the_pattern_threshold_is_not_scored(client):
    upload = _upload(
        client,
        data={"text": "普通面霜。", "account_history": json.dumps({"posts": ["只有一篇"]})},
    ).json()
    report = client.get(f"/api/report/{upload['task_id']}").json()
    account = next(d for d in report["dimensions"] if d["code"] == "T")
    assert account["assessed"] is False
    assert account["trust_score"] is None
    assert "不足" in account["summary"]


# -- Xiaohongshu link ingestion ---------------------------------------------


def test_link_upload_is_rejected_when_crawling_is_disabled(client):
    """The test environment never enables XHS, so a link must 422 immediately."""
    resp = _upload(
        client,
        data={"url": "https://www.xiaohongshu.com/explore/674a1b2c3d4e5f60718293a4"},
    )
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "validation_error"
    assert "无法抓取该链接" in error["message"]

