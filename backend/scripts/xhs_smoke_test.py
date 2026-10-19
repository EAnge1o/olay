"""Live crawl smoke test for the Xiaohongshu link pipeline.

    python scripts/xhs_smoke_test.py "<note url or share text>"

Runs the *real* browser crawl through :class:`XhsService` (so it exercises URL
normalisation, the subprocess runner, Chrome, and the JSON contract), then
optionally pushes the result through the whole analysis pipeline.

    python scripts/xhs_smoke_test.py "<url>" --analyze

Exits 2 when crawling is not configured (so CI without Chrome skips it), 1 on a
crawl failure, 0 on success. The failure message is the actionable one the API
would return — usually "log in once in the persistent profile and retry".
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.services.xhs_service import XhsError, XhsService  # noqa: E402


def _rule(char: str = "=", width: int = 78) -> str:
    return char * width


def _crawl(url: str, account_url: str | None = None):
    settings = get_settings()
    service = XhsService.from_settings(settings)
    if not service.available:
        print(f"SKIPPED: {service.unavailable_reason()}")
        raise SystemExit(2)

    print(_rule())
    print("TruLens — 小红书链接抓取 Smoke Test")
    print(_rule())
    print(f"  链接       : {url}")
    print(f"  持久 profile: {settings.xhs_user_data_path_absolute}")
    print(f"  超时       : {settings.xhs_timeout_seconds}s")

    try:
        return asyncio.run(service.fetch(url, account_url=account_url))
    except XhsError as exc:
        print("\n结果: FAILED — 抓取未产出内容")
        print(f"  原因: {exc.reason}")
        if exc.detail:
            print(f"  诊断: {exc.detail}")
        raise SystemExit(1) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Crawl one Xiaohongshu note.")
    parser.add_argument("url", help="note URL or the raw share text")
    parser.add_argument("--account-url", help="optional Xiaohongshu author homepage URL")
    parser.add_argument("--analyze", action="store_true", help="also run the four-dimension pipeline")
    args = parser.parse_args()

    note = _crawl(args.url, args.account_url)

    print("\n抓取成功:")
    print(f"  标题      : {note.title or '（无）'}")
    print(f"  正文      : {len(note.content)} 字")
    print(f"  图片      : {note.image_count} 张 -> R 维度只用第 1 张（封面）")
    print(f"  评论      : {len(note.comments)} 条（最佳努力抽取）")
    print(f"  作者      : {note.author_handle or '（未取到）'}")
    if note.account:
        print(f"  作者主页  : {note.account.profile_url}")
        print(f"  账号历史  : {len(note.account.posts)} 篇（主页快照）")
        print(f"  粉丝/笔记 : {note.account.follower_count or '—'} / {note.account.total_post_count or '—'}")
    if note.warning:
        print(f"  警告      : {note.warning}")
    if note.comments[:3]:
        print("  评论样例  :")
        for item in note.comments[:3]:
            print(f"    - [{item.get('author') or '匿名'}] {item['content'][:48]}")
    print("\n  R 维度可用 :", "是" if note.image_paths else "否（无图 -> 暂不评分）")
    print("  U 维度可用 :", "是" if note.comments else "否（无评论 -> 暂不评分）")
    print(
        "  T 维度可用 :",
        "是" if note.account and len(note.account.posts) >= 3
        else "否（主页历史不足 3 篇或主页未取到）",
    )

    if not args.analyze:
        print("\n" + _rule())
        print("结果: PASSED — 链接可抓取，可进入四维分析")
        print(_rule())
        return 0

    # Full pipeline against an isolated DB, so this never touches dev data.
    import os
    import tempfile

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="xhs-analyze-"))
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(tmp / 'xhs.db').as_posix()}"
    os.environ["STORAGE_DIR"] = str(tmp / "uploads")

    from fastapi.testclient import TestClient  # noqa: PLC0415

    from app.main import app  # noqa: PLC0415

    with TestClient(app) as client:
        form = {"url": args.url, "source": "xhs_smoke"}
        if args.account_url:
            form["account_url"] = args.account_url
        upload = client.post("/api/upload", data=form)
        print(f"\nPOST /api/upload -> {upload.status_code}")
        if upload.status_code != 201:
            print(f"  FAILED: {upload.text}")
            return 1
        body = upload.json()
        print(f"  task_id={body['task_id']}  source_url={body['source_url']}")

        status = client.get(f"/api/status/{body['task_id']}").json()
        print(f"GET  /api/status -> {status['status']} ({status['progress']}%)")
        if status["status"] == "failed":
            print(f"  FAILED: {status['error_message']}")
            return 1

        report = client.get(f"/api/report/{body['task_id']}")
        print(f"GET  /api/report -> {report.status_code}")
        if report.status_code != 200:
            print(f"  FAILED: {report.text}")
            return 1
        report = report.json()
        print(f"  trust_score={report['trust_score']}  {report['trust_label']}")
        for dim in report["dimensions"]:
            score = f"{dim['trust_score']}/100" if dim["trust_score"] is not None else "暂不评分"
            print(f"    ({dim['code']}) {dim['label']:<12} {score}")
        print(f"  assessed={report['assessed_dimensions']}  evidence={len(report['evidence'])}")

    print("\n" + _rule())
    print("结果: PASSED — 链接 -> 抓取 -> 四维报告 全链路可运行")
    print(_rule())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
