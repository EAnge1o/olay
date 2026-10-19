"""Run the current Content Trust Engine on the ten human-labeled test posts.

Human labels and annotation notes are loaded only for the final evaluation and
are never included in the model request.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from content_trust import BatchTrustInput, CommentInput, ContentTrustEngine, TrustImageInput
from content_trust.config import Settings
from content_trust.nmpa import NMPARegistry


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = (
    ROOT
    / "data/model-experiments/trufor_detectivesam_test_package_3_20261015"
    / "dataset/测试数据包"
)
DEFAULT_OUTPUT = ROOT / "data/evaluations/labeled_test_package_current_20261017"
COMMENT_ROW = re.compile(r"^\[(\d+)\]\s*(.*)$")
LABEL_ROW = re.compile(r"^(昵称|时间|地区|是否作者|回复对象|内容)=(.*)$")


def _print_progress(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _parse_comments(path: Path) -> tuple[list[CommentInput], int, list[str]]:
    comments: list[CommentInput] = []
    omitted_empty = 0
    warnings: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        row = COMMENT_ROW.match(line)
        if not row:
            continue
        fields: dict[str, str] = {}
        for item in row.group(2).split(" | "):
            match = LABEL_ROW.match(item.strip())
            if match:
                fields[match.group(1)] = match.group(2).strip()
        required = {"昵称", "时间", "地区", "是否作者", "回复对象", "内容"}
        if required - fields.keys():
            warnings.append(f"line {line_number}: malformed comment row")
            continue
        text = fields["内容"].strip()
        if not text:
            omitted_empty += 1
            continue
        comments.append(
            CommentInput(
                id=f"comment-{int(row.group(1)):03d}",
                text=text,
                author=None if fields["昵称"] in {"", "-", "未识别"} else fields["昵称"],
                published_at=None if fields["时间"] in {"", "-"} else fields["时间"],
                region=None if fields["地区"] in {"", "-"} else fields["地区"],
            )
        )
    return comments, omitted_empty, warnings


def _human_annotation(meta_path: Path) -> dict[str, Any]:
    text = meta_path.read_text(encoding="utf-8")

    def value(name: str) -> str | None:
        match = re.search(rf"^{re.escape(name)}:\s*(.*?)\s*$", text, re.MULTILINE)
        return match.group(1) if match else None

    label_text = value("is_fake")
    if label_text not in {"是", "否", "true", "false"}:
        raise ValueError(f"Missing or invalid is_fake label in {meta_path}")
    ground_truth_match = re.search(
        r"^ground_truth:\s*(.*?)\s*$", text, re.MULTILINE
    )
    return {
        "is_fake": label_text in {"是", "true"},
        "fake_type": value("fake_type"),
        "fake_method": value("fake_method"),
        "ground_truth": ground_truth_match.group(1) if ground_truth_match else None,
        "risk_level": value("risk_level"),
    }


def _format_comment_scope(total_rows: int, omitted_empty: int) -> str:
    return (
        f"导入测试包 comments.txt 中 {total_rows} 条转录评论；"
        f"其中 {omitted_empty} 条内容为空，未发送分析。评论为截图可见样本，"
        "不代表帖子完整评论区；昵称、人工真伪标签及标注依据不用于评论模式判断。"
    )


def _percent(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _evaluation_rows(
    results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    tp = fp = tn = fn = 0
    finding_categories: Counter[str] = Counter()
    provider_errors: Counter[str] = Counter()
    priorities: Counter[str] = Counter()
    for result in results:
        human_fake = bool(result["human_annotation"]["is_fake"])
        priority = result["review_priority"]
        flagged = priority in {"review", "high"}
        priorities[priority] += 1
        if human_fake and flagged:
            tp += 1
        elif human_fake:
            fn += 1
        elif flagged:
            fp += 1
        else:
            tn += 1
        for category in result["finding_categories"]:
            finding_categories[category] += 1
        for error in result["provider_errors"]:
            provider_errors[error] += 1
        rows.append(
            {
                "sample_id": result["sample_id"],
                "human_label": "fake" if human_fake else "real",
                "ground_truth": result["human_annotation"].get("ground_truth"),
                "risk_level": result["human_annotation"].get("risk_level"),
                "review_priority": priority,
                "triage_flagged": flagged,
                "image_count": result["image_count"],
                "comment_count": result["comment_count"],
                "finding_count": result["finding_count"],
                "finding_categories": result["finding_categories"],
                "comment_suspicious_pattern_count": result[
                    "comment_suspicious_pattern_count"
                ],
                "provider_errors": result["provider_errors"],
                "latency_ms": result["latency_ms"],
                "report_path": result["report_path"],
            }
        )

    total = tp + fp + tn + fn
    metrics = {
        "positive_prediction_rule": "review_priority in {'review', 'high'}",
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "accuracy": _percent(tp + tn, total),
        "precision": _percent(tp, tp + fp),
        "recall": _percent(tp, tp + fn),
        "specificity": _percent(tn, tn + fp),
        "f1": _percent(2 * tp, 2 * tp + fp + fn),
        "review_priority_counts": dict(priorities),
        "finding_categories": dict(finding_categories),
        "provider_error_counts": dict(provider_errors),
        "interpretation": (
            "Exploratory post-level triage comparison on 10 manually labeled items. "
            "The labels cover different kinds of misleading/AI-generated content, "
            "not image-level forensic ground truth; these metrics are not a calibrated "
            "production accuracy estimate."
        ),
    }
    return rows, metrics


def _write_markdown(path: Path, results: list[dict[str, Any]], metrics: dict[str, Any]) -> None:
    def pct(value: float | None) -> str:
        return "N/A" if value is None else f"{value * 100:.1f}%"

    lines = [
        "# 人工标注测试数据包：当前主流程评测",
        "",
        "## 评测口径",
        "",
        "人工 `is_fake` 标签仅用于模型运行后的对照，未进入 DeepSeek、Qwen 或评论分析输入。",
        "预测为风险项的规则是 `review_priority` 为 `review` 或 `high`。这只是人工分流代理指标，",
        "不表示模型判断内容一定为假。样本为 10 篇帖子，标签为帖子级；不具备逐图 AI/篡改像素级真值。",
        "",
        "## 汇总",
        "",
        f"- TP={metrics['tp']}，FP={metrics['fp']}，TN={metrics['tn']}，FN={metrics['fn']}",
        f"- Accuracy={pct(metrics['accuracy'])}，Precision={pct(metrics['precision'])}，"
        f"Recall={pct(metrics['recall'])}，Specificity={pct(metrics['specificity'])}，F1={pct(metrics['f1'])}",
        f"- Review priority：`{json.dumps(metrics['review_priority_counts'], ensure_ascii=False)}`",
        f"- Findings 类别计数：`{json.dumps(metrics['finding_categories'], ensure_ascii=False)}`",
        f"- Provider 错误计数：`{json.dumps(metrics['provider_error_counts'], ensure_ascii=False)}`",
        "",
        "## 逐帖结果",
        "",
        "| 样本 | 人工标签 | 引擎分流 | 标记风险 | 图数 | 评论数 | Findings | 评论模式簇 | Provider 错误 |",
        "|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in results:
        errors = ", ".join(row["provider_errors"]) or "—"
        categories = ", ".join(row["finding_categories"]) or "—"
        lines.append(
            f"| {row['sample_id']} | {'伪造' if row['human_annotation']['is_fake'] else '真实'} "
            f"| {row['review_priority']} | {'是' if row['triage_flagged'] else '否'} "
            f"| {row['image_count']} | {row['comment_count']} | {categories} "
            f"| {row['comment_suspicious_pattern_count']} | {errors} |"
        )
    lines.extend(
        [
            "",
            "## 限制",
            "",
            "- AI 文本来源检测器未配置；文案风格信号不等于真伪判决。",
            "- LOTA、NFA-ViT、TruFor 等像素级鉴伪模型没有进入主流程；Qwen 仅负责可见语义和图文一致性判断。",
            "- C2PA/EXIF 缺失按中性处理，数据包说明元数据因 PDF 导出被剥离，不能作为区分真伪的信号。",
            "- 评论仅为测试包转录的可见部分；样本量和截图选择会影响评论异常模式分析。",
            "- 10 个帖子不足以支撑泛化准确率结论；请结合每篇 JSON 报告和人工依据复核误报/漏报。",
            "",
            "## 报告文件",
            "",
            "每个 `sample_XXX.json` 保存单篇模型输出；`evaluation.json` 保存人工标签与结果对照。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run(dataset_root: Path, output_dir: Path, nmpa_db: Path | None = None) -> None:
    dataset_root = dataset_root.expanduser().resolve()
    output_dir = output_dir.expanduser()
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    samples = sorted(dataset_root.glob("sample_*"))
    samples = [sample for sample in samples if sample.is_dir()]
    if len(samples) != 10:
        raise SystemExit(f"Expected 10 sample directories, found {len(samples)} at {dataset_root}")

    settings = Settings.from_env()
    engine = ContentTrustEngine(settings)
    registry = None
    if nmpa_db is not None:
        nmpa_db = nmpa_db.expanduser()
        if not nmpa_db.is_absolute():
            nmpa_db = ROOT / nmpa_db
        registry = NMPARegistry(nmpa_db)
        _print_progress(f"Loaded local registry: {registry.count()} records")

    _print_progress(
        f"Running {len(samples)} human-labeled posts with DeepSeek text/comments and "
        f"Qwen vision ({settings.qwen_vl_model}); labels stay out of model inputs."
    )
    results: list[dict[str, Any]] = []
    all_images = all_comments = 0
    for index, sample_dir in enumerate(samples, 1):
        sample_id = sample_dir.name
        post_dir = sample_dir / "post"
        text = (post_dir / "content.txt").read_text(encoding="utf-8").strip()
        image_paths = sorted(
            post_dir.glob("image_*.jpg"),
            key=lambda item: int(re.search(r"image_(\d+)", item.stem).group(1)),
        )
        if not image_paths:
            raise SystemExit(f"No images found for {sample_id}")
        raw_comment_rows = [
            line
            for line in (sample_dir / "comments.txt").read_text(encoding="utf-8").splitlines()
            if COMMENT_ROW.match(line)
        ]
        comments, omitted_empty, parse_warnings = _parse_comments(sample_dir / "comments.txt")
        annotation = _human_annotation(sample_dir / "meta.txt")
        scope = _format_comment_scope(len(raw_comment_rows), omitted_empty)

        evidence_items = []
        if registry is not None:
            for match in registry.find_mentions(text):
                evidence_items.append(match.to_evidence_item())

        _print_progress(
            f"[{index}/10 {sample_id}] {len(image_paths)} images, "
            f"{len(comments)}/{len(raw_comment_rows)} comments sent; "
            "human labels withheld from model input."
        )
        report = engine.analyze_batch(
            BatchTrustInput(
                content_id=sample_id,
                text=text,
                images=[
                    TrustImageInput(id=f"{sample_id}-image-{image_index:02d}", image_path=str(path))
                    for image_index, path in enumerate(image_paths, 1)
                ],
                comments=comments,
                comment_scope=scope,
                evidence_items=evidence_items,
                locale="zh-CN",
            ),
            progress_callback=lambda message, prefix=f"[{index}/10 {sample_id}] ": _print_progress(
                prefix + message
            ),
        )
        report_path = output_dir / f"{sample_id}.json"
        report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        text_findings = list(report.findings)
        image_findings = [
            finding
            for image in report.images
            for finding in image.crossmodal_analysis.image_text_findings
        ]
        finding_categories = sorted(
            {finding.category for finding in [*text_findings, *image_findings]}
        )
        result = {
            "sample_id": sample_id,
            "human_annotation": annotation,
            "review_priority": report.review_priority,
            "triage_flagged": report.review_priority in {"review", "high"},
            "image_count": len(image_paths),
            "comment_count": len(comments),
            "comment_rows_in_source": len(raw_comment_rows),
            "empty_comment_rows_omitted": omitted_empty,
            "comment_suspicious_pattern_count": (
                report.comment_analysis.suspicious_pattern_count
                if report.comment_analysis is not None
                else 0
            ),
            "finding_count": len(text_findings) + len(image_findings),
            "finding_categories": finding_categories,
            "provider_errors": report.model_metadata.get("provider_errors", []),
            "latency_ms": report.model_metadata.get("latency_ms"),
            "report_path": str(report_path.relative_to(ROOT)),
            "summary": report.summary,
            "parse_warnings": parse_warnings,
        }
        results.append(result)
        all_images += len(image_paths)
        all_comments += len(comments)

        rows, metrics = _evaluation_rows(results)
        (output_dir / "evaluation.partial.json").write_text(
            json.dumps(
                {
                    "dataset_root": str(dataset_root.relative_to(ROOT)),
                    "completed_sample_count": len(results),
                    "image_count": all_images,
                    "comment_count": all_comments,
                    "metrics": metrics,
                    "samples": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    rows, metrics = _evaluation_rows(results)
    evaluation = {
        "experiment": "current_content_trust_engine_on_human_labeled_test_package",
        "status": "exploratory_post_level_triage_comparison",
        "dataset_root": str(dataset_root.relative_to(ROOT)),
        "sample_count": len(results),
        "real_sample_count": sum(not item["human_annotation"]["is_fake"] for item in results),
        "fake_sample_count": sum(item["human_annotation"]["is_fake"] for item in results),
        "image_count": all_images,
        "comment_count_sent_to_engine": all_comments,
        "nmpa_registry_record_count": registry.count() if registry else 0,
        "metrics": metrics,
        "samples": rows,
    }
    (output_dir / "evaluation.json").write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_markdown(output_dir / "evaluation_report.md", results, metrics)
    (output_dir / "evaluation.partial.json").unlink(missing_ok=True)
    print(
        json.dumps(
            {
                "evaluation_report": str((output_dir / "evaluation_report.md").relative_to(ROOT)),
                "evaluation_json": str((output_dir / "evaluation.json").relative_to(ROOT)),
                "sample_count": len(results),
                "image_count": all_images,
                "comment_count_sent_to_engine": all_comments,
                "metrics": metrics,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--nmpa-db", type=Path, default=None)
    args = parser.parse_args()
    run(args.dataset, args.output_dir, args.nmpa_db)


if __name__ == "__main__":
    main()
