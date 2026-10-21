"""Server-side report reasoning: cross-checks, decision tiers and confidence."""

from __future__ import annotations

from collections.abc import Sequence

from app.core.enums import RiskLevel
from app.models.agent_result import AgentResult
from app.models.report import Report
from app.schemas.report import (
    CrossCheckRead,
    DecisionAdviceRead,
    DecisionRead,
    DimensionRead,
    ReportConfidenceRead,
    ReportRead,
)
from app.services.presentation import build_dimension_reads, overall_trust

CODES = ("T", "R", "U", "S")
PAIRS = (("T", "R"), ("T", "U"), ("T", "S"), ("R", "U"), ("R", "S"), ("U", "S"))
MATERIAL_LABELS = {"T": "账号材料", "R": "图片材料", "U": "评论材料", "S": "文案材料"}
DIMENSION_NAMES = {"T": "账号", "R": "图片", "U": "评论", "S": "文案"}


def build_cross_checks(dimensions: Sequence[DimensionRead]) -> list[CrossCheckRead]:
    by_code = {dimension.code: dimension for dimension in dimensions}
    assessed = [code for code in CODES if by_code.get(code) and by_code[code].assessed]
    if not assessed:
        return []

    if len(assessed) == 1:
        code = assessed[0]
        dimension = by_code[code]
        score = float(dimension.trust_score or 0)
        level = "consistent" if score >= 70 else "doubtful"
        return [
            CrossCheckRead(
                id=f"intra-{code.lower()}",
                from_code=code,
                to_code=code,
                left=f"{dimension.label}：{score:.0f} / 100",
                right=f"{len(dimension.evidence)} 条证据 · {dimension.trust_label or '已评估'}",
                conclusion=(
                    f"{dimension.label}当前未呈现明显高风险"
                    if level == "consistent"
                    else f"{dimension.label}存在需要留意的风险信号"
                ),
                state=level,
                dimension=f"{DIMENSION_NAMES[code]}维度内",
                detail=(
                    f"{dimension.summary} 本次仅提供{DIMENSION_NAMES[code]}材料，"
                    "因此先展示维度内结论，不推断其他模态。"
                ),
                confidence=_confidence_for(dimension),
                method="llm" if dimension.evidence else "rule",
                label="实时核验",
            )
        ]

    items: list[CrossCheckRead] = []
    for left_code, right_code in PAIRS:
        left = by_code.get(left_code)
        right = by_code.get(right_code)
        available = bool(left and right and left.assessed and right.assessed)
        if not available:
            missing = [
                MATERIAL_LABELS[code]
                for code in (left_code, right_code)
                if not by_code.get(code) or not by_code[code].assessed
            ]
            items.append(
                CrossCheckRead(
                    id=f"{left_code.lower()}-{right_code.lower()}",
                    from_code=left_code,
                    to_code=right_code,
                    left=f"{DIMENSION_NAMES[left_code]}材料",
                    right=f"{DIMENSION_NAMES[right_code]}材料",
                    conclusion=f"缺少{'、'.join(missing)}，当前无法比对",
                    state="unverified",
                    dimension=f"{DIMENSION_NAMES[left_code]} × {DIMENSION_NAMES[right_code]}",
                    detail=f"本次报告未完成{'、'.join(missing)}维度评估，因此不生成该组合的方向性结论。",
                    label="等待材料",
                )
            )
            continue

        left_score = float(left.trust_score or 0)
        right_score = float(right.trust_score or 0)
        gap = abs(left_score - right_score)
        both_trusted = left_score >= 70 and right_score >= 70
        both_risky = left_score < 50 and right_score < 50
        direction_conflict = (left_score >= 70 and right_score < 50) or (
            right_score >= 70 and left_score < 50
        )
        state = (
            "conflict"
            if direction_conflict
            else "corroborated"
            if both_trusted or both_risky
            else "consistent"
            if gap <= 18
            else "doubtful"
        )
        conclusion = {
            "conflict": "两个维度的可信方向不一致",
            "corroborated": "两个维度同时出现风险信号"
            if both_risky
            else "两个维度均呈现较高可信度",
            "consistent": "两个维度暂未呈现明显冲突",
            "doubtful": "两个维度存在明显分差，建议复核",
        }[state]
        evidence_count = len(left.evidence) + len(right.evidence)
        items.append(
            CrossCheckRead(
                id=f"{left_code.lower()}-{right_code.lower()}",
                from_code=left_code,
                to_code=right_code,
                left=f"{left.label}：{left_score:.0f} / 100",
                right=f"{right.label}：{right_score:.0f} / 100",
                conclusion=conclusion,
                state=state,
                dimension=f"{DIMENSION_NAMES[left_code]} × {DIMENSION_NAMES[right_code]}",
                detail=(
                    f"本次报告显示{DIMENSION_NAMES[left_code]}可信度 {left_score:.0f}，"
                    f"{DIMENSION_NAMES[right_code]}可信度 {right_score:.0f}，分差 {gap:.0f}。"
                    f"该关系基于分数、风险等级和 {evidence_count} 条证据计算，"
                    "不替代进一步的人工事实核验。"
                ),
                confidence=_confidence_for(left, right) if gap <= 18 else (
                    "中" if evidence_count >= 4 else "低"
                ),
                method="llm" if evidence_count else "rule",
                label="实时比对",
            )
        )
    return items


def build_decision(report: Report, dimensions: Sequence[DimensionRead]) -> DecisionRead:
    assessed = [dimension for dimension in dimensions if dimension.assessed]
    tier = (
        "pass"
        if report.trust_score >= 80
        else "verify"
        if report.trust_score >= 60
        else "review"
    )
    tier_label = {
        "pass": "放行 · 轻提示",
        "verify": "提示 · 补充核验",
        "review": "人工复核 · 处置",
    }[tier]
    top = max(
        assessed,
        key=lambda dimension: dimension.risk_score if dimension.risk_score is not None else -1,
        default=None,
    )
    headline = (
        "输入不足以完成四维交叉验证。"
        if not top
        else f"最需要留意的维度：{top.code} · {top.label}。"
        if len(assessed) > 1
        else f"当前仅完成 {top.code} · {top.label} 单维核验。"
    )
    limitations = list(
        dict.fromkeys(
            limitation for dimension in assessed for limitation in dimension.limitations
        )
    )
    report_recommendations = [str(item) for item in (report.recommendations or []) if item]
    fallback = "建议补充更多模态材料后再作结论。" if len(assessed) < 4 else "当前未发现新的处置建议。"
    top_basis = (
        f"{top.label}风险分 {top.risk_score:.1f}/100。"
        if top and top.risk_score is not None
        else "当前评估材料有限。"
    )

    advice: list[DecisionAdviceRead] = []
    for index, text in enumerate(report_recommendations[:4]):
        advice.append(
            DecisionAdviceRead(
                perspective="consumer",
                priority="重点" if index == 0 else "建议",
                title=text,
                basis="来自本次报告的后端建议。",
            )
        )
    if not any(item.perspective == "consumer" for item in advice):
        advice.append(
            DecisionAdviceRead(
                perspective="consumer",
                priority="重点",
                title=fallback,
                basis=top_basis,
            )
        )

    advice.extend(
        [
            DecisionAdviceRead(
                perspective="creator",
                priority="重点" if tier != "pass" else "建议",
                title=(
                    report_recommendations[0]
                    if report_recommendations
                    else "发布前复核高风险表述，并保留可核验的原始材料。"
                ),
                basis=top_basis,
            ),
            DecisionAdviceRead(
                perspective="platform",
                priority="重点" if tier == "review" else "建议",
                title={
                    "pass": "可放行并保留轻提示。",
                    "verify": "建议加提示标注并补充材料后复核。",
                    "review": "建议进入人工复核，必要时限流或下架。",
                }[tier],
                basis=f"当前分级：{tier_label}。",
            ),
            DecisionAdviceRead(
                perspective="brand",
                priority="重点" if tier != "pass" else "建议",
                title={
                    "pass": "可作为合作候选，保留核验记录。",
                    "verify": "合作前索取原始素材和功效依据。",
                    "review": "暂缓合作，等待人工复核或补充证据。",
                }[tier],
                basis=top_basis,
            ),
        ]
    )
    return DecisionRead(
        tier=tier,
        label=tier_label,
        headline=headline,
        advice=advice,
        limitations=limitations,
    )


def build_confidence(dimensions: Sequence[DimensionRead], cross_checks: Sequence[CrossCheckRead]) -> ReportConfidenceRead:
    assessed = [dimension for dimension in dimensions if dimension.assessed]
    count = len(assessed)
    level = "high" if count == 4 else "medium" if count >= 2 else "low"
    label = {"high": "高", "medium": "中", "low": "低"}[level]
    evidence_count = len({item.evidence for dimension in assessed for item in dimension.evidence})
    return ReportConfidenceRead(
        level=level,
        label=label,
        bars={"high": 3, "medium": 2, "low": 1}[level],
        assessed_count=count,
        evidence_count=evidence_count,
        cross_check_count=len([item for item in cross_checks if item.state != "unverified"]),
        missing_materials=[MATERIAL_LABELS[code] for code in CODES if code not in {item.code for item in assessed}],
    )


def build_report_read(report: Report, rows: Sequence[AgentResult]) -> ReportRead:
    trust_level, trust_label = overall_trust(RiskLevel(report.risk_level))
    dimensions = build_dimension_reads(rows)
    cross_checks = build_cross_checks(dimensions)
    decision = build_decision(report, dimensions)
    confidence = build_confidence(dimensions, cross_checks)
    return ReportRead(
        id=report.id,
        task_id=report.task_id,
        overall_score=report.overall_score,
        trust_score=report.trust_score,
        risk_level=report.risk_level,
        trust_level=trust_level,
        trust_label=trust_label,
        summary=report.summary,
        evidence=report.evidence or [],
        recommendations=report.recommendations or [],
        dimension_scores=report.dimension_scores or {},
        dimensions=dimensions,
        cross_checks=cross_checks,
        decision=decision,
        confidence=confidence,
        assessed_dimensions=[row.agent_name for row in rows if row.assessed],
        generated_at=report.generated_at,
        created_at=report.created_at,
    )


def _confidence_for(*dimensions: DimensionRead) -> str:
    minimum = min((len(dimension.evidence) for dimension in dimensions), default=0)
    return "高" if minimum >= 3 else "中" if minimum >= 1 else "低"
