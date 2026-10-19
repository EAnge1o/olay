"""End-to-end smoke test with the Applied-AI engine enabled.

    python scripts/engine_smoke_test.py

Runs the *real* ASGI app with TRUST_ENGINE_ENABLED=true against an isolated
temp database, so it never touches development data. Requires the
``content_trust`` package (see requirements-engine.txt); exits with a clear
message if it is missing.

Works without DEEPSEEK/QWEN keys too — the engine then reports the text and
comment dimensions from its local rules and marks the image dimension
``unavailable``.
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

# --- isolate runtime state and force the engine on, BEFORE importing the app ---
_TMP = pathlib.Path(tempfile.mkdtemp(prefix="tg-engine-"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_TMP / 'engine.db').as_posix()}"
os.environ["STORAGE_DIR"] = str(_TMP / "uploads")
os.environ["SQL_ECHO"] = "false"
os.environ["DEBUG"] = "false"
os.environ["ENABLE_LLM"] = "false"
os.environ["TRUST_ENGINE_ENABLED"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

TEXT = (
    "姐妹们！这支寡肽精华真的绝了！我是敏感肌，用了7天痘印就淡了，毛孔也消失了，"
    "皮肤科医生推荐，100%修复敏感肌，无副作用零风险，一次见效，7天白一个度，"
    "立竿见影，坚持用能根治痘印。首先质地很清爽，其次吸收很快，最后性价比超高，家人们闭眼入！"
)
COMMENTS = [
    {"author": "u1", "content": "好用", "created_at": "2026-10-01T10:00:00Z"},
    {"author": "u2", "content": "好用", "created_at": "2026-10-01T10:00:20Z"},
    {"author": "u3", "content": "好用", "created_at": "2026-10-01T10:00:31Z"},
    {"author": "u4", "content": "真的太好用了，果断入手", "created_at": "2026-10-01T10:00:44Z"},
    {"author": "u5", "content": "真的太好用了，果断入手！", "created_at": "2026-10-01T10:00:52Z"},
    {"author": "u6", "content": "我用了两周，两颊泛红缓解了，但痘印淡得不明显，要配合防晒。", "created_at": "2026-10-02T09:12:00Z"},
]

# T (账号可信度) dimension input: 4 posts, one of which is a bare commercial
# pitch, so the account role has a real pattern to screen.
ACCOUNT_HISTORY = {
    "handle": "cutie_beauty88",
    "profile_text": "每天一支好物分享 / 合作请私信",
    "follower_count": 8200,
    "total_post_count": 460,
    "posts": [
        {"id": "p1", "text": "今天也是这瓶精华，闭眼入，链接在橱窗，领券下单更划算。",
         "published_at": "2026-09-28T09:00:00Z", "like_count": 21},
        {"id": "p2", "text": "又回购了，姐妹们冲，商品卡直接拍，品牌方合作款。",
         "published_at": "2026-09-28T09:12:00Z", "like_count": 18},
        {"id": "p3", "text": "无脑入就对了，优惠券领取后更便宜，旗舰店同款。",
         "published_at": "2026-09-28T09:26:00Z", "like_count": 15},
        {"id": "p4", "text": "用了两周，脸颊泛红有缓解，但痘印变化不明显，需要配合防晒。",
         "published_at": "2026-09-30T21:05:00Z", "like_count": 140},
    ],
}


def _rule(char: str = "=", width: int = 78) -> str:
    return char * width


def _image_bytes() -> bytes:
    try:
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (1024, 1024), (232, 200, 200)).save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 - Pillow is optional
        return TINY_PNG


def main() -> int:
    failures: list[str] = []

    try:
        import content_trust  # noqa: F401
    except ImportError as exc:
        print("SKIPPED: `content_trust` is not installed.")
        print("Install it first:  pip install -r requirements-engine.txt   (from backend/)")
        print(f"detail: {exc}")
        return 2

    with TestClient(app) as client:
        print(_rule())
        print("TruLens — 引擎接入 Smoke Test (TRUST_ENGINE_ENABLED=true)")
        print(_rule())

        health = client.get("/api/health").json()
        print(f"\n[1/5] backend = {health['analysis_backend']}")
        if health["analysis_backend"] != "trust_engine":
            failures.append(f"analysis_backend is {health['analysis_backend']}, expected trust_engine")

        resp = client.post(
            "/api/upload",
            data={
                "text": TEXT,
                "comments": json.dumps(COMMENTS, ensure_ascii=False),
                "account_history": json.dumps(ACCOUNT_HISTORY, ensure_ascii=False),
                "source": "engine_smoke",
            },
            files={"image": ("product.png", _image_bytes(), "image/png")},
        )
        if resp.status_code != 201:
            print(f"      upload FAILED: {resp.text}")
            return 1
        upload = resp.json()
        task_id, sample_id = upload["task_id"], upload["sample_id"]
        print(
            f"[2/5] POST /api/upload -> 201 task_id={task_id[:12]}... "
            f"comments={upload['comment_count']} account_posts={upload['account_post_count']}"
        )

        status = client.get(f"/api/status/{task_id}").json()
        print(f"[3/5] GET  /api/status -> {status['status']} ({status['progress']}%)")
        for item in status["agents"]:
            verdict = (
                f"risk={item['risk_score']} trust={item['trust_score']}"
                if item["assessed"]
                else "暂不评分"
            )
            print(f"        - {item['code']} {item['label']:<12} {item['status']:<10} {verdict}")
        if status["status"] != "completed":
            failures.append(f"task did not complete: {status['status']}")

        report_resp = client.get(f"/api/report/{task_id}")
        if report_resp.status_code != 200:
            print(f"      report FAILED: {report_resp.text}")
            return 1
        report = report_resp.json()
        print(f"\n[4/5] GET  /api/report -> {report_resp.status_code}")
        print(
            f"        risk_level={report['risk_level']}  trust_level={report['trust_label']}  "
            f"risk={report['overall_score']}  trust={report['trust_score']}"
        )
        print(f"        assessed_dimensions={report['assessed_dimensions']}")
        for dim in report["dimensions"]:
            score = f"{dim['trust_score']} / 100" if dim["trust_score"] is not None else "暂不评分"
            print(f"          ({dim['code']}) {dim['label']:<12} {score}")
        print(f"        evidence={len(report['evidence'])} 条, recommendations={len(report['recommendations'])} 条")
        # The full response must stay shape-compatible with the frozen contract.
        expected_report_keys = {
            "id", "task_id", "overall_score", "trust_score", "risk_level", "trust_level",
            "trust_label", "summary", "evidence", "recommendations", "dimension_scores",
            "dimensions", "assessed_dimensions", "generated_at", "created_at",
        }
        if set(report.keys()) != expected_report_keys:
            failures.append(f"report keys drifted: {sorted(set(report) ^ expected_report_keys)}")
        # The engine's report must still carry the four-dimension view.
        if [d["code"] for d in report["dimensions"]] != ["T", "R", "U", "S"]:
            failures.append("report dimensions are not the four T/R/U/S codes")

        trace_resp = client.get(f"/api/engine-trace/{task_id}")
        print(f"\n[5/5] GET  /api/engine-trace -> {trace_resp.status_code}")
        if trace_resp.status_code != 200:
            print(f"      FAILED: {trace_resp.text}")
            return 1
        trace = trace_resp.json()

        print(f"\n  引擎 schema: {trace['engine_schema_version']}  backend: {trace['backend']}")
        print(f"  引擎聚合分: {trace['overall_score']} ({trace['overall_risk_level']})  latency={trace['latency_ms']}ms")
        print(f"  证据链: {trace['evidence_count']} 条   provider_errors: {trace['provider_errors']}")

        print(f"\n  四角色执行状态（visible_roles）:")
        for role in trace["visible_roles"]:
            print(f"    - {role['role']:<8} {role['provider']:<12} {role['status']:<16} prompt={role['prompt_version']} err={role['error_code']}")

        print(f"\n  维度评估（dimension_assessments）:")
        for item in trace["dimension_assessments"]:
            print(f"    - {item['dimension']:<8} {item['status']:<16} score={item['score']} level={item['risk_level']} weight={item['normalized_weight']}")

        print(f"\n  前 5 条证据:")
        for item in trace["evidence_chain"][:5]:
            excerpt = (item.get("excerpt") or "")[:52]
            print(f"    [{item['dimension']}/{item['source_kind']}] {excerpt}")

        print(f"\n  引擎局限（limitations）:")
        for note in trace["limitations"][:4]:
            print(f"    - {note}")

        # report summary must be the engine's own (it carries the
        # "not a probability" caveat), not the heuristic fuse summary.
        if trace["overall_score"] is not None and report["overall_score"] != trace["overall_score"]:
            failures.append(
                f"report score {report['overall_score']} != engine score {trace['overall_score']}"
            )
        if trace["overall_score"] is None:
            failures.append("engine produced no overall score")

        if not trace["visible_roles"]:
            failures.append("visible_roles is empty")
        if not trace["evidence_chain"]:
            failures.append("evidence_chain is empty")
        if not trace["limitations"]:
            failures.append("limitations is empty")

        print("\n" + _rule())
        if failures:
            print("结果: FAILED")
            for failure in failures:
                print(f"  - {failure}")
            return 1
        print("结果: PASSED — 引擎接管分析维度，报告契约未变，轨迹可查")
        print(_rule())
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
