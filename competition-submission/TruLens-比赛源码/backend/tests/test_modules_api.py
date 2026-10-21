"""Contract tests for the backend-owned report, team and creator modules."""

from __future__ import annotations

from tests.conftest import TINY_PNG


def test_report_contains_server_cross_checks_decision_and_confidence(client):
    upload = client.post(
        "/api/upload",
        data={
            "text": "连续使用七天，皮肤立刻白了一个度，敏感肌也可以放心闭眼冲。",
        },
    )
    assert upload.status_code == 201
    task_id = upload.json()["task_id"]
    report = client.get(f"/api/report/{task_id}").json()

    assert report["cross_checks"]
    assert {item["state"] for item in report["cross_checks"]} <= {
        "conflict",
        "consistent",
        "corroborated",
        "doubtful",
        "unverified",
    }
    assert report["decision"]["tier"] in {"pass", "verify", "review"}
    assert {
        item["perspective"] for item in report["decision"]["advice"]
    } == {"consumer", "creator", "platform", "brand"}
    assert report["confidence"]["assessed_count"] == sum(
        dimension["assessed"] for dimension in report["dimensions"]
    )


def test_team_queue_reads_real_tasks_and_persists_review_status(client):
    upload = client.post(
        "/api/upload",
        data={"text": "这款精华7天立刻变白，百分百有效。"},
    )
    task_id = upload.json()["task_id"]

    queue = client.get("/api/team/queue").json()
    item = next(entry for entry in queue["items"] if entry["task_id"] == task_id)
    assert item["title"]
    assert item["risk_level"] in {"low", "medium", "high"}
    assert item["actions"]["platform"]

    update = client.patch(
        f"/api/team/queue/{task_id}",
        json={"status": "done"},
    )
    assert update.status_code == 200
    assert update.json()["status"] == "done"

    refreshed = client.get("/api/team/queue").json()
    refreshed_item = next(
        entry for entry in refreshed["items"] if entry["task_id"] == task_id
    )
    assert refreshed_item["status"] == "done"


def test_creator_check_returns_real_agent_report_and_issues(client):
    response = client.post(
        "/api/creator/check",
        data={
            "draft": "连续使用七天，皮肤立刻白了一个度，敏感肌也可以放心闭眼冲。",
            "is_collaboration": "true",
            "category": "护肤",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["task_id"]
    assert body["sample_id"]
    assert body["publication_state"] in {"ready", "revise", "hold"}
    assert body["copy_score"] is not None
    assert body["issues"]
    assert all(issue["reason"] for issue in body["issues"])
    assert body["report"]["task_id"] == body["task_id"]
    assert body["report"]["assessed_dimensions"] == ["text"]


def test_creator_check_runs_image_agent_when_image_is_supplied(client):
    response = client.post(
        "/api/creator/check",
        data={
            "draft": "换季使用两周，个人感觉保湿感有提升，效果因人而异。",
            "is_collaboration": "false",
            "category": "护肤",
        },
        files={"image": ("creator.png", TINY_PNG, "image/png")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["image_score"] is not None
    assert {"text", "image"} <= set(body["report"]["assessed_dimensions"])


def test_sample_trace_finds_exact_image_hash_reuse(client):
    first = client.post(
        "/api/upload",
        data={"text": "第一篇图片素材", "auto_analyze": "false"},
        files={"image": ("same.png", TINY_PNG, "image/png")},
    ).json()
    second = client.post(
        "/api/upload",
        data={"text": "第二篇复用图片", "auto_analyze": "false"},
        files={"image": ("same-copy.png", TINY_PNG, "image/png")},
    ).json()

    trace = client.get(f"/api/samples/{second['sample_id']}/trace")
    assert trace.status_code == 200
    body = trace.json()
    assert body["sha256"]
    assert any(match["sample_id"] == first["sample_id"] for match in body["matches"])
