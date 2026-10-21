#!/usr/bin/env python3
"""Black-box evaluation for the current TruLens FastAPI service.

This script intentionally uses only the existing HTTP API. It does not import or
modify backend/engine internals, prompts, thresholds, or scoring code.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "competition-submission" / "TruLens_TestDataset"
DEFAULT_OUT = ROOT / "eval"
API_BASE = "http://127.0.0.1:8000/api"

DIMENSION_ORDER = ("T", "R", "U", "S")
RISK_LABEL_ZH = {"high": "高风险", "medium": "中风险", "low": "低风险"}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig").strip()


def natural_image_key(path: Path) -> tuple[int, str]:
    match = re.search(r"image_(\d+)", path.stem)
    return (int(match.group(1)) if match else 10**9, path.name)


def parse_key_value_line(line: str) -> dict[str, str]:
    match = re.match(r"^\[(\d+)\]\s*(.*)$", line.strip())
    if not match:
        return {}
    index, rest = match.groups()
    result = {"index": index}
    for part in re.split(r"\s*\|\s*", rest):
        if "=" not in part:
            result.setdefault("内容", part.strip())
            continue
        key, value = part.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", value):
        return value
    if re.match(r"^\d{2}-\d{2}", value):
        return f"2026-{value}"
    return value


def parse_comments(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    comments: list[dict[str, Any]] = []
    for line in read_text(path).splitlines():
        item = parse_key_value_line(line)
        content = (
            item.get("内容")
            or item.get("评价")
            or item.get("评论")
            or ""
        ).strip()
        if not content:
            continue
        comments.append(
            {
                "content": content,
                "author": item.get("昵称") or item.get("用户"),
                "created_at": normalize_date(item.get("时间")),
                "likes": int(item["点赞"]) if str(item.get("点赞", "")).isdigit() else 0,
                "source_file": path.name,
                "source_index": item.get("index"),
            }
        )
    return comments


def parse_account(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, None
    raw = read_text(path)
    handle_match = re.search(r"昵称[：:]\s*([^\n]+)", raw)
    profile_match = re.search(r"简介[：:]\s*([^\n]+)", raw)
    follower_match = re.search(r"粉丝[：:]\s*([\d,]+)", raw)
    total_match = re.search(r"近\s*30\s*天笔记[（(]?共?\s*(\d+)\s*篇", raw)

    posts: list[dict[str, Any]] = []
    for line in raw.splitlines():
        match = re.match(
            r"^\[(\d+)\]\s+(\d{2}-\d{2})\s+标题=(.*?)\s*\|\s*自述肤质=(.*)$",
            line.strip(),
        )
        if not match:
            continue
        index, mmdd, title, skin = match.groups()
        posts.append(
            {
                "id": f"account-post-{int(index):02d}",
                "text": f"标题：{title.strip()}；自述肤质：{skin.strip()}",
                "published_at": f"2026-{mmdd}T00:00:00Z",
            }
        )

    if not (handle_match or profile_match or posts or total_match):
        return None, raw

    account = {
        "handle": handle_match.group(1).strip() if handle_match else None,
        "profile_text": profile_match.group(1).strip() if profile_match else raw,
        "follower_count": (
            int(follower_match.group(1).replace(",", "")) if follower_match else 0
        ),
        "total_post_count": int(total_match.group(1)) if total_match else len(posts),
        "posts": posts,
    }
    return account, raw


def load_manifest(dataset: Path) -> dict[str, dict[str, str]]:
    with (dataset / "manifest.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        return {row["sample_id"]: row for row in csv.DictReader(handle)}


def load_sample(dataset: Path, sample_id: str, manifest_row: dict[str, str]) -> dict[str, Any]:
    sample_dir = dataset / sample_id
    post_dir = sample_dir / "post"
    content = read_text(post_dir / "content.txt")
    complaint = read_text(sample_dir / "complaint.txt") if (sample_dir / "complaint.txt").exists() else ""

    text = content
    if complaint:
        text += "\n\n【客服投诉材料】\n" + complaint

    images = sorted(
        [p for p in post_dir.glob("image_*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"}],
        key=natural_image_key,
    )
    comment_images = sorted(
        [
            p
            for p in (sample_dir / "comment_images").glob("*")
            if p.suffix.lower() in {".jpg", ".jpeg", ".png"}
        ]
        if (sample_dir / "comment_images").exists()
        else []
    )

    comments: list[dict[str, Any]] = []
    for filename in ("comments.txt", "reviews.txt"):
        comments.extend(parse_comments(sample_dir / filename))

    account, account_raw = parse_account(sample_dir / "account.txt")
    labels_path = sample_dir / "labels.json"
    labels = json.loads(read_text(labels_path)) if labels_path.exists() else []

    return {
        "sample_id": sample_id,
        "manifest": manifest_row,
        "content_text": content,
        "submitted_text": text,
        "images_available": [str(p.relative_to(sample_dir)) for p in images],
        "images_submitted": [str(images[0].relative_to(sample_dir))] if images else [],
        "comment_images_available": [
            str(p.relative_to(sample_dir)) for p in comment_images
        ],
        "comment_images_submitted": [],
        "comments": comments,
        "account": account,
        "account_raw": account_raw,
        "labels": labels,
        "files": {
            "comments": (sample_dir / "comments.txt").exists(),
            "reviews": (sample_dir / "reviews.txt").exists(),
            "complaint": (sample_dir / "complaint.txt").exists(),
            "account": (sample_dir / "account.txt").exists(),
        },
        "image_path": images[0] if images else None,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def json_or_error(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return {"status_code": response.status_code, "text": response.text}


def upload_sample(client: httpx.Client, sample: dict[str, Any], api_base: str) -> dict[str, Any]:
    data = {
        "text": sample["submitted_text"],
        "comments": json.dumps(sample["comments"], ensure_ascii=False),
        "source": "eval",
        "agents": json.dumps(["account", "image", "comment", "text"]),
        "use_llm": "false",
        "auto_analyze": "true",
    }
    if sample["account"] is not None:
        data["account_history"] = json.dumps(sample["account"], ensure_ascii=False)

    files = None
    image_path: Path | None = sample["image_path"]
    if image_path is not None:
        mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        files = {"image": (image_path.name, image_path.read_bytes(), mime)}

    response = client.post(f"{api_base}/upload", data=data, files=files)
    return {
        "request": {
            "text_chars": len(sample["submitted_text"]),
            "comments_count": len(sample["comments"]),
            "account_posts": len((sample["account"] or {}).get("posts", [])),
            "image_submitted": sample["images_submitted"],
            "images_available": sample["images_available"],
            "comment_images_available": sample["comment_images_available"],
            "comment_images_submitted": [],
        },
        "http_status": response.status_code,
        "response": json_or_error(response),
    }


def poll_task(client: httpx.Client, task_id: str, api_base: str, timeout_s: int = 420) -> dict[str, Any]:
    started = time.perf_counter()
    snapshots: list[dict[str, Any]] = []
    while time.perf_counter() - started < timeout_s:
        response = client.get(f"{api_base}/status/{task_id}")
        status = json_or_error(response)
        snapshots.append(
            {
                "at_s": round(time.perf_counter() - started, 3),
                "http_status": response.status_code,
                "status": status,
            }
        )
        state = status.get("status") if isinstance(status, dict) else None
        if state in {"completed", "failed"}:
            return {
                "final": status,
                "snapshots": snapshots,
                "elapsed_s": round(time.perf_counter() - started, 3),
            }
        time.sleep(1.5)
    return {
        "final": {"status": "timeout", "error_message": f"poll exceeded {timeout_s}s"},
        "snapshots": snapshots,
        "elapsed_s": round(time.perf_counter() - started, 3),
    }


def fetch_report_and_trace(
    client: httpx.Client, task_id: str, api_base: str
) -> tuple[Any, Any]:
    report_response = client.get(f"{api_base}/report/{task_id}")
    trace_response = client.get(f"{api_base}/engine-trace/{task_id}")
    return (
        {
            "http_status": report_response.status_code,
            "response": json_or_error(report_response),
        },
        {
            "http_status": trace_response.status_code,
            "response": json_or_error(trace_response),
        },
    )


def run_one(
    client: httpx.Client,
    sample: dict[str, Any],
    api_base: str,
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "sample_id": sample["sample_id"],
        "manifest": sample["manifest"],
        "input_mapping": {
            **sample["files"],
            "content_text": sample["content_text"],
            "submitted_text": sample["submitted_text"],
            "images_available": sample["images_available"],
            "images_submitted": sample["images_submitted"],
            "comment_images_available": sample["comment_images_available"],
            "comment_images_submitted": sample["comment_images_submitted"],
            "comments_submitted": sample["comments"],
            "account_submitted": sample["account"],
            "labels": sample["labels"],
        },
        "attempts": [],
        "success": False,
        "failure_reason": None,
    }

    started = time.perf_counter()
    for attempt_no in range(1, 3):
        attempt: dict[str, Any] = {"attempt": attempt_no}
        try:
            uploaded = upload_sample(client, sample, api_base)
            attempt["upload"] = uploaded
            task_id = uploaded["response"].get("task_id")
            if uploaded["http_status"] not in range(200, 300) or not task_id:
                raise RuntimeError(f"upload failed: {json.dumps(uploaded['response'], ensure_ascii=False)[:500]}")

            polled = poll_task(client, task_id, api_base)
            attempt["poll"] = polled
            if polled["final"].get("status") != "completed":
                raise RuntimeError(
                    f"task status={polled['final'].get('status')} "
                    f"error={polled['final'].get('error_message')}"
                )

            report, trace = fetch_report_and_trace(client, task_id, api_base)
            attempt["report"] = report
            attempt["engine_trace"] = trace
            if report["http_status"] not in range(200, 300):
                raise RuntimeError("report fetch failed")

            sample_response = client.get(
                f"{api_base}/samples/{uploaded['response']['sample_id']}"
            )
            attempt["sample_api"] = {
                "http_status": sample_response.status_code,
                "response": json_or_error(sample_response),
            }
            raw["task_id"] = task_id
            raw["sample_id_api"] = uploaded["response"]["sample_id"]
            raw["report"] = report["response"]
            raw["engine_trace"] = trace["response"]
            raw["success"] = True
            raw["failure_reason"] = None
            raw["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            raw["attempts"].append(attempt)
            return raw
        except Exception as exc:  # noqa: BLE001 - preserve exact failure
            attempt["error"] = f"{type(exc).__name__}: {exc}"
            raw["attempts"].append(attempt)
            raw["failure_reason"] = attempt["error"]
            if attempt_no < 2:
                time.sleep(3)

    raw["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return raw


def extract_dimension_scores(report: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for dimension in report.get("dimensions", []) or []:
        code = dimension.get("code")
        if code:
            result[code] = {
                "trust_score": dimension.get("trust_score"),
                "risk_score": dimension.get("risk_score"),
                "risk_level": dimension.get("risk_level"),
                "assessed": dimension.get("assessed"),
            }
    return result


def cross_check_counts(report: dict[str, Any]) -> dict[str, int]:
    counts = {key: 0 for key in ("conflict", "corroborated", "doubtful", "consistent", "unverified")}
    for item in report.get("cross_checks", []) or []:
        state = item.get("state")
        if state in counts:
            counts[state] += 1
    return counts


def expected_fake(manifest_row: dict[str, str]) -> bool:
    return str(manifest_row.get("是否伪造", "")).strip().startswith("是")


def result_row(raw: dict[str, Any]) -> dict[str, Any]:
    manifest = raw["manifest"]
    fake = expected_fake(manifest)
    report = raw.get("report") if raw.get("success") else None
    risk = report.get("risk_level") if isinstance(report, dict) else None
    dims = extract_dimension_scores(report) if isinstance(report, dict) else {}
    counts = cross_check_counts(report) if isinstance(report, dict) else {}
    correct = None
    if not raw.get("success"):
        correct = False
    elif fake:
        correct = risk in {"medium", "high"}
    else:
        correct = risk == "low"

    return {
        "sample_id": raw["sample_id"],
        "scenario": manifest.get("场景"),
        "is_fake": "是" if fake else "否",
        "difficulty": manifest.get("难度"),
        "expected_risk": manifest.get("风险等级"),
        "failed": "否" if raw.get("success") else "是",
        "failure_reason": raw.get("failure_reason") or "",
        "trust_score": report.get("trust_score") if isinstance(report, dict) else None,
        "risk_score": report.get("overall_score") if isinstance(report, dict) else None,
        "risk_level": risk,
        "risk_level_zh": RISK_LABEL_ZH.get(risk, risk),
        "T_trust_score": dims.get("T", {}).get("trust_score"),
        "R_trust_score": dims.get("R", {}).get("trust_score"),
        "U_trust_score": dims.get("U", {}).get("trust_score"),
        "S_trust_score": dims.get("S", {}).get("trust_score"),
        "conflict_count": counts.get("conflict", 0),
        "corroborated_count": counts.get("corroborated", 0),
        "doubtful_count": counts.get("doubtful", 0),
        "consistent_count": counts.get("consistent", 0),
        "unverified_count": counts.get("unverified", 0),
        "prediction_correct": "是" if correct else "否" if correct is not None else "",
        "elapsed_seconds": raw.get("elapsed_seconds"),
        "task_id": raw.get("task_id", ""),
        "images_available": len(raw["input_mapping"].get("images_available", [])),
        "images_submitted": len(raw["input_mapping"].get("images_submitted", [])),
        "comment_images_available": len(raw["input_mapping"].get("comment_images_available", [])),
        "comment_images_submitted": len(raw["input_mapping"].get("comment_images_submitted", [])),
        "comments_submitted": len(raw["input_mapping"].get("comments_submitted", [])),
        "account_posts_submitted": len((raw["input_mapping"].get("account_submitted") or {}).get("posts", [])),
    }


def write_results(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def report_search_text(raw: dict[str, Any]) -> str:
    parts: list[str] = []
    report = raw.get("report") or {}
    trace = raw.get("engine_trace") or {}
    parts.append(flatten_text(report.get("summary")))
    for item in report.get("evidence", []) or []:
        parts.append(flatten_text(item))
    for dimension in report.get("dimensions", []) or []:
        parts.append(flatten_text(dimension.get("summary")))
        parts.append(flatten_text(dimension.get("evidence")))
        parts.append(flatten_text(dimension.get("limitations")))
    for item in report.get("cross_checks", []) or []:
        parts.append(flatten_text(item))
    parts.append(flatten_text(report.get("decision")))
    parts.append(flatten_text(trace.get("major_evidence")))
    parts.append(flatten_text(trace.get("evidence_chain")))
    parts.append(flatten_text(trace.get("limitations")))
    parts.append(flatten_text(trace.get("visible_roles")))
    return "\n".join(part for part in parts if part)


KEYWORD_GROUPS = {
    "absolute": ["绝对", "100%", "百分百", "零风险", "无刺激", "孕妇", "敏感肌", "安全"],
    "unsupported": ["无出处", "缺少", "依据", "来源", "凭证", "检测", "数据", "claim", "声明"],
    "ai_text": ["模板", "结构化", "AI 文风", "生成", "重复", "段长", "口语"],
    "image": ["图片", "图像", "配图", "纹理", "光影", "修图", "调色", "提亮", "磨皮", "锐化", "拼接", "字体", "同源", "镜像"],
    "comment": ["评论", "刷评", "水军", "重复", "模板", "话术", "集中", "同质化"],
    "account": ["账号", "主页", "历史笔记", "发布节奏", "空瓶", "肤质", "合作"],
    "time": ["时间", "倒置", "早于", "晚于", "日期", "时间线", "订单", "工号", "批号"],
    "disclosure": ["软广", "广告", "未披露", "合作", "口令", "专属", "导购", "品牌"],
    "chart": ["图表", "坐标", "柱", "比例", "百分比", "纵轴"],
    "report": ["报告", "检测机构", "印章", "资质", "备案号", "特字"],
}


def keyword_group_for_label(label: dict[str, Any]) -> list[str]:
    text = f"{label.get('scene', '')} {label.get('signal', '')} {label.get('expected', '')}"
    groups: list[str] = []
    for group, words in KEYWORD_GROUPS.items():
        if any(word.lower() in text.lower() for word in words):
            groups.append(group)
    return groups


def auto_label_hits(raw: dict[str, Any]) -> list[dict[str, Any]]:
    searchable = report_search_text(raw)
    normalized = searchable.lower()
    rows: list[dict[str, Any]] = []
    for label in raw["input_mapping"].get("labels", []):
        groups = keyword_group_for_label(label)
        signal_text = f"{label.get('signal', '')} {label.get('expected', '')}"
        quoted = re.findall(r"[「『“\"]([^」』”\"]{2,30})[」』”\"]", signal_text)
        numbers = re.findall(r"\d+(?:\.\d+)?%?", signal_text)
        matches = 0
        matched_terms: list[str] = []
        for group in groups:
            for word in KEYWORD_GROUPS[group]:
                if word.lower() in normalized:
                    matches += 1
                    matched_terms.append(word)
        for token in quoted + numbers:
            if token and token.lower() in normalized:
                matches += 1
                matched_terms.append(token)
        hit = 1 if matches >= 2 else 0
        evidence = ""
        if matched_terms:
            for needle in dict.fromkeys(matched_terms):
                index = normalized.find(needle.lower())
                if index >= 0:
                    evidence = searchable[max(0, index - 80): index + 180].replace("\n", " ")
                    break
        rows.append(
            {
                "sample_id": raw["sample_id"],
                "label_id": label.get("id"),
                "hit": hit,
                "agent_evidence": evidence,
                "judge": "auto",
                "scene": label.get("scene", ""),
                "file": label.get("file", ""),
                "target": label.get("target", ""),
                "signal": label.get("signal", ""),
                "expected": label.get("expected", ""),
                "auto_matches": matches,
            }
        )
    return rows


def write_label_hits(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def read_label_hits(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def group_detection(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    fake_rows = [row for row in rows if row["is_fake"] == "是"]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in fake_rows:
        grouped[str(row.get(key) or "未标注")].append(row)
    output = []
    for label, items in sorted(grouped.items()):
        detected = sum(1 for item in items if item["prediction_correct"] == "是")
        output.append({"group": label, "detected": detected, "total": len(items)})
    return output


def build_summary(
    *,
    eval_dir: Path,
    rows: list[dict[str, Any]],
    label_hits: list[dict[str, str]] | None,
    health: dict[str, Any],
    started_at: str,
    finished_at: str,
) -> str:
    fake = [row for row in rows if row["is_fake"] == "是"]
    controls = [row for row in rows if row["is_fake"] == "否"]
    detected = sum(1 for row in fake if row["prediction_correct"] == "是")
    false_positives = sum(1 for row in controls if row["prediction_correct"] == "否")
    elapsed = [float(row["elapsed_seconds"]) for row in rows if row.get("elapsed_seconds") is not None]
    avg_elapsed = statistics.mean(elapsed) if elapsed else 0

    lines = [
        "# TruLens Agent 测试数据包评估汇总",
        "",
        "## 运行环境",
        "",
        f"- 运行日期：{started_at} 至 {finished_at}",
        f"- API：`{API_BASE}`",
        f"- 分析后端：`{health.get('analysis_backend')}`",
        f"- LLM provider：`{health.get('llm_provider')}` / enabled=`{health.get('llm_enabled')}`",
        f"- 数据库：`{health.get('database')}`",
        f"- 链接抓取：`{health.get('xhs_enabled')}`",
        f"- 运行脚本：`eval/run_eval.py`",
        "- 输入方式：`POST /api/upload`，后台任务完成后读取 `/api/status`、`/api/report`、`/api/engine-trace`。",
        "- 当前接口为单图入口；每个样本仅提交编号最小的正文图，其余正文图和 `comment_images` 记录为未提交材料。",
        "",
        "## 第一档汇总",
        "",
        f"- 样本总数：{len(rows)}",
        f"- 伪造样本检出：{detected}/{len(fake)}",
        f"- 对照样本误报：{false_positives}/{len(controls)}",
        f"- 失败样本数：{sum(1 for row in rows if row['failed'] == '是')}",
        f"- 平均耗时（全部样本，含失败）：{avg_elapsed:.2f} 秒",
        "",
        "## 按场景（伪造样本）",
        "",
        "| 场景 | 检出 | 合计 |",
        "|---|---:|---:|",
    ]
    for item in group_detection(rows, "scenario"):
        lines.append(f"| {item['group']} | {item['detected']} | {item['total']} |")

    lines += ["", "## 按难度（伪造样本）", "", "| 难度 | 检出 | 合计 |", "|---|---:|---:|"]
    for item in group_detection(rows, "difficulty"):
        lines.append(f"| {item['group']} | {item['detected']} | {item['total']} |")

    lines += ["", "## 第二档汇总", ""]
    recall_text = "未统计"
    if label_hits is not None:
        total_labels = len(label_hits)
        hit_count = sum(1 for item in label_hits if str(item.get("hit")) == "1")
        recall_text = f"{hit_count}/{total_labels}"
        lines.append(f"- 疑点级命中：{hit_count}/{total_labels}")
        lines.append(f"- 疑点召回率：{hit_count / total_labels * 100:.2f}%" if total_labels else "- 疑点召回率：0%")
        lines.append("- `judge=manual` 行表示人工确认；`judge=auto` 行保留自动预判。")
    else:
        lines.append("- 未生成第二档统计。")

    lines += ["", "## 失败样本", ""]
    failed = [row for row in rows if row["failed"] == "是"]
    if failed:
        for row in failed:
            lines.append(f"- `{row['sample_id']}`：{row['failure_reason']}")
    else:
        lines.append("- 无。")

    lines += [
        "",
        "## 可直接写进 PPT 的结论",
        "",
        (
            f"在 {len(rows)} 条自建样本上，伪造样本检出 {detected}/{len(fake)}，"
            f"对照样本误报 {false_positives}/{len(controls)}；疑点级召回 {recall_text}"
            + ("。" if label_hits is not None else "（未统计）。")
        ),
        "",
        "## 结果文件",
        "",
        "- `eval/results.csv`",
        "- `eval/raw/sample_0XX.json`",
        "- `eval/label_hits.csv`",
        "",
    ]
    summary = "\n".join(lines)
    (eval_dir / "summary.md").write_text(summary, encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--api", default=API_BASE)
    parser.add_argument("--sample", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    raw_dir = args.out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=httpx.Timeout(connect=15.0, read=60.0, write=60.0, pool=15.0)) as client:
        health = json_or_error(client.get(f"{args.api}/health"))

        if args.summarize_only:
            raws = []
            for path in sorted(raw_dir.glob("sample_*.json")):
                raws.append(json.loads(path.read_text(encoding="utf-8")))
        else:
            manifest = load_manifest(args.dataset)
            sample_ids = sorted(manifest)
            if args.sample:
                wanted = set(args.sample)
                sample_ids = [sid for sid in sample_ids if sid in wanted]
            if args.limit:
                sample_ids = sample_ids[: args.limit]

            raws = []
            started_at = datetime.now(timezone.utc).isoformat()
            for index, sample_id in enumerate(sample_ids, start=1):
                raw_path = raw_dir / f"{sample_id}.json"
                if args.resume and raw_path.exists():
                    existing = json.loads(raw_path.read_text(encoding="utf-8"))
                    if existing.get("success"):
                        print(f"[{index}/{len(sample_ids)}] {sample_id}: resume existing success")
                        raws.append(existing)
                        continue
                print(f"[{index}/{len(sample_ids)}] {sample_id}: evaluating")
                sample = load_sample(args.dataset, sample_id, manifest[sample_id])
                raw = run_one(client, sample, args.api)
                write_json(raw_path, raw)
                raws.append(raw)
                print(
                    f"[{index}/{len(sample_ids)}] {sample_id}: "
                    f"{'OK' if raw['success'] else 'FAILED'} "
                    f"{raw.get('report', {}).get('risk_level', '')} "
                    f"{raw.get('elapsed_seconds')}s"
                )
            finished_at = datetime.now(timezone.utc).isoformat()

        rows = [result_row(raw) for raw in raws]
        write_results(args.out / "results.csv", rows)

        hit_rows: list[dict[str, Any]] = []
        for raw in raws:
            if raw.get("success"):
                hit_rows.extend(auto_label_hits(raw))
        write_label_hits(args.out / "label_hits.csv", hit_rows)

        label_hits = read_label_hits(args.out / "label_hits.csv") if (args.out / "label_hits.csv").exists() else None
        summary = build_summary(
            eval_dir=args.out,
            rows=rows,
            label_hits=label_hits,
            health=health,
            started_at=locals().get("started_at", "existing raw files"),
            finished_at=locals().get("finished_at", datetime.now(timezone.utc).isoformat()),
        )
        if args.summarize_only:
            print(summary)
        else:
            preview = summary.split("## 失败样本", 1)[0]
            print("\n" + preview)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
