"""Standalone smoke test — a demonstrative end-to-end run with pretty output.

    python scripts/smoke_test.py

Exercises: upload (text + comments + image + account history) -> analyze ->
status -> report. Uses an isolated temp SQLite DB and temp storage, so it never
touches dev data.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import sys
import tempfile

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

# --- isolate runtime state BEFORE importing the app ---
_TMP = pathlib.Path(tempfile.mkdtemp(prefix="tg-smoke-"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_TMP / 'smoke.db').as_posix()}"
os.environ["STORAGE_DIR"] = str(_TMP / "uploads")
os.environ["SQL_ECHO"] = "false"
os.environ["ENABLE_LLM"] = "false"
os.environ["DEBUG"] = "false"
# This script exists to prove the built-in heuristics path, so pin it regardless
# of what the developer's .env says (real env vars beat the .env file).
os.environ["TRUST_ENGINE_ENABLED"] = "false"
os.environ["XHS_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PAYLOAD_PATH = BACKEND_ROOT / "tests" / "fixtures" / "sample_payload.json"
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

SEVERITY_ICON = {"high": "[HIGH]", "medium": "[MED]", "low": "[LOW]"}

# Account history for the T (账号可信度) dimension — 3 clean posts so the
# pattern screening runs, plus a handle/follower context.
ACCOUNT_HISTORY = {
    "handle": "眠云的护肤手账",
    "profile_text": "敏感肌 / 成分党 / 只写自己用过的",
    "follower_count": 35000,
    "total_post_count": 174,
    "posts": [
        {"text": "空瓶记录：这支精华用了两周，两颊泛红有缓解，痘印淡得不明显，要配合防晒。",
         "published_at": "2026-08-02T09:10:00Z", "like_count": 412},
        {"text": "夏天混合皮的面霜选择，质地偏厚的那支留给冬天，清透的这支出油后也能用。",
         "published_at": "2026-08-19T20:40:00Z", "like_count": 268},
        {"text": "平价卸妆膏横评：三支里只有一支乳化彻底，其余会闷痘，附上成分表截图。",
         "published_at": "2026-09-05T11:25:00Z", "like_count": 507},
    ],
}


def _rule(char: str = "-", width: int = 78) -> str:
    return char * width


def _image_bytes() -> bytes:
    """Prefer a 1024x1024 PNG (trips the image heuristic); fall back to 1x1."""
    try:
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (1024, 1024), (232, 200, 200)).save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 - Pillow is optional
        return TINY_PNG


def main() -> int:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    failures: list[str] = []

    with TestClient(app) as client:
        print(_rule("="))
        print("TruLens 本地 Smoke Test — 美妆内容可信度核验（T/R/U/S 四维）")
        print(_rule("="))

        # 1) system
        health = client.get("/api/health")
        print(f"\n[1/5] GET /api/health      -> {health.status_code} {health.json()}")
        if health.status_code != 200:
            failures.append("health endpoint failed")

        agents = client.get("/api/agents").json()
        print(f"      pipeline: {' -> '.join(agents['pipeline'])}")
        for dim in agents["dimensions"]:
            print(f"        ({dim['code']}) {dim['label']:<12} 输入={dim['input']}  {dim['question']}")

        # 2) upload
        resp = client.post(
            "/api/upload",
            data={
                "text": payload["text"],
                "comments": json.dumps(payload["comments"], ensure_ascii=False),
                "account_history": json.dumps(ACCOUNT_HISTORY, ensure_ascii=False),
                "source": payload.get("source", "smoke"),
            },
            files={"image": ("product.png", _image_bytes(), "image/png")},
        )
        print(f"\n[2/5] POST /api/upload     -> {resp.status_code}")
        if resp.status_code != 201:
            print(f"      FAILED: {resp.text}")
            return 1
        upload = resp.json()
        task_id = upload["task_id"]
        print(f"      task_id={task_id}")
        print(
            f"      sample_id={upload['sample_id']}  comments={upload['comment_count']}  "
            f"image={upload['has_image']}  account_posts={upload['account_post_count']}"
        )

        # 3) analyze (explicit; upload already auto-triggered, this proves idempotency)
        analyze = client.post(f"/api/analyze/{task_id}", json={"use_llm": False})
        print(f"\n[3/5] POST /api/analyze/{task_id[:8]}... -> {analyze.status_code}")

        # 4) status
        status = client.get(f"/api/status/{task_id}").json()
        print(f"\n[4/5] GET  /api/status     -> {status['status']} (progress {status['progress']}%)")
        for item in status["agents"]:
            verdict = (
                f"risk={item['risk_score']} trust={item['trust_score']}"
                if item["assessed"]
                else "暂不评分"
            )
            print(
                f"        - {item['code']} {item['label']:<12} {item['status']:<10} {verdict} "
                f"({item.get('duration_ms')}ms)"
            )
        if status["status"] != "completed":
            failures.append(f"task did not complete: {status['status']}")
        if not status["report_ready"]:
            failures.append("report not ready")

        # 5) report
        report_resp = client.get(f"/api/report/{task_id}")
        print(f"\n[5/5] GET  /api/report     -> {report_resp.status_code}")
        if report_resp.status_code != 200:
            print(f"      FAILED: {report_resp.text}")
            return 1
        report = report_resp.json()

        print("\n" + _rule("="))
        print("最终可信度报告")
        print(_rule("="))
        print(f"  风险等级   : {report['risk_level'].upper()} / 可信等级: {report['trust_label']}")
        print(f"  可信分     : {report['trust_score']} / 100   （风险分 {report['overall_score']}）")
        print(f"  已评估维度 : {report['assessed_dimensions']}")

        print("\n  四维明细:")
        for dim in report["dimensions"]:
            score = f"{dim['trust_score']} / 100" if dim["trust_score"] is not None else "暂不评分"
            print(f"    ({dim['code']}) {dim['label']:<12} {score:<12} {dim['summary']}")

        print(f"\n  结论: {report['summary']}")

        print(f"\n  证据（{len(report['evidence'])} 条）:")
        for item in report["evidence"]:
            icon = SEVERITY_ICON.get(item["severity"], "[?]")
            print(f"    {icon} [{item['source']}/{item['type']}] (conf={item['confidence']}) {item['evidence']}")

        print(f"\n  建议（{len(report['recommendations'])} 条）:")
        for idx, rec in enumerate(report["recommendations"], 1):
            print(f"    {idx}. {rec}")

        print("\n" + _rule("="))
        if failures:
            print("结果: FAILED")
            for failure in failures:
                print(f"  - {failure}")
            return 1
        print("结果: PASSED — 上传 -> 四维分析 -> 报告 全链路可运行")
        print(_rule("="))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
