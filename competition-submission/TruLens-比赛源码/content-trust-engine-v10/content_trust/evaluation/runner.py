"""Run an offline-first JSONL evaluation against the public engine API."""

import argparse
import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from content_trust.config import Settings
from content_trust.evaluation.metrics import summarize_results
from content_trust.schemas import EvidenceItem, TextIntegritySignalType, TrustInput
from content_trust.schemas.output import FindingCategory
from content_trust.service import ContentTrustEngine

_NON_API_ERRORS = {
    "provider_not_configured",
    "image_unavailable",
    "image_source_required",
    "invalid_image_url",
    "image_url_too_long",
    "image_too_large",
    "unsupported_image_type",
    "image_required",
}


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    text: str | None = None
    image: str | None = None
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    expected_categories: list[FindingCategory] = Field(default_factory=list)
    expected_review_priority: Literal["low", "review", "high"] | None = None
    expected_text_integrity_signals: list[TextIntegritySignalType] = Field(
        default_factory=list
    )
    expected_text_integrity_status: Literal[
        "signals_found", "no_clear_signal", "insufficient_context"
    ] | None = None
    fixture_condition: str = ""
    requires_providers: list[
        Literal["deepseek", "nfa_vit_space", "ai_text_detector"]
    ] = Field(
        default_factory=list
    )
    notes: str = ""


def run_evaluation(
    jsonl_path: str | Path,
    *,
    engine: ContentTrustEngine | None = None,
    live: bool = False,
) -> dict[str, Any]:
    path = Path(jsonl_path)
    if engine is None:
        engine = ContentTrustEngine(Settings.from_env() if live else Settings())

    results = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = EvaluationCase.model_validate_json(line)
        except ValidationError as exc:
            invalid_case = _failed_case(f"line-{line_number}", str(exc))
            invalid_case["input_error"] = True
            results.append(invalid_case)
            continue
        configured = getattr(engine, "configured_providers", set())
        missing_providers = sorted(set(case.requires_providers) - set(configured))
        if missing_providers:
            results.append(
                _skipped_case(
                    case.id,
                    f"requires providers: {', '.join(missing_providers)}",
                    expected_categories=case.expected_categories,
                )
            )
            continue
        started = time.perf_counter()
        try:
            report = engine.analyze(_to_input(case, path.parent))
            payload = report.model_dump(mode="json")
            json.dumps(payload, ensure_ascii=False)
            predicted = sorted({finding.category for finding in report.findings})
            expected = sorted(set(case.expected_categories))
            integrity = report.text_analysis.text_integrity if report.text_analysis else None
            predicted_integrity = sorted(
                {signal.signal_type for signal in integrity.signals}
                if integrity is not None
                else set()
            )
            expected_integrity = sorted(set(case.expected_text_integrity_signals))
            provider_errors = [
                error
                for error in report.model_metadata.get("provider_errors", [])
                if error.rsplit(":", 1)[-1] not in _NON_API_ERRORS
            ]
            results.append(
                {
                    "id": case.id,
                    "json_valid": True,
                    "expected_categories": expected,
                    "predicted_categories": predicted,
                    "matched_categories": sorted(set(expected) & set(predicted)),
                    "missed_categories": sorted(set(expected) - set(predicted)),
                    "unexpected_categories": sorted(set(predicted) - set(expected)),
                    "false_positive": not expected and bool(predicted),
                    "expected_text_integrity_signals": expected_integrity,
                    "predicted_text_integrity_signals": predicted_integrity,
                    "matched_text_integrity_signals": sorted(
                        set(expected_integrity) & set(predicted_integrity)
                    ),
                    "missed_text_integrity_signals": sorted(
                        set(expected_integrity) - set(predicted_integrity)
                    ),
                    "false_positive_text_integrity": (
                        not expected_integrity and bool(predicted_integrity)
                    ),
                    "expected_text_integrity_status": case.expected_text_integrity_status,
                    "predicted_text_integrity_status": (
                        integrity.status if integrity is not None else None
                    ),
                    "text_integrity_status_match": (
                        integrity is not None
                        and integrity.status == case.expected_text_integrity_status
                        if case.expected_text_integrity_status is not None
                        else None
                    ),
                    "fixture_condition": case.fixture_condition,
                    "expected_review_priority": case.expected_review_priority,
                    "predicted_review_priority": report.review_priority,
                    "review_priority_match": (
                        report.review_priority == case.expected_review_priority
                        if case.expected_review_priority is not None
                        else None
                    ),
                    "provider_errors": provider_errors,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "explanation_complete": bool(
                        report.summary.strip()
                        and report.recommended_actions
                        and report.limitations
                        and report.evidence_cards
                    ),
                    "error": None,
                }
            )
        except Exception as exc:
            results.append(
                _failed_case(
                    case.id,
                    f"{type(exc).__name__}: {exc}",
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                    expected_categories=case.expected_categories,
                )
            )
    return summarize_results(results)


def _to_input(case: EvaluationCase, base_dir: Path) -> TrustInput:
    image_path = None
    image_url = None
    if case.image:
        if case.image.startswith(("https://", "http://")):
            image_url = case.image
        else:
            image_path = str((base_dir / case.image).resolve())
    return TrustInput(
        content_id=case.id,
        text=case.text,
        image_path=image_path,
        image_url=image_url,
        evidence_items=case.evidence_items,
    )


def _failed_case(
    case_id: str,
    error: str,
    *,
    latency_ms: float = 0.0,
    expected_categories: list[str] | None = None,
) -> dict[str, Any]:
    expected = sorted(set(expected_categories or []))
    return {
        "id": case_id,
        "json_valid": False,
        "expected_categories": expected,
        "predicted_categories": [],
        "matched_categories": [],
        "missed_categories": expected,
        "unexpected_categories": [],
        "false_positive": False,
        "provider_errors": [],
        "latency_ms": latency_ms,
        "explanation_complete": False,
        "error": error,
        "input_error": False,
    }


def _skipped_case(
    case_id: str,
    reason: str,
    *,
    expected_categories: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "json_valid": None,
        "expected_categories": sorted(set(expected_categories or [])),
        "predicted_categories": [],
        "matched_categories": [],
        "missed_categories": [],
        "unexpected_categories": [],
        "false_positive": False,
        "provider_errors": [],
        "latency_ms": 0.0,
        "explanation_complete": False,
        "error": None,
        "input_error": False,
        "skipped": True,
        "skip_reason": reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Content Trust Engine JSONL cases.")
    parser.add_argument(
        "jsonl",
        nargs="?",
        default=str(Path(__file__).with_name("sample_cases.jsonl")),
    )
    parser.add_argument("--output", help="Optional path to write the JSON metrics report.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use environment-configured providers; this can consume API credits.",
    )
    args = parser.parse_args()
    result = run_evaluation(args.jsonl, live=args.live)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
