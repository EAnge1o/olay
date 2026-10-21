"""End-to-end API tests over the real ASGI app with a temp SQLite database.

Run:  pytest
"""

from __future__ import annotations

import json

from tests.conftest import TINY_PNG


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "sqlite"


def test_agents_are_described(client):
    resp = client.get("/api/agents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pipeline"] == ["account", "image", "comment", "text", "report"]
    dims = body["dimensions"]
    assert [d["code"] for d in dims] == ["T", "R", "U", "S"]
    assert {d["name"] for d in dims} == {"account", "image", "comment", "text"}


def test_upload_requires_content(client):
    resp = client.post("/api/upload", data={})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_unknown_task_returns_404(client):
    assert client.get("/api/status/does-not-exist").status_code == 404
    assert client.get("/api/report/does-not-exist").status_code == 404


def test_full_upload_analyze_report_flow(client, sample_payload):
    # 1. upload (auto_analyze=True runs the pipeline as a background task)
    resp = client.post(
        "/api/upload",
        data={
            "text": sample_payload["text"],
            "comments": json.dumps(sample_payload["comments"], ensure_ascii=False),
            "source": "pytest",
        },
        files={"image": ("shot.png", TINY_PNG, "image/png")},
    )
    assert resp.status_code == 201, resp.text
    upload = resp.json()
    task_id = upload["task_id"]
    assert upload["comment_count"] == len(sample_payload["comments"])
    assert upload["has_image"] is True

    # 2. status should be terminal by the time TestClient returns
    status = client.get(f"/api/status/{task_id}").json()
    assert status["status"] in {"completed", "running", "pending"}
    assert {a["agent"] for a in status["agents"]} == {"account", "image", "comment", "text"}

    if status["status"] != "completed":
        # BackgroundTasks normally complete inline; re-run explicitly if not.
        assert client.post(f"/api/analyze/{task_id}", json={}).status_code == 200
        status = client.get(f"/api/status/{task_id}").json()

    assert status["status"] == "completed"
    assert status["progress"] == 100
    assert status["report_ready"] is True

    # 3. report
    report_resp = client.get(f"/api/report/{task_id}")
    assert report_resp.status_code == 200, report_resp.text
    report = report_resp.json()

    assert 0 <= report["overall_score"] <= 100
    assert report["trust_score"] == round(100 - report["overall_score"], 1)
    assert report["risk_level"] in {"low", "medium", "high"}
    # The fixture copy is deliberately non-compliant: the text and comment
    # dimensions must both come out high-risk. The *aggregate* band follows the
    # engine's weights (text .35 / image .30 / comment .20), and the fixture's
    # image is clean, so the fused score lands in the 存疑 band rather than 高风险.
    assert report["risk_level"] in {"medium", "high"}
    assert report["trust_label"] in {"存疑", "低可信"}
    assert report["evidence"], "report must carry evidence"
    assert report["recommendations"], "report must carry recommendations"

    # text / comment / image all had input, so all three are scored.
    assert set(report["assessed_dimensions"]) == {"text", "comment", "image"}
    by_code = {d["code"]: d for d in report["dimensions"]}
    for code in ("R", "U", "S"):
        assert by_code[code]["assessed"] is True
        assert by_code[code]["trust_score"] is not None
    assert by_code["S"]["risk_level"] == "high"
    assert by_code["U"]["risk_level"] == "high"
    # No account history was supplied -> T is explicitly not scored.
    assert by_code["T"]["assessed"] is False
    assert by_code["T"]["trust_score"] is None


def test_text_only_upload_is_analysable(client):
    resp = client.post(
        "/api/upload",
        data={"text": "这是一支普通面霜，保湿不错，早晚使用，敏感肌可用，已回购两次。"},
    )
    assert resp.status_code == 201
    task_id = resp.json()["task_id"]
    report = client.get(f"/api/report/{task_id}")
    assert report.status_code == 200
    assert report.json()["risk_level"] in {"low", "medium", "high"}
