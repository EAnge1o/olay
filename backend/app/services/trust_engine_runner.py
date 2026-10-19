"""Adapter for the Applied-AI delivery: ``content-trust-engine-v10``.

Why an adapter instead of calling the engine from the API layer:

* **One engine call per task.** ``ContentTrustAgent.analyze`` produces text,
  comment *and* image dimensions in a single pass (≈50 s with live providers),
  so calling it once per dimension would triple latency and cost. The runner
  therefore returns a *bundle* of dimension outputs that the analysis service
  fans out into the existing per-agent rows.
* **Blocking API.** ``analyze`` is synchronous, so it is dispatched through
  ``asyncio.to_thread`` — otherwise it would stall the whole event loop.
* **Graceful degradation.** If the package is missing, credentials are absent
  and nothing is assessable, or the engine raises, ``available`` is False and
  the caller falls back to the built-in heuristics. The demo never depends on
  this package.

Contract stability: the mapped ``AgentOutput`` objects feed the *existing*
ReportAgent and the *existing* ``agent_results`` rows, so ``/api/report`` and
``/api/status`` keep their frozen shape. Everything the engine adds beyond that
(``role_runs``, ``evidence_chain``, ``limitations``) is persisted separately in
``engine_traces`` and served by the read-only ``/api/engine-trace`` endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.core.enums import AgentName, RiskLevel
from app.models.content_sample import ContentSample
from app.schemas.agent import AgentOutput, Finding
from app.services.glm_vision import build_glm_vision

logger = logging.getLogger(__name__)

# Engine dimension  ->  our public agent key. Identity mapping: the public
# vocabulary was renamed to match the engine's role names one-for-one, which is
# also what the frontend's T/R/U/S codes resolve against.
ENGINE_TO_OURS: dict[str, str] = {
    "text": AgentName.TEXT.value,
    "comment": AgentName.COMMENT.value,
    "image": AgentName.IMAGE.value,
    "account": AgentName.ACCOUNT.value,
}
OURS_TO_ENGINE: dict[str, str] = {v: k for k, v in ENGINE_TO_OURS.items()}

MAX_FINDINGS_PER_DIMENSION = 12

# -- optional dependency -----------------------------------------------------
_IMPORT_ERROR: str | None = None
try:  # pragma: no cover - presence depends on the local install
    from content_trust import AgentInput as _EngineAgentInput
    from content_trust import AccountHistoryInput as _EngineAccountHistory
    from content_trust import CommentInput as _EngineCommentInput
    from content_trust import ContentTrustAgent as _ContentTrustAgent
    from content_trust import ContentTrustEngine as _ContentTrustEngine
    from content_trust import TrustImageInput as _EngineImageInput
    # NOTE: `Settings` is deliberately NOT re-exported by content_trust/__init__,
    # so it must be imported from its own module.
    from content_trust.config import Settings as _EngineSettings
except Exception as exc:  # noqa: BLE001 - any import problem must degrade, not crash
    _IMPORT_ERROR = str(exc)

ENGINE_AVAILABLE = _IMPORT_ERROR is None


@dataclass(slots=True)
class EngineRunResult:
    """Everything one engine execution produced, already normalized."""

    available: bool
    dimension_outputs: dict[str, AgentOutput] = field(default_factory=dict)
    trace: dict[str, Any] | None = None
    overall_score: float | None = None
    overall_risk_level: str | None = None
    latency_ms: int | None = None
    error: str | None = None


class TrustEngineRunner:
    """Runs ``ContentTrustAgent`` off the event loop and maps its output."""

    backend = "trust_engine"

    def __init__(
        self, agent: Any | None, settings: Settings, vision_provider: Any | None = None
    ) -> None:
        self._agent = agent
        self._settings = settings
        # Set when we injected our own cross-modal provider (GLM) instead of the
        # engine's built-in Qwen client; used to correct the trace's labels.
        self._vision_provider = vision_provider

    @property
    def available(self) -> bool:
        return self._agent is not None

    @property
    def vision_provider(self) -> Any | None:
        """The cross-modal provider we injected (GLM), or ``None`` for the engine default."""
        return self._vision_provider

    @classmethod
    def from_settings(cls, settings: Settings) -> "TrustEngineRunner":
        if not ENGINE_AVAILABLE:
            logger.warning(
                "trust engine requested but `content_trust` is not importable (%s); "
                "heuristics will be used",
                _IMPORT_ERROR,
            )
            return cls(agent=None, settings=settings)

        engine_settings = _EngineSettings(
            deepseek_api_key=settings.deepseek_api_key,
            deepseek_base_url=settings.deepseek_base_url,
            deepseek_model=settings.deepseek_model,
            qwen_api_key=settings.qwen_api_key or settings.dashscope_api_key,
            dashscope_api_key=settings.dashscope_api_key,
            qwen_vl_base_url=settings.qwen_vl_base_url,
            qwen_vl_model=settings.qwen_vl_model,
            qwen_vl_timeout_seconds=settings.qwen_vl_timeout_seconds,
        )
        # The image role runs on GLM when a GLM key is present; otherwise the
        # engine falls back to its own Qwen-VL client, and failing that the
        # dimension is reported unavailable.
        vision = build_glm_vision(settings)
        has_qwen = bool(engine_settings.qwen_api_key)
        providers = (
            f"deepseek={'yes' if engine_settings.deepseek_api_key else 'no'}, "
            f"vision={vision.provider_name if vision is not None else ('qwen_vision' if has_qwen else 'none')}"
        )
        try:
            if vision is not None:
                # Inject our cross-modal provider. The engine still constructs its
                # own DeepSeek client for the text / comment / explanation roles
                # (passing an engine does not disable that).
                engine = _ContentTrustEngine(engine_settings, crossmodal_provider=vision)
                agent = _ContentTrustAgent(
                    settings=engine_settings, engine=engine, screenshot_provider=vision
                )
            else:
                agent = _ContentTrustAgent(settings=engine_settings)
        except Exception:  # noqa: BLE001
            logger.exception("trust engine initialization failed; heuristics will be used")
            return cls(agent=None, settings=settings)

        logger.info("trust engine ready (%s)", providers)
        return cls(agent=agent, settings=settings, vision_provider=vision)

    # -- execution -----------------------------------------------------------

    async def run(self, *, task_id: str, sample: ContentSample) -> EngineRunResult:
        if self._agent is None:
            return EngineRunResult(available=False, error="engine_unavailable")

        try:
            engine_input = self._build_input(task_id=task_id, sample=sample)
        except Exception as exc:  # noqa: BLE001 - bad payload must not kill the task
            logger.warning("trust engine input rejected: %s", exc)
            return EngineRunResult(available=False, error=f"input_error: {exc}")

        started = time.perf_counter()
        try:
            # Blocking, provider-bound call — must not run on the event loop.
            result = await asyncio.to_thread(self._agent.analyze, engine_input)
            payload = result.model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            logger.exception("trust engine execution failed; falling back to heuristics")
            return EngineRunResult(available=False, error=str(exc))

        latency_ms = int((time.perf_counter() - started) * 1000)
        return self._map(payload, latency_ms=latency_ms)

    def _build_input(self, *, task_id: str, sample: ContentSample) -> Any:
        images = (
            [_EngineImageInput(id="image-1", image_path=sample.image_path)]
            if sample.image_path
            else []
        )
        comments = [
            _EngineCommentInput(
                id=f"c-{item.get('index', index)}",
                text=str(item.get("content", "")),
                author=item.get("author") or None,
                published_at=item.get("created_at") or None,
            )
            for index, item in enumerate(sample.comment_payload or [])
            if str(item.get("content", "")).strip()
        ]
        account_history = None
        snapshot = sample.account_snapshot
        if snapshot is not None:
            account_history = _EngineAccountHistory.model_validate(
                {
                    "handle": snapshot.handle,
                    "profile_text": snapshot.profile_text,
                    "follower_count": snapshot.follower_count,
                    "total_post_count": snapshot.total_post_count,
                    "posts": snapshot.posts or [],
                }
            )

        return _EngineAgentInput(
            task_id=task_id,
            content_id=sample.id,
            text=sample.text_content,
            images=images,
            comments=comments,
            account_history=account_history,
            locale="zh-CN",
            include_debug_log=True,
        )

    # -- mapping -------------------------------------------------------------

    def _map(self, payload: dict[str, Any], *, latency_ms: int) -> EngineRunResult:
        if self._vision_provider is not None:
            _relabel_vision_provider(payload, self._vision_provider)

        report = payload.get("analysis_report") or {}
        assessments_raw = payload.get("dimension_assessments") or []
        roles_raw = payload.get("role_runs") or []
        limitations = list(payload.get("limitations") or [])
        major_evidence = list(payload.get("major_evidence") or [])
        provider_errors = list((payload.get("model_metadata") or {}).get("provider_errors") or [])

        assessments = {item.get("dimension"): item for item in assessments_raw}
        roles = {item.get("role"): item for item in roles_raw}
        findings_by_dimension = _findings_by_dimension(report)
        findings_by_dimension["account"] = _account_findings(payload)

        trace = {
            "backend": self.backend,
            "engine_schema_version": report.get("schema_version"),
            "overall_score": payload.get("overall_score"),
            "overall_risk_level": payload.get("overall_risk_level"),
            "input_kind": payload.get("input_kind"),
            "enabled_dimensions": payload.get("enabled_dimensions") or [],
            "dimension_assessments": assessments_raw,
            # Engine calls these "role_runs"; the API exposes them as the four
            # visible specialist roles (text / comment / account / image).
            "visible_roles": roles_raw,
            "evidence_chain": payload.get("evidence_chain") or [],
            "major_evidence": major_evidence,
            "limitations": limitations,
            "provider_errors": provider_errors,
            "model_metadata": payload.get("model_metadata") or {},
            "summary": payload.get("summary"),
            "latency_ms": latency_ms,
        }

        assessed = [a for a in assessments_raw if a.get("status") == "assessed"]
        if not assessed:
            # Nothing was assessable (e.g. image-only with no Qwen key). Let the
            # heuristics produce a meaningful result rather than an empty report.
            return EngineRunResult(
                available=False,
                trace=trace,
                latency_ms=latency_ms,
                error="engine produced no assessable dimension",
            )

        outputs = {
            our_name: _to_agent_output(
                our_name=our_name,
                engine_dimension=engine_dim,
                assessment=assessments.get(engine_dim),
                findings=findings_by_dimension.get(engine_dim, []),
                role=roles.get(engine_dim),
                limitations=limitations,
            )
            for engine_dim, our_name in ENGINE_TO_OURS.items()
        }

        return EngineRunResult(
            available=True,
            dimension_outputs=outputs,
            trace=trace,
            overall_score=_as_float(payload.get("overall_score")),
            overall_risk_level=payload.get("overall_risk_level"),
            latency_ms=latency_ms,
        )


# -- helpers -----------------------------------------------------------------


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _account_findings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Map the engine's account-history findings into our finding shape.

    The engine keeps account results outside ``analysis_report`` (in
    ``account_analysis``) and its findings carry a ``verdict`` instead of a
    ``category``, so they need their own adapter.
    """
    analysis = payload.get("account_analysis") or {}
    mapped: list[dict[str, Any]] = []
    for finding in analysis.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        mapped.append(
            {
                "category": "account_pattern",
                "observation": finding.get("observation"),
                "reason": finding.get("reason"),
                "severity": finding.get("severity"),
                "risk_score": finding.get("risk_score"),
                "recommended_action": finding.get("recommended_action"),
            }
        )
    return mapped


def _relabel_vision_provider(payload: dict[str, Any], provider: Any) -> None:
    """Correct the engine's hardcoded ``qwen_vision`` label on the image role.

    ``ContentTrustAgent._build_role_runs`` writes ``provider="qwen_vision"`` and
    ``role_provider_map["image"]="qwen_vision"`` regardless of what was actually
    injected. When we supplied our own cross-modal provider we know the truth, so
    the trace is corrected rather than left misreporting which vendor ran.
    """
    name = getattr(provider, "provider_name", None)
    if not name:
        return
    model = getattr(provider, "model_name", None)
    prompt_version = getattr(provider, "prompt_version", None)

    for role in payload.get("role_runs") or []:
        if role.get("role") != "image":
            continue
        role["provider"] = name
        if model:
            role["model"] = model
        if prompt_version:
            role["prompt_version"] = prompt_version

    metadata = payload.get("model_metadata")
    if isinstance(metadata, dict):
        if isinstance(metadata.get("role_provider_map"), dict):
            metadata["role_provider_map"]["image"] = name
        metadata["vision_provider"] = name
        metadata["vision_model"] = model


def _to_agent_output(
    *,
    our_name: str,
    engine_dimension: str,
    assessment: dict[str, Any] | None,
    findings: list[dict[str, Any]],
    role: dict[str, Any] | None,
    limitations: list[str],
) -> AgentOutput:
    """Map one engine ``DimensionAssessment`` onto our frozen ``AgentOutput``."""
    role = role or {}
    meta: dict[str, Any] = {
        "backend": TrustEngineRunner.backend,
        "engine_dimension": engine_dimension,
        "engine_status": (assessment or {}).get("status", "not_applicable"),
        "provider": role.get("provider"),
        "model": role.get("model"),
        "prompt_version": role.get("prompt_version"),
        "role_status": role.get("status"),
        "provider_error": role.get("error_code"),
        "limitations": limitations[:8],
    }

    if assessment is None:
        return AgentOutput(
            agent=our_name,
            risk_level=RiskLevel.LOW,
            score=0.0,
            summary="引擎未对该维度产出结果（缺少对应输入或维度未启用）。",
            findings=[],
            recommendations=[],
            metrics={"engine_status": "not_applicable"},
            meta=meta,
        )

    status = assessment.get("status")
    score = _as_float(assessment.get("score"))
    if status != "assessed" or score is None:
        return AgentOutput(
            agent=our_name,
            risk_level=RiskLevel.LOW,
            score=0.0,
            summary=str(assessment.get("summary") or "该维度没有可用结果。"),
            findings=[],
            recommendations=[],
            metrics={"engine_status": status},
            meta=meta,
        )

    # Thresholds are the engine's own (medium 30 / high 60); see the API contract.
    try:
        level = RiskLevel(str(assessment.get("risk_level")))
    except ValueError:
        level = RiskLevel.from_score(score)

    mapped = [_to_finding(item, level) for item in findings][:MAX_FINDINGS_PER_DIMENSION]
    if not mapped:
        mapped = [
            Finding(
                type="engine_evidence",
                evidence=str(item),
                confidence=0.6,
                severity=level,
                detail="来自引擎证据集，未细分为具体风险类别。",
            )
            for item in (assessment.get("evidence") or [])[:5]
        ]

    recommendations = [
        str(item.get("recommended_action"))
        for item in findings
        if item.get("recommended_action")
    ]
    # The engine states these caveats explicitly; keeping them in the payload is
    # what stops a reviewer from reading the score as a fake/real probability.
    for note in assessment.get("limitations") or []:
        meta.setdefault("dimension_limitations", [])
        meta["dimension_limitations"].append(str(note))

    return AgentOutput(
        agent=our_name,
        risk_level=level,
        score=score,
        summary=str(assessment.get("summary") or ""),
        findings=mapped,
        recommendations=list(dict.fromkeys(recommendations)),
        metrics={"engine_status": status},
        meta=meta,
    )


def _to_finding(raw: dict[str, Any], default_severity: RiskLevel) -> Finding:
    try:
        severity = RiskLevel(str(raw.get("severity")))
    except ValueError:
        severity = default_severity
    confidence = _as_float(raw.get("risk_score"))
    confidence = 0.5 if confidence is None else min(1.0, max(0.0, confidence))
    return Finding(
        type=str(raw.get("category") or "engine_finding"),
        evidence=str(raw.get("observation") or ""),
        confidence=confidence,
        severity=severity,
        detail=raw.get("reason"),
    )


def _findings_by_dimension(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Collect the engine's structured findings per dimension.

    The engine stores text findings under ``text_analysis``, image findings
    under ``images[]`` (batch) or in the top-level list (single image), and
    comment signals as pattern clusters rather than findings.
    """
    out: dict[str, list[dict[str, Any]]] = {"text": [], "image": [], "comment": []}
    if not isinstance(report, dict) or not report:
        return out

    text_analysis = report.get("text_analysis") or {}
    text_findings = list(text_analysis.get("findings") or [])
    out["text"].extend(text_findings)

    images = report.get("images")
    if isinstance(images, list) and images:
        for image in images:
            out["image"].extend(image.get("findings") or [])
    elif report.get("image_analysis") is not None:
        text_ids = {item.get("id") for item in text_findings}
        out["image"].extend(
            item for item in (report.get("findings") or []) if item.get("id") not in text_ids
        )

    comment_analysis = report.get("comment_analysis") or {}
    for cluster in comment_analysis.get("pattern_clusters") or []:
        pattern = str(cluster.get("pattern_type") or "comment_pattern")
        # The engine marks this bucket as "sampled, not a signal" — skip it.
        if pattern == "sampled_unique_comments":
            continue
        verdict = cluster.get("verdict")
        out["comment"].append(
            {
                "category": pattern,
                "observation": cluster.get("representative_text") or "",
                "reason": cluster.get("reason"),
                "severity": "high" if verdict == "suspicious" else "medium",
                "risk_score": 0.82 if verdict == "suspicious" else 0.4,
                "recommended_action": cluster.get("recommended_action"),
            }
        )
    return out
