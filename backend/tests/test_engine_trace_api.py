"""Contract tests for the additive engine-trace endpoint and the health field."""

from __future__ import annotations


def test_health_reports_the_active_analysis_backend(client):
    body = client.get("/api/health").json()
    # The pytest environment never enables the engine, so heuristics must win.
    assert body["analysis_backend"] == "heuristics"


def test_engine_trace_unknown_task_returns_404(client):
    resp = client.get("/api/engine-trace/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_engine_trace_absent_when_heuristics_ran(client):
    task_id = client.post(
        "/api/upload", data={"text": "这支精华100%修复敏感肌，7天白一个度，皮肤科医生推荐。"}
    ).json()["task_id"]

    # The analysis itself still succeeds on the heuristic backend.
    assert client.get(f"/api/status/{task_id}").json()["status"] == "completed"
    assert client.get(f"/api/report/{task_id}").status_code == 200

    resp = client.get(f"/api/engine-trace/{task_id}")
    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["code"] == "not_found"
    assert error["message"] == "engine trace not available for this task"
    assert error["details"]["task_id"] == task_id
    assert "reason" in error["details"]
