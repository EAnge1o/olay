"""Text claim analysis with a deterministic offline fallback."""

import re

from content_trust.providers.base import AITextDetectorProvider, ClaimAnalysisProvider
from content_trust.schemas import (
    AITextSignal,
    Claim,
    ClaimEvidenceAlignment,
    EvidenceItem,
    Finding,
    TextIntegrityAnalysis,
    TextIntegritySignal,
    TextAnalysisResult,
)

_CLAUSE_SPLIT = re.compile(r"(?<=[。！？!?；;,，])")
_ABSOLUTE_TERMS = re.compile(r"绝对|百分之百|100%|百分百|彻底|永不|保证|一定有效|完全修复")
_MEDICAL_TERMS = re.compile(r"医学级|治疗|治愈|消炎|处方级|医疗级|细胞再生")
_ENDORSEMENT_TERMS = re.compile(r"权威机构|专家认证|官方认证|医院认证|医生推荐|权威验证")
_SAFETY_TERMS = re.compile(r"不过敏|无副作用|零风险|安全无害|不会过敏")
_TIME_TERMS = re.compile(r"\d+\s*(?:天|周|个月|月|小时|日)")
_NUMERIC_TERMS = re.compile(r"\d+(?:\.\d+)?\s*%|\d+(?:\.\d+)?\s*(?:倍|毫克|mg|克|g)")
_EFFICACY_TERMS = re.compile(r"淡纹|祛皱|美白|祛斑|修复|抗老|改善|提升|消除|缩小|收缩")
_PRODUCT_IDENTITY_TERMS = re.compile(
    r"产品名|商品名|品牌名|品牌是|产品叫|型号为|包装上(?:写着|标注)|"
    r"这款(?:产品|精华|面霜|乳液|口红|粉底)是"
)
_FACTUAL_TYPES = {
    "efficacy",
    "numeric",
    "time_bound",
    "medical",
    "safety",
    "ingredient",
    "endorsement",
    "comparative",
    "product_identity",
    "absolute",
}
_TEXT_SEGMENT_SPLIT = re.compile(r"[^。！？!?；;，,\n]+(?:[。！？!?；;，,\n]+|$)")
_REPETITION_SEGMENT_SPLIT = re.compile(r"[^。！？!?；;\n]+(?:[。！？!?；;\n]+|$)")
_SEGMENT_DECORATION = re.compile(r"[\s\u3000，,。！？!?；;、:：\"'“”‘’（）()]+")


class TextAnalyzer:
    def __init__(
        self,
        claim_provider: ClaimAnalysisProvider | None = None,
        ai_text_detector: AITextDetectorProvider | None = None,
    ) -> None:
        self._claim_provider = claim_provider
        self._ai_text_detector = ai_text_detector

    def analyze(
        self,
        text: str,
        evidence_items: list[EvidenceItem],
        *,
        locale: str = "zh-CN",
    ) -> TextAnalysisResult:
        provider_error = None
        model_reviewed = False
        if self._claim_provider is not None:
            try:
                localized = getattr(
                    self._claim_provider, "analyze_claims_localized", None
                )
                if callable(localized):
                    result = localized(text, evidence_items, locale=locale)
                else:
                    result = self._claim_provider.analyze_claims(text, evidence_items)
            except Exception as exc:
                provider_error = _error_code(exc)
                result = _rule_analysis(text, evidence_items)
            else:
                result = result.model_copy(deep=True)
                model_reviewed = True
        else:
            result = _rule_analysis(text, evidence_items)

        if provider_error:
            result.provider_error = provider_error

        if self._ai_text_detector is None:
            # DeepSeek may return a separate, uncalibrated style-suspicion signal
            # alongside claim analysis. It is not treated as provenance proof.
            signal = result.ai_text_signal
            if model_reviewed and signal is not None:
                if signal.available and signal.score is not None:
                    signal = signal.model_copy(
                        update={
                            "provider": "deepseek_style_review",
                            "score_semantics": (
                                "uncalibrated style-suspicion score; not a calibrated probability"
                            ),
                        }
                    )
                    result.ai_text_signal = signal
                    if signal.score >= 0.60:
                        result.findings.append(
                            _ai_style_finding(signal, source="llm")
                        )
                else:
                    result.ai_text_signal = signal.model_copy(
                        update={
                            "provider": "deepseek_style_review",
                            "score_semantics": (
                                "uncalibrated style-suspicion score; not a calibrated probability"
                            ),
                        }
                    )
            else:
                result.ai_text_signal = AITextSignal(
                    available=False,
                    reason="provider_not_configured",
                )
        else:
            try:
                result.ai_text_signal = self._ai_text_detector.detect_ai_text(text)
                signal = result.ai_text_signal
                ai_fraction = (
                    signal.ai_fraction if signal.ai_fraction is not None else signal.score
                )
                if signal.available and ai_fraction is not None and ai_fraction >= 0.5:
                    result.findings.append(
                        _ai_style_finding(signal, source="ai_text_detector")
                    )
            except Exception as exc:
                result.ai_text_signal = AITextSignal(
                    available=False,
                    reason=_error_code(exc),
                    provider="ai_text_detector",
                )

        integrity = _normalize_text_integrity(
            text,
            result.text_integrity,
            model_reviewed=model_reviewed,
        )
        # Text provenance/style cues are intentionally excluded from risk fusion.
        result.semantic_anomaly_score = None
        result.text_integrity = integrity
        result.style_anomalies = [signal.observation for signal in integrity.signals]
        result.findings.extend(_integrity_findings(integrity))
        return result


def _ai_style_finding(signal: AITextSignal, *, source: str) -> Finding:
    score = signal.ai_fraction if signal.ai_fraction is not None else signal.score
    score = score if score is not None else 0.0
    label = "AI 文风疑似度" if source == "llm" else "AI 文本检测器信号"
    cues = "；".join(signal.observed_cues[:3])
    explanation = signal.explanation or signal.reason
    reason_parts = [
        "这是来源/文风层面的弱信号，不能证明文本由 AI 生成，也不说明事实内容为假。"
    ]
    if explanation:
        reason_parts.append(explanation)
    if cues:
        reason_parts.append("观察线索：" + cues)
    return Finding(
        id="ai-text-signal",
        category="ai_generated_text",
        verdict="suspicious",
        severity="low",
        risk_score=0.2 if source == "ai_text_detector" else None,
        signal_score=score,
        score_semantics=(
            signal.score_semantics
            or "weak AI-style/provenance signal; not proof of authorship"
        ),
        confidence=signal.confidence,
        observation=f"{label}为 {score:.2f}/1.00。",
        reason=" ".join(reason_parts),
        source=source,
        recommended_action=(
            "仅作为文风线索；如需确认作者来源，应结合原稿、版本记录或创作者说明。"
        ),
    )


def _error_code(exc: Exception) -> str:
    return getattr(exc, "code", "provider_error")


def _rule_analysis(text: str, evidence_items: list[EvidenceItem]) -> TextAnalysisResult:
    clauses = _atomic_clauses(text)
    claims: list[Claim] = []
    findings: list[Finding] = []
    for index, clause in enumerate(clauses, start=1):
        claim_type = _infer_claim_type(clause)
        status = "insufficient_evidence" if claim_type in _FACTUAL_TYPES else None
        # The offline fallback does not attach evidence by keyword overlap. The
        # evidence-aware provider is responsible for citing direct support.
        evidence_ids: list[str] = []

        claims.append(
            Claim(
                id=f"claim-{index}",
                text=clause,
                claim_type=claim_type,
                verification_status=status,
                risk_score=_claim_risk(clause),
                confidence=None,
                evidence_ids=evidence_ids,
                observation=clause,
                reason=(
                    "Rule-based extraction only; factual verification requires evidence-aware review."
                    if status == "insufficient_evidence"
                    else "No high-risk factual pattern matched."
                ),
                metric=_claim_metric(clause),
                value=_claim_value(clause),
                unit=_claim_unit(clause),
                duration=_claim_duration(clause),
                evidence_required=claim_type in _FACTUAL_TYPES,
                evidence_alignment=(
                    ClaimEvidenceAlignment(
                        status="insufficient_evidence",
                        reason=(
                            "The offline fallback does not align a claim to evidence; "
                            "no direct match was established."
                        ),
                    )
                    if status == "insufficient_evidence"
                    else None
                ),
            )
        )

        if _ABSOLUTE_TERMS.search(clause):
            findings.append(
                _finding(
                    f"absolute-{index}",
                    category="exaggerated_claim",
                    verdict="suspicious",
                    severity="high",
                    risk_score=0.88,
                    observation=clause,
                    reason="Contains absolute or guaranteed efficacy wording.",
                    action="人工核对该承诺是否有适用范围明确的临床或产品证据。",
                )
            )
        if _MEDICAL_TERMS.search(clause):
            findings.append(
                _finding(
                    f"medical-{index}",
                    category="exaggerated_claim",
                    verdict="suspicious",
                    severity="high",
                    risk_score=0.82,
                    observation=clause,
                    reason="Uses medicalized treatment or regeneration language.",
                    action="人工核查该表达的证据和广告合规性。",
                )
            )
        if _ENDORSEMENT_TERMS.search(clause):
            findings.append(
                _finding(
                    f"endorsement-{index}",
                    category="fake_endorsement_risk",
                    verdict="insufficient_evidence",
                    severity="medium",
                    risk_score=0.68,
                    observation=clause,
                    reason="Authority or official endorsement is asserted but is not verified by this wording alone.",
                    evidence_ids=evidence_ids,
                    action="核实背书机构、授权关系和可公开引用的证明材料。",
                )
            )
        if _SAFETY_TERMS.search(clause):
            findings.append(
                _finding(
                    f"safety-{index}",
                    category="exaggerated_claim",
                    verdict="suspicious",
                    severity="high",
                    risk_score=0.86,
                    observation=clause,
                    reason="Makes an absolute safety or no-allergy promise.",
                    action="要求提供适用人群、测试条件和不良反应资料，并由人工复核。",
                )
            )
        if _NUMERIC_TERMS.search(clause) and _EFFICACY_TERMS.search(clause):
            findings.append(
                _finding(
                    f"quantified-efficacy-{index}",
                    category="unsupported_claim",
                    verdict="insufficient_evidence",
                    severity="medium",
                    risk_score=0.62,
                    observation=clause,
                    reason="Quantified efficacy requires a matching test result and methodology.",
                    evidence_ids=evidence_ids,
                    action="索取与该数值、时间和产品版本相匹配的研究资料。",
                )
            )

    return TextAnalysisResult(
        claims=claims,
        findings=findings,
        # Claim wording risks are already represented by claim findings. They are
        # not a semantic-consistency or text-provenance score.
        semantic_anomaly_score=None,
    )


def _normalize_text_integrity(
    text: str,
    analysis: TextIntegrityAnalysis,
    *,
    model_reviewed: bool,
) -> TextIntegrityAnalysis:
    normalized: list[TextIntegritySignal] = []
    seen: set[tuple[str, int, int, int, int]] = set()

    for signal in analysis.signals:
        first = _locate_exact_span(text, signal.span)
        if first is None:
            continue
        related = None
        if signal.related_span:
            related = _locate_exact_span(
                text,
                signal.related_span,
                after=first[1] if signal.related_span == signal.span else 0,
            )
            if related is None and signal.related_span != signal.span:
                related = _locate_exact_span(text, signal.related_span)
        if related is None or _overlaps(first, related):
            continue

        ordered_spans = sorted((first, related))
        key = (
            signal.signal_type,
            ordered_spans[0][0],
            ordered_spans[0][1],
            ordered_spans[1][0],
            ordered_spans[1][1],
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            TextIntegritySignal(
                id=f"text-integrity-{len(normalized) + 1}",
                signal_type=signal.signal_type,
                span=signal.span,
                related_span=signal.related_span,
                start_char=first[0],
                end_char=first[1],
                related_start_char=related[0],
                related_end_char=related[1],
                observation=signal.observation,
                reason=signal.reason,
                benign_alternative=signal.benign_alternative,
                strength=signal.strength,
                source="llm" if model_reviewed else "rule",
            )
        )

    for signal in _rule_repetition_signals(text):
        first = (signal.start_char, signal.end_char)
        related = (signal.related_start_char, signal.related_end_char)
        if None in (*first, *related):
            continue
        ordered_spans = sorted((first, related))
        key = (
            signal.signal_type,
            ordered_spans[0][0],
            ordered_spans[0][1],
            ordered_spans[1][0],
            ordered_spans[1][1],
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(signal)

    if normalized:
        status = "signals_found"
    elif len(_text_segments(text)) < 2:
        status = "insufficient_context"
    else:
        status = "no_clear_signal"
    return TextIntegrityAnalysis(
        status=status,
        signals=normalized,
        model_reviewed=model_reviewed,
    )


def _rule_repetition_signals(text: str) -> list[TextIntegritySignal]:
    first_by_normalized: dict[str, tuple[str, int, int]] = {}
    signals: list[TextIntegritySignal] = []
    for segment, start, end in _text_segments(text, _REPETITION_SEGMENT_SPLIT):
        normalized = _SEGMENT_DECORATION.sub("", segment)
        if len(normalized) < 16:
            continue
        previous = first_by_normalized.get(normalized)
        if previous is None:
            first_by_normalized[normalized] = (segment, start, end)
            continue
        previous_text, previous_start, previous_end = previous
        signals.append(
            TextIntegritySignal(
                id=f"text-integrity-rule-repetition-{len(signals) + 1}",
                signal_type="repetition",
                span=previous_text,
                related_span=segment,
                start_char=previous_start,
                end_char=previous_end,
                related_start_char=start,
                related_end_char=end,
                observation="文案中有两处较长句段重复，新增信息有限。",
                reason="重复表达值得复核，但单独出现不能说明文本由 AI 生成或被拼接。",
                benign_alternative="可能是有意强调、复制粘贴或引用品牌话术。",
                strength="weak",
                source="rule",
            )
        )
    return signals


def _text_segments(
    text: str,
    pattern: re.Pattern[str] = _TEXT_SEGMENT_SPLIT,
) -> list[tuple[str, int, int]]:
    segments: list[tuple[str, int, int]] = []
    for match in pattern.finditer(text):
        raw = match.group(0)
        left_trim = len(raw) - len(raw.lstrip())
        segment = raw.strip()
        if not segment:
            continue
        start = match.start() + left_trim
        segments.append((segment, start, start + len(segment)))
    return segments


def _locate_exact_span(text: str, quote: str, *, after: int = 0) -> tuple[int, int] | None:
    if not quote:
        return None
    start = text.find(quote, after)
    if start < 0 and after:
        start = text.find(quote)
    return (start, start + len(quote)) if start >= 0 else None


def _overlaps(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return first[0] < second[1] and second[0] < first[1]


def _integrity_findings(analysis: TextIntegrityAnalysis) -> list[Finding]:
    findings = []
    for signal in analysis.signals:
        strength_score = 0.30 if signal.strength == "moderate" else 0.15
        findings.append(
            Finding(
                id=f"finding-{signal.id}",
                category="semantic_anomaly",
                verdict="unknown",
                severity="low" if signal.strength == "moderate" else "info",
                risk_score=strength_score,
                confidence=None,
                observation=signal.observation,
                reason=(
                    f"观察片段：‘{signal.span}’；对照片段：‘{signal.related_span}’。"
                    f"{signal.reason}可能的非异常解释：{signal.benign_alternative}"
                    "该线索不能确认 AI 生成或人工/AI 编辑。"
                ),
                evidence_ids=[],
                source=signal.source,
                recommended_action=(
                    "如需判断是否发生后期改写，请与原稿、版本记录或引用来源对照；"
                    "单篇最终文本不足以确认编辑历史。"
                ),
            )
        )
    return findings


def _atomic_clauses(text: str) -> list[str]:
    # A measured outcome with its duration and magnitude is one atomic,
    # independently testable claim. Keep the details in structured fields
    # instead of splitting into overlapping fragments such as "7 days" and
    # "80%" that are not meaningful claims on their own.
    return [part.strip() for part in _CLAUSE_SPLIT.split(text) if part.strip()]


def _infer_claim_type(text: str) -> str:
    if _SAFETY_TERMS.search(text):
        return "safety"
    if _ENDORSEMENT_TERMS.search(text):
        return "endorsement"
    if _MEDICAL_TERMS.search(text):
        return "medical"
    if _TIME_TERMS.search(text):
        return "time_bound"
    if _NUMERIC_TERMS.search(text):
        return "numeric"
    if _EFFICACY_TERMS.search(text):
        return "efficacy"
    if _ABSOLUTE_TERMS.search(text):
        return "absolute"
    if re.search(r"成分|配方|含有|添加", text):
        return "ingredient"
    if _PRODUCT_IDENTITY_TERMS.search(text):
        return "product_identity"
    return "other"


def _claim_risk(text: str) -> float:
    if _SAFETY_TERMS.search(text) or _ABSOLUTE_TERMS.search(text):
        return 0.75
    if _MEDICAL_TERMS.search(text) or _ENDORSEMENT_TERMS.search(text):
        return 0.65
    if _NUMERIC_TERMS.search(text) and _EFFICACY_TERMS.search(text):
        return 0.55
    return 0.10


def _claim_metric(text: str) -> str | None:
    terms = ["淡纹", "皱纹", "美白", "保湿", "痘印", "毛孔", "过敏", "刺激"]
    return next((term for term in terms if term in text), None)


def _claim_value(text: str) -> str | None:
    match = _NUMERIC_TERMS.search(text)
    return match.group(0).strip() if match else None


def _claim_unit(text: str) -> str | None:
    value = _claim_value(text)
    if value is None:
        return None
    for unit in ("%", "倍", "毫克", "mg", "克", "g"):
        if unit in value:
            return unit
    return None


def _claim_duration(text: str) -> str | None:
    match = _TIME_TERMS.search(text)
    return match.group(0).strip() if match else None


def _finding(
    finding_id: str,
    *,
    category: str,
    verdict: str,
    severity: str,
    risk_score: float,
    observation: str,
    reason: str,
    action: str,
    evidence_ids: list[str] | None = None,
) -> Finding:
    return Finding(
        id=finding_id,
        category=category,
        verdict=verdict,
        severity=severity,
        risk_score=risk_score,
        observation=observation,
        reason=reason,
        evidence_ids=evidence_ids or [],
        source="rule",
        recommended_action=action,
    )
