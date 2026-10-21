"""Report Agent — fuses Account / Image / Comment / Text outputs into one verdict.

Fusion model: a weighted risk average over the dimensions that were actually
*assessed* (weights renormalized when a dimension is missing), then a curated
evidence set ranked by severity × confidence, then deduplicated recommendations
plus risk-level-specific advice.

Only ``assessed`` dimensions take part. A dimension that ran but had nothing to
judge (``not_applicable`` / ``insufficient_data`` / ``unavailable``) carries a
placeholder score of 0 — including it would silently drag the average towards
"safe", which is the opposite of what "we could not check this" means.
"""

from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.enums import DIMENSION_ORDER, MEDIUM_RISK_MAX, AgentName, RiskLevel
from app.schemas.agent import AgentInput, AgentOutput
from app.schemas.report import EvidenceItem, ReportContent

# Dimension weights, identical to the engine's defaults so both backends
# aggregate the same way: text .35 / image .30 / comment .20 / account .15.
DIMENSION_WEIGHTS: dict[str, float] = {
    AgentName.TEXT.value: 0.35,
    AgentName.IMAGE.value: 0.30,
    AgentName.COMMENT.value: 0.20,
    AgentName.ACCOUNT.value: 0.15,
}

SEVERITY_RANK = {RiskLevel.HIGH: 3, RiskLevel.MEDIUM: 2, RiskLevel.LOW: 1}
MAX_EVIDENCE = 12

# Neutral midpoint used only when *no* dimension could be assessed: the report
# stays inside the "存疑" band and its summary says so, rather than reporting a
# perfect 0-risk score from data it never managed to read.
NEUTRAL_RISK = MEDIUM_RISK_MAX / 2

RISK_LEVEL_ADVICE: dict[RiskLevel, str] = {
    RiskLevel.HIGH: "内容存在高风险可信度问题，建议立即下架/整改后再发布，并保留核验记录。",
    RiskLevel.MEDIUM: "内容存在中等风险，建议按证据逐条修订文案与评论策略，复审后发布。",
    RiskLevel.LOW: "内容可信度较高，建议保持真实使用细节与合规披露。",
}


class ReportAgent(BaseAgent[ReportContent]):
    """Not a keyword detector — a fuser. Consumes the other agents' outputs.

    Reads ``payload.options["agent_results"]`` — a ``{agent_name: AgentOutput}``
    map assembled by the analysis service.
    """

    name = AgentName.REPORT.value
    display_name = "报告 Agent"
    description = "融合各 Agent 结果，输出结构化可信度报告"

    async def analyze(self, payload: AgentInput) -> ReportContent:
        results: dict[str, AgentOutput] = payload.options.get("agent_results") or {}
        return self.synthesize(results, use_llm=self.wants_llm(payload))

    def synthesize(
        self, results: dict[str, AgentOutput], *, use_llm: bool = False
    ) -> ReportContent:
        available = {
            name: out
            for name, out in results.items()
            if out is not None and out.meta.get("engine_status", "assessed") == "assessed"
        }
        dimension_scores = {name: out.score for name, out in available.items()}

        if not available:
            return self._unassessable(results)

        overall = self._weighted_overall(available)
        trust = round(100.0 - overall, 1)
        risk_level = RiskLevel.from_score(overall)

        evidence = self._select_evidence(available)
        recommendations = self._merge_recommendations(available, risk_level)
        summary = self._summary(overall, risk_level, available, evidence)

        return ReportContent(
            overall_score=overall,
            trust_score=trust,
            risk_level=risk_level,
            summary=summary,
            evidence=evidence,
            recommendations=recommendations,
            dimension_scores=dimension_scores,
            assessed_dimensions=list(available),
        )

    def _unassessable(self, results: dict[str, AgentOutput]) -> ReportContent:
        """No dimension produced a verdict — say so instead of inventing one."""
        skipped = [name for name, out in results.items() if out is not None]
        skipped_text = "、".join(skipped) or "无"
        return ReportContent(
            overall_score=NEUTRAL_RISK,
            trust_score=round(100.0 - NEUTRAL_RISK, 1),
            risk_level=RiskLevel.from_score(NEUTRAL_RISK),
            summary=(
                f"本次输入不足以核验任何维度（已尝试：{skipped_text}）。"
                "当前分数为中性占位，不代表内容可信；请补充文案、评论、图片或完整的账号历史后重新核验。"
            ),
            evidence=[],
            recommendations=[
                "补充完整的账号历史（≥3 篇）、评论、文案或图片后重新提交核验。",
            ],
            dimension_scores={},
            assessed_dimensions=[],
        )

    # -- Fusion steps -------------------------------------------------------

    @staticmethod
    def _weighted_overall(available: dict[str, AgentOutput]) -> float:
        if not available:
            return 0.0
        total_weight = sum(DIMENSION_WEIGHTS.get(name, 0.2) for name in available)
        if total_weight <= 0:
            return 0.0
        weighted = sum(
            DIMENSION_WEIGHTS.get(name, 0.2) * out.score for name, out in available.items()
        )
        return round(weighted / total_weight, 1)

    @staticmethod
    def _select_evidence(available: dict[str, AgentOutput]) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        for name, out in available.items():
            for finding in out.findings:
                items.append(
                    EvidenceItem(
                        source=name,
                        type=finding.type,
                        evidence=finding.evidence,
                        confidence=finding.confidence,
                        severity=finding.severity,
                        reason=finding.detail,
                    )
                )
        items.sort(
            key=lambda e: (SEVERITY_RANK.get(e.severity, 0), e.confidence),
            reverse=True,
        )
        return items[:MAX_EVIDENCE]

    @staticmethod
    def _merge_recommendations(
        available: dict[str, AgentOutput], risk_level: RiskLevel
    ) -> list[str]:
        ordered: list[str] = []
        # Highest-weight dimensions first, then report-level advice, then rest.
        for name in DIMENSION_ORDER:
            out = available.get(name)
            if out:
                ordered.extend(out.recommendations)
        ordered.append(RISK_LEVEL_ADVICE[risk_level])
        return list(dict.fromkeys(ordered))

    @staticmethod
    def _summary(
        overall: float,
        risk_level: RiskLevel,
        available: dict[str, AgentOutput],
        evidence: list[EvidenceItem],
    ) -> str:
        label = {RiskLevel.HIGH: "高风险", RiskLevel.MEDIUM: "中等风险", RiskLevel.LOW: "低风险"}[risk_level]
        dims = "、".join(
            f"{name} {out.score:.0f}" for name, out in available.items()
        ) or "无可用维度"
        high = sum(1 for e in evidence if e.severity == RiskLevel.HIGH)
        return (
            f"综合可信度核验结论：{label}（风险分 {overall}/100，可信分 {100 - overall:.0f}）。"
            f"各维度风险分：{dims}。共提取 {len(evidence)} 条证据，其中高风险 {high} 条。"
        )
