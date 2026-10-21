"""Read-side mapping: persisted ``AgentResult`` rows -> API views.

Kept in the service layer (not the schema layer) because it needs ORM rows:
schemas stay pure wire contracts, services own the assembly.

Two rules this module exists to enforce, so no endpoint can get them wrong:

* **Direction.** The stored score is *risk* (higher = riskier). The frontend
  renders *credibility* (``Trust Score / 100``, higher = better), so the API
  boundary converts with :func:`trust_score_for` and never leaks a raw risk
  score under a "score" name the UI would misread.
* **Honesty.** A dimension that ran but had nothing to judge (``not_applicable``
  / ``insufficient_data`` / ``unavailable``) is ``assessed == false`` and its
  score is ``None`` — the UI shows "暂不评分" rather than a misleading 0.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.enums import (
    DIMENSION_META,
    DIMENSION_ORDER,
    TRUST_LABELS,
    RiskLevel,
    TaskStatus,
    TrustLevel,
    trust_level_for,
    trust_score_for,
)
from app.models.agent_result import AgentResult
from app.schemas.analysis import AgentStatusItem
from app.schemas.report import DimensionRead, EvidenceItem


def overall_trust(risk_level: RiskLevel | str) -> tuple[TrustLevel, str]:
    """Invert the report's risk band into the frontend's credibility band."""
    level = _as_risk_level(risk_level)
    trust = trust_level_for(level)
    return trust, TRUST_LABELS[trust]


def build_dimension_reads(rows: Sequence[AgentResult]) -> list[DimensionRead]:
    """Assemble the four-dimension view in canonical T/R/U/S order.

    Dimensions that were not requested still appear, marked ``assessed=false``,
    so the frontend's radar always has all four axis labels available.
    """
    by_key = {row.agent_name: row for row in rows}
    reads: list[DimensionRead] = []
    for key in DIMENSION_ORDER:
        meta = DIMENSION_META[key]
        row = by_key.get(key)
        if row is None:
            reads.append(
                DimensionRead(
                    key=key,
                    code=meta.code,
                    label=meta.label,
                    question=meta.question,
                    input_label=meta.input_label,
                    assessed=False,
                    summary="该维度未参与本次分析。",
                )
            )
            continue

        assessed = row.assessed
        risk_score = round(float(row.score), 1) if assessed else None
        risk_level = _risk_level(row, assessed)
        trust_level = trust_level_for(risk_level) if risk_level else None
        reads.append(
            DimensionRead(
                key=key,
                code=meta.code,
                label=meta.label,
                question=meta.question,
                input_label=meta.input_label,
                assessed=assessed,
                trust_score=trust_score_for(risk_score) if risk_score is not None else None,
                risk_score=risk_score,
                risk_level=risk_level,
                trust_level=trust_level,
                trust_label=TRUST_LABELS[trust_level] if trust_level else None,
                summary=row.summary or "",
                evidence=_findings_to_evidence(key, row.findings),
                limitations=_limitations(row.raw_output) if assessed else [],
            )
        )
    return reads


def build_agent_status_items(rows: Sequence[AgentResult]) -> list[AgentStatusItem]:
    """Lifecycle view for ``GET /api/status/{task_id}``, in row order."""
    items: list[AgentStatusItem] = []
    for row in rows:
        meta = DIMENSION_META.get(row.agent_name)
        assessed = row.assessed
        risk_score = round(float(row.score), 1) if assessed else None
        risk_level = _risk_level(row, assessed)
        trust_level = trust_level_for(risk_level) if risk_level else None
        items.append(
            AgentStatusItem(
                agent=row.agent_name,
                code=meta.code if meta else row.agent_name[:1].upper(),
                label=meta.label if meta else row.agent_name,
                status=TaskStatus(row.status),
                assessed=assessed,
                risk_level=risk_level,
                risk_score=risk_score,
                trust_level=trust_level,
                trust_score=trust_score_for(risk_score) if risk_score is not None else None,
                trust_label=TRUST_LABELS[trust_level] if trust_level else None,
                duration_ms=row.duration_ms,
                error_message=row.error_message,
            )
        )
    return items


# -- helpers -----------------------------------------------------------------


def _as_risk_level(value: RiskLevel | str) -> RiskLevel:
    """Coerce to :class:`RiskLevel`.

    ``str(RiskLevel.MEDIUM)`` is ``"RiskLevel.MEDIUM"`` on Python 3.11+, not
    ``"medium"``, so an already-built enum must be returned as-is rather than
    re-stringified — otherwise the conversion raises ``ValueError``.
    """
    if isinstance(value, RiskLevel):
        return value
    return RiskLevel(str(value))


def _risk_level(row: AgentResult, assessed: bool) -> RiskLevel | None:
    if not assessed or not row.risk_level:
        return None
    try:
        return _as_risk_level(row.risk_level)
    except ValueError:
        return None


def _limitations(raw: dict | None) -> list[str]:
    """Return only caveats that belong to this dimension.

    The engine stores two levels of caveats: ``dimension_limitations`` for the
    role that produced the finding, and ``limitations`` for the whole report.
    Showing both on every dimension repeats the global caveat block four times
    and buries the actual finding. Keep the role-specific notes when available;
    the report-level caveats remain visible in the report footer.
    """
    raw = raw or {}
    notes = [str(x) for x in (raw.get("dimension_limitations") or [])]
    if not notes:
        notes = [str(x) for x in (raw.get("limitations") or [])]
    return list(dict.fromkeys(note for note in notes if note))


def _findings_to_evidence(key: str, findings: list | None) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for raw in findings or []:
        if not isinstance(raw, dict):
            continue
        try:
            severity = _as_risk_level(raw.get("severity"))
        except ValueError:
            severity = RiskLevel.MEDIUM
        try:
            confidence = float(raw.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        items.append(
            EvidenceItem(
                source=key,
                type=str(raw.get("type") or "finding"),
                evidence=str(raw.get("evidence") or ""),
                confidence=max(0.0, min(1.0, confidence)),
                severity=severity,
                reason=raw.get("detail"),
            )
        )
    return items
