"""Run Content Trust Engine on an imported Xiaohongshu post or JSONL samples."""

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

from content_trust import (
    BatchTrustInput,
    CommentInput,
    ContentTrustEngine,
    TrustImageInput,
)
from content_trust.config import Settings
from content_trust.nmpa import NMPARegistry


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "xhs_69410496000000001f00d4bf_20261014"
NOTE_ID = "69410496000000001f00d4bf"


def _print_progress(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _print_provider_status(settings: Settings) -> None:
    if not (settings.qwen_api_key or settings.dashscope_api_key):
        print(
            "QWEN_API_KEY is not configured; Qwen image analysis will be unavailable."
        )
    else:
        print(
            f"Qwen image analysis is configured: {settings.qwen_vl_model} "
            f"(timeout={settings.qwen_vl_timeout_seconds:.0f}s)."
        )
    if not settings.deepseek_api_key:
        print(
            "DeepSeek API credentials are not configured; "
            "text and explanation will use local fallbacks, comments will use "
            "local pattern rules. Qwen image analysis can still run."
        )
    print("LOTA, NFA-ViT and TruFor are disabled in the main run.")


def _load_manifest(manifest_path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"Invalid JSON in {manifest_path} at line {line_number}: {exc}"
            ) from exc
        if not isinstance(record, dict):
            raise SystemExit(
                f"Expected a JSON object in {manifest_path} at line {line_number}"
            )
        records.append(record)
    if not records:
        raise SystemExit(f"No samples found in {manifest_path}")
    return records


def run_manifest(
    manifest_path: Path,
    limit: int | None = None,
    *,
    nmpa_db_path: Path | None = None,
    output_dir: Path | None = None,
) -> None:
    manifest_path = manifest_path.expanduser()
    if not manifest_path.is_absolute():
        manifest_path = ROOT / manifest_path
    if not manifest_path.is_file():
        raise SystemExit(f"Manifest not found: {manifest_path}")

    records = _load_manifest(manifest_path)
    if limit is not None:
        if limit < 1:
            raise SystemExit("--limit must be greater than zero")
        records = records[:limit]

    prepared = []
    for index, record in enumerate(records, start=1):
        sample_id = str(record.get("sample_id") or f"sample-{index:03d}")
        image_value = record.get("image_path")
        if not image_value:
            raise SystemExit(f"Sample {sample_id} has no image_path")
        image_path = Path(str(image_value)).expanduser()
        if not image_path.is_absolute():
            image_path = manifest_path.parent / image_path
        if not image_path.is_file():
            raise SystemExit(f"Image for sample {sample_id} not found: {image_path}")
        prepared.append((sample_id, record, image_path))

    post_groups: dict[str, list[tuple[str, dict[str, Any], Path]]] = {}
    for sample_id, record, image_path in prepared:
        post_group_id = str(record.get("post_group_id") or sample_id)
        post_groups.setdefault(post_group_id, []).append(
            (sample_id, record, image_path)
        )

    settings = Settings.from_env()
    _print_provider_status(settings)
    engine = ContentTrustEngine(settings)
    registry = None
    registry_record_count = 0
    if nmpa_db_path is not None:
        nmpa_db_path = nmpa_db_path.expanduser()
        if not nmpa_db_path.is_absolute():
            nmpa_db_path = ROOT / nmpa_db_path
        if not nmpa_db_path.is_file():
            raise SystemExit(f"NMPA database not found: {nmpa_db_path}")
        registry = NMPARegistry(nmpa_db_path)
        registry_record_count = registry.count()
        _print_progress(
            f"Local NMPA registry loaded: {registry_record_count} records "
            "(matching only; records retain their declared source/provenance)."
        )
    report_dir = output_dir or (manifest_path.parent / "reports")
    report_dir = report_dir.expanduser()
    if not report_dir.is_absolute():
        report_dir = ROOT / report_dir
    report_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"Analyzing {len(prepared)} images in {len(post_groups)} post groups "
        f"from {manifest_path} ... "
        f"(Qwen={settings.qwen_vl_model}, "
        f"Qwen timeout={settings.qwen_vl_timeout_seconds:.0f}s; "
        f"DeepSeek timeout={settings.timeout_seconds:.0f}s)",
        flush=True,
    )
    index_records = []
    for position, (post_group_id, samples) in enumerate(post_groups.items(), start=1):
        sample_ids = [sample_id for sample_id, _, _ in samples]
        captions = list(
            dict.fromkeys(
                str(record.get("caption_excerpt") or "").strip()
                for _, record, _ in samples
                if str(record.get("caption_excerpt") or "").strip()
            )
        )
        caption = "\n".join(captions) or None
        products = list(
            dict.fromkeys(
                str(record.get("visible_product") or "").strip()
                for _, record, _ in samples
                if str(record.get("visible_product") or "").strip()
            )
        )
        product_summary = "、".join(products) or "商品名未标注"
        registry_scan_text = "\n".join(
            [
                caption or "",
                *products,
                *(
                    str(visible_text).strip()
                    for _, record, _ in samples
                    for visible_text in (record.get("visible_text") or [])
                    if str(visible_text).strip()
                ),
            ]
        )
        registry_matches = (
            registry.find_mentions(registry_scan_text)
            if registry is not None
            else []
        )
        evidence_items = [match.to_evidence_item() for match in registry_matches]
        if registry is not None:
            _print_progress(
                f"[{position}/{len(post_groups)}] 本地备案库精确名称匹配："
                f"{len(registry_matches)} 条"
            )
        _print_progress(
            f"[{position}/{len(post_groups)}] post_group={post_group_id}: "
            f"{len(samples)} 张图，样本 {', '.join(sample_ids)}；{product_summary}"
        )
        report = engine.analyze_batch(
            BatchTrustInput(
                content_id=f"xhs-{post_group_id}",
                text=caption,
                images=[
                    TrustImageInput(id=sample_id, image_path=str(image_path))
                    for sample_id, _, image_path in samples
                ],
                evidence_items=evidence_items,
                locale="zh-CN",
            ),
            progress_callback=lambda message, prefix=(
                f"[{position}/{len(post_groups)} {post_group_id}] "
            ): _print_progress(prefix + message),
        )
        if registry_matches:
            metadata = dict(report.model_metadata)
            metadata.update(
                {
                    "nmpa_registry_record_count": registry_record_count,
                    "nmpa_registry_match_count": len(registry_matches),
                    "nmpa_registry_source_notice": (
                        "Matched rows come from the imported Wanxiang Data sample "
                        "snapshot and were not individually re-verified against NMPA."
                    ),
                }
            )
            report = report.model_copy(
                update={
                    "product_registry_matches": [
                        match.to_public_match() for match in registry_matches
                    ],
                    "model_metadata": metadata,
                }
            )
        safe_group_id = re.sub(r"[^A-Za-z0-9._-]+", "_", post_group_id)
        report_path = report_dir / f"post-{safe_group_id}.json"
        report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        source_url = next(
            (record.get("source_url") for _, record, _ in samples if record.get("source_url")),
            None,
        )
        index_records.append(
            {
                "post_group_id": post_group_id,
                "sample_ids": sample_ids,
                "source_url": source_url,
                "image_count": len(samples),
                "report_path": str(report_path.relative_to(ROOT)),
                "review_priority": report.review_priority,
                "summary": report.summary,
                "finding_count": len(report.findings),
                "finding_categories": sorted(
                    {finding.category for finding in report.findings}
                ),
                "nmpa_registry_matches": [
                    match.to_public_match().model_dump(mode="json")
                    for match in registry_matches
                ],
                "provider_errors": report.model_metadata.get("provider_errors", []),
            }
        )

    index_path = report_dir / "index.json"
    index_path.write_text(
        json.dumps(
            {
                "manifest": str(manifest_path.relative_to(ROOT)),
                "analyzed_post_group_count": len(index_records),
                "analyzed_image_count": len(prepared),
                "nmpa_registry_record_count": registry_record_count,
                "nmpa_registry_matched_post_group_count": sum(
                    bool(item["nmpa_registry_matches"]) for item in index_records
                ),
                "nmpa_registry_unique_matched_record_count": len(
                    {
                        match["record_id"]
                        for item in index_records
                        for match in item["nmpa_registry_matches"]
                    }
                ),
                "post_group_count": len(index_records),
                "reports": index_records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "manifest": str(manifest_path.relative_to(ROOT)),
                "analyzed_post_group_count": len(index_records),
                "analyzed_image_count": len(prepared),
                "report_index": str(index_path.relative_to(ROOT)),
                "failed_post_groups": [
                    item["post_group_id"]
                    for item in index_records
                    if item["provider_errors"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


def run_original_post() -> None:
    text = (DATA_DIR / "post.txt").read_text(encoding="utf-8")
    image_paths = sorted((DATA_DIR / "images").glob("slide-*.webp"))
    comments_path = DATA_DIR / "comments.jsonl"
    comments = []
    if comments_path.exists():
        comments = [
            CommentInput.model_validate(json.loads(line))
            for line in comments_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    comments_metadata_path = DATA_DIR / "comments_metadata.json"
    comments_metadata = (
        json.loads(comments_metadata_path.read_text(encoding="utf-8"))
        if comments_metadata_path.exists()
        else {}
    )
    comment_scope = None
    if comments:
        comment_scope = (
            f"页面显示 {comments_metadata.get('reported_total_comments', '未知')} 条评论；"
            f"本次仅采集公开预览中的 {len(comments)} 条，其他评论需要登录查看；"
            "仅保存文本、显示日期和地区，未采集账号标识、点赞数或回复，"
            "因此无法分析账号真实性或评论互动/协同关系。"
        )
    if not image_paths:
        raise SystemExit(f"No imported images found in {DATA_DIR / 'images'}")

    settings = Settings.from_env()
    _print_provider_status(settings)
    if comments and not settings.deepseek_api_key:
        print("Comment analysis will use local pattern rules without DeepSeek review.")

    engine = ContentTrustEngine(settings)
    report_dir = DATA_DIR / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"Analyzing shared post text once, {len(image_paths)} images, "
        f"and {len(comments)} comments ... "
        f"(Qwen={settings.qwen_vl_model}, "
        f"Qwen timeout={settings.qwen_vl_timeout_seconds:.0f}s; "
        f"DeepSeek timeout={settings.timeout_seconds:.0f}s)",
        flush=True,
    )
    report = engine.analyze_batch(
        BatchTrustInput(
            content_id=f"xhs-{NOTE_ID}",
            text=text,
            images=[
                TrustImageInput(id=image_path.stem, image_path=str(image_path))
                for image_path in image_paths
            ],
            comments=comments,
            comment_scope=comment_scope,
            evidence_items=[],
            locale="zh-CN",
        ),
        progress_callback=_print_progress,
    )
    report_path = report_dir / "content-report.json"
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(report_path.relative_to(ROOT)),
                "review_priority": report.review_priority,
                "image_count": len(report.images),
                "comment_count": (
                    report.comment_analysis.assessed_count
                    if report.comment_analysis is not None
                    else 0
                ),
                "comment_total_count": (
                    report.comment_analysis.total_comment_count
                    if report.comment_analysis is not None
                    else 0
                ),
                "comment_llm_input_count": (
                    report.comment_analysis.llm_input_count
                    if report.comment_analysis is not None
                    else 0
                ),
                "comment_suspicious_pattern_count": (
                    report.comment_analysis.suspicious_pattern_count
                    if report.comment_analysis is not None
                    else 0
                ),
                "claim_evidence_summary": report.claim_evidence_summary.model_dump(
                    mode="json"
                ),
                "evidence_cards": [
                    {
                        "dimension": card.dimension,
                        "status": card.status,
                        "title": card.title,
                        "observations": card.observations,
                    }
                    for card in report.evidence_cards
                ],
                "summary": report.summary,
                "text_analysis_runs": report.model_metadata.get("text_analysis_runs"),
                "image_vision_runs": report.model_metadata.get("image_vision_runs"),
                "explanation_runs": report.model_metadata.get("explanation_runs"),
                "comment_analysis_runs": report.model_metadata.get("comment_analysis_runs"),
                "findings": [
                    {
                        "category": finding.category,
                        "verdict": finding.verdict,
                        "severity": finding.severity,
                        "observation": finding.observation,
                        "reason": finding.reason,
                        "source": finding.source,
                        "evidence_ids": finding.evidence_ids,
                        "recommended_action": finding.recommended_action,
                    }
                    for finding in report.findings
                ],
                "provider_errors": report.model_metadata.get("provider_errors", []),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Analyze each image/caption pair from a JSONL dataset manifest.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Analyze only the first N manifest samples.",
    )
    parser.add_argument(
        "--nmpa-db",
        type=Path,
        help="Optional local NMPA registry SQLite database; only exact full-name/alias mentions are matched.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Optional report directory; defaults to <manifest-dir>/reports.",
    )
    args = parser.parse_args()
    if args.limit is not None and args.manifest is None:
        parser.error("--limit can only be used with --manifest")
    if args.manifest is not None:
        run_manifest(
            args.manifest,
            args.limit,
            nmpa_db_path=args.nmpa_db,
            output_dir=args.output_dir,
        )
    else:
        run_original_post()


if __name__ == "__main__":
    main()
