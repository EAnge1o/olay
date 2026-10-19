"""Explain normalized evidence and review priority without a truth score."""

from content_trust.providers.base import ExplanationProvider
from content_trust.schemas import (
    CommentAnalysisResult,
    EvidenceCard,
    EvidenceItem,
    ExplanationResult,
    Finding,
    ImageTrustResult,
)


class ExplanationBuilder:
    def __init__(self, provider: ExplanationProvider | None = None) -> None:
        self._provider = provider

    def build(
        self,
        *,
        findings: list[Finding],
        evidence_items: list[EvidenceItem],
        review_priority: str,
        evidence_cards: list[EvidenceCard],
        image_results: list[ImageTrustResult] | None = None,
        comment_analysis: CommentAnalysisResult | None = None,
        locale: str = "zh-CN",
    ) -> tuple[ExplanationResult, str | None]:
        if self._provider is not None:
            try:
                localized = getattr(self._provider, "explain_localized", None)
                if callable(localized):
                    result = localized(
                        findings=findings,
                        evidence_items=evidence_items,
                        image_results=image_results or [],
                        comment_analysis=comment_analysis,
                        review_priority=review_priority,
                        evidence_cards=evidence_cards,
                        locale=locale,
                    )
                else:
                    result = self._provider.explain(
                        findings=findings,
                        evidence_items=evidence_items,
                        image_results=image_results or [],
                        comment_analysis=comment_analysis,
                        review_priority=review_priority,
                        evidence_cards=evidence_cards,
                    )
                return result, None
            except Exception as exc:
                error = getattr(exc, "code", "provider_error")
        else:
            error = None
        return (
            _deterministic_explanation(
                findings,
                evidence_items,
                review_priority,
                evidence_cards,
                comment_analysis,
            ),
            error,
        )


def _deterministic_explanation(
    findings: list[Finding],
    evidence_items: list[EvidenceItem],
    review_priority: str,
    evidence_cards: list[EvidenceCard],
    comment_analysis: CommentAnalysisResult | None,
) -> ExplanationResult:
    evidence_by_id = {item.id: item for item in evidence_items}
    observations: list[str] = []
    cited_evidence: list[str] = []
    explanations: list[str] = []
    actions: list[str] = []

    for finding in findings:
        if finding.observation:
            observations.append(finding.observation)
        if finding.reason:
            explanations.append(finding.reason)
        if finding.recommended_action:
            actions.append(finding.recommended_action)
        for evidence_id in finding.evidence_ids:
            item = evidence_by_id.get(evidence_id)
            if item is not None:
                cited_evidence.append(f"{evidence_id} ({item.source}): {item.text}")

    card_lines = [
        f"{card.dimension}={card.status}: " + "；".join(card.observations)
        for card in evidence_cards
        if card.observations
    ]
    parts = [f"人工复核优先级：{review_priority}。该优先级不是真假结论。"]
    if observations:
        parts.append("观察到：" + "；".join(_unique(observations)))
    if cited_evidence:
        parts.append("引用证据：" + "；".join(_unique(cited_evidence)))
    if card_lines:
        parts.append("独立证据维度：" + "；".join(_unique(card_lines)))
    if explanations:
        parts.append("解释与局限：" + "；".join(_unique(explanations)))

    if comment_analysis is not None:
        if comment_analysis.available:
            review_scope = (
                f"DeepSeek 复核 {comment_analysis.llm_input_count} 个压缩模式簇。"
                if comment_analysis.model_reviewed
                else "仅运行本地文本规则，没有 DeepSeek 模式复核。"
            )
            parts.append(
                "评论文本模式："
                + comment_analysis.summary
                + f"；覆盖 {comment_analysis.total_comment_count} 条评论；"
                + review_scope
            )
            actions.extend(
                cluster.recommended_action
                for cluster in comment_analysis.pattern_clusters
                if cluster.recommended_action
            )
        else:
            parts.append("评论模式分析不可用；没有据此判断评论真假。")
        if comment_analysis.sample_scope:
            parts.append("评论结论范围：" + comment_analysis.sample_scope)

    if not findings and not card_lines:
        parts.append("当前没有产生可报告的具体 finding；这不等于内容已验证真实。")
    if not actions:
        actions.append("保留原始素材、编辑历史和相关研究资料，必要时安排人工复核。")

    return ExplanationResult(
        summary="".join(parts),
        recommended_actions=_unique(actions),
    )


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in items if item.strip()))
