"""Build separate evidence cards and a conservative human-review priority."""

from content_trust.schemas import (
    ClaimEvidenceSummary,
    CommentAnalysisResult,
    CrossModalResult,
    EvidenceCard,
    Finding,
    ImageForensicsResult,
    ProvenanceAnalysis,
    TextAnalysisResult,
)


def claim_evidence_summary(
    text_analysis: TextAnalysisResult | None,
) -> ClaimEvidenceSummary:
    if text_analysis is None:
        return ClaimEvidenceSummary()
    factual = [claim for claim in text_analysis.claims if claim.evidence_required]
    return ClaimEvidenceSummary(
        total_claims=len(text_analysis.claims),
        evidence_required=len(factual),
        supported=sum(claim.verification_status == "supported" for claim in factual),
        contradicted=sum(
            claim.verification_status == "contradicted" for claim in factual
        ),
        insufficient_evidence=sum(
            claim.verification_status == "insufficient_evidence" for claim in factual
        ),
        not_assessed=sum(claim.verification_status is None for claim in factual),
    )


def build_evidence_cards(
    *,
    provenance_analysis: ProvenanceAnalysis | None = None,
    provenance_analyses: list[ProvenanceAnalysis] | None = None,
    image_analyses: list[ImageForensicsResult] | None = None,
    crossmodal_analyses: list[CrossModalResult] | None = None,
    text_analysis: TextAnalysisResult | None = None,
    comment_analysis: CommentAnalysisResult | None = None,
) -> list[EvidenceCard]:
    cards: list[EvidenceCard] = []
    provenances = provenance_analyses or (
        [provenance_analysis] if provenance_analysis is not None else []
    )
    for index, analysis in enumerate(provenances, start=1):
        cards.append(
            _provenance_card(
                analysis,
                title_suffix=f" (image {index})" if len(provenances) > 1 else "",
            )
        )

    images = image_analyses or []
    if images:
        cards.append(_visual_card(images))

    crossmodal = crossmodal_analyses or []
    if crossmodal:
        observations = []
        for index, result in enumerate(crossmodal, start=1):
            observations.extend(
                f"图片 {index}: {item.observation}"
                for item in result.observations
            )
            if result.summary:
                observations.append(f"图片 {index}: {result.summary}")
        statuses = [item.status for item in crossmodal]
        status = (
            "MISMATCH"
            if "MISMATCH" in statuses
            else "UNCERTAIN"
            if "UNCERTAIN" in statuses
            else "NOT_VERIFIABLE"
            if all(item == "NOT_VERIFIABLE" for item in statuses)
            else "PARTIAL"
            if "NOT_VERIFIABLE" in statuses
            else "MATCH"
        )
        cards.append(
            EvidenceCard(
                dimension="semantic_consistency",
                status=status,
                title="Image and text consistency",
                observations=observations,
                limitations=[
                    "This checks visible semantic details only; it cannot establish pixel editing or efficacy over time."
                ],
            )
        )

    claim_summary = claim_evidence_summary(text_analysis)
    if text_analysis is not None:
        cards.append(
            EvidenceCard(
                dimension="claim_evidence",
                status=_claim_status(claim_summary),
                title="Claim evidence alignment",
                observations=[
                    (
                        f"{claim_summary.supported} supported, "
                        f"{claim_summary.contradicted} contradicted, "
                        f"{claim_summary.insufficient_evidence} insufficient, "
                        f"{claim_summary.not_assessed} not assessed among "
                        f"{claim_summary.evidence_required} evidence-requiring claims."
                    ),
                    *[
                        _claim_observation(claim)
                        for claim in text_analysis.claims
                        if claim.evidence_required
                    ],
                ],
                evidence_ids=list(
                    dict.fromkeys(
                        evidence_id
                        for claim in text_analysis.claims
                        for evidence_id in claim.evidence_ids
                    )
                ),
                limitations=[
                    "Product-name similarity alone does not establish formula, population, endpoint, method, or duration alignment."
                ],
            )
        )
        signal = text_analysis.ai_text_signal
        if signal is not None:
            available = signal.available and signal.score is not None
            observations = []
            if available:
                observations.append(
                    f"AI 文风疑似度分数：{signal.score:.2f}/1.00。"
                )
                if signal.explanation:
                    observations.append(signal.explanation)
                observations.extend(signal.observed_cues)
            else:
                observations.append(
                    "AI 文风疑似度暂不可用："
                    + (signal.reason or "provider_not_configured")
                )
            cards.append(
                EvidenceCard(
                    dimension="text_provenance",
                    status=(
                        "weak_signal"
                        if available and signal.score >= 0.60
                        else "no_strong_signal"
                        if available
                        else "unavailable"
                    ),
                    title="AI 文案风格线索",
                    observations=observations,
                    signal_score=signal.score if available else None,
                    score_semantics=(
                        signal.score_semantics
                        or "weak provenance/style signal; not proof of authorship"
                    ),
                    limitations=[
                        "该分数是未校准的文风疑似度，不是 AI 写作概率；单篇成稿不能证明作者来源。",
                        "该信号不参与文案事实风险分或整体风险分。",
                    ],
                )
            )

    if comment_analysis is not None:
        patterns = [
            item
            for item in comment_analysis.pattern_clusters
            if item.pattern_type != "sampled_unique_comments"
        ]
        cards.append(
            EvidenceCard(
                dimension="comment_anomaly",
                status=(
                    "patterns_found"
                    if patterns
                    else "no_clear_pattern"
                    if comment_analysis.available
                    else "unavailable"
                ),
                title="Comment text patterns",
                observations=[
                    f"{item.pattern_type}: {item.count} comments; sample: {item.representative_text}"
                    for item in patterns
                ]
                or [comment_analysis.summary],
                limitations=[
                    "Text patterns do not establish that a comment is fake, coordinated, paid, or from a particular account."
                ],
            )
        )
    return cards


def review_priority(
    *,
    findings: list[Finding],
    text_analysis: TextAnalysisResult | None = None,
    provenance_analysis: ProvenanceAnalysis | None = None,
    provenance_analyses: list[ProvenanceAnalysis] | None = None,
    comment_analysis: CommentAnalysisResult | None = None,
) -> str:
    if any(
        (
            finding.category == "contradicted_claim"
            and finding.verdict == "contradicted"
            and bool(finding.evidence_ids)
        )
        or (
            finding.category == "image_text_mismatch"
        )
        for finding in findings
    ):
        return "high"
    provenances = provenance_analyses or (
        [provenance_analysis] if provenance_analysis is not None else []
    )
    if any(item.status == "invalid" for item in provenances):
        return "high"
    if text_analysis is not None and any(
        claim.evidence_required
        and claim.verification_status == "insufficient_evidence"
        for claim in text_analysis.claims
    ):
        return "review"
    if any(
        finding.category not in {"ai_generated_text", "semantic_anomaly"}
        and (
            finding.verdict == "suspicious"
            or (
                finding.category in {"unsupported_claim", "fake_endorsement_risk"}
                and finding.verdict == "insufficient_evidence"
            )
            or (
                finding.severity == "high"
                and finding.verdict == "insufficient_evidence"
            )
        )
        for finding in findings
    ):
        return "review"
    if any(finding.category == "semantic_anomaly" for finding in findings):
        return "review"
    if comment_analysis is not None and any(
        cluster.pattern_type != "sampled_unique_comments"
        and (
            cluster.verdict == "suspicious"
            or (
                not comment_analysis.model_reviewed
                and cluster.pattern_type in {"repeated_template", "near_duplicate"}
                and cluster.count >= 3
            )
        )
        for cluster in comment_analysis.pattern_clusters
    ):
        return "review"
    return "low"


def _provenance_card(
    analysis: ProvenanceAnalysis, *, title_suffix: str = ""
) -> EvidenceCard:
    observations = []
    if analysis.manifest_present is True:
        observations.append(
            "C2PA manifest found; SDK validation state: "
            + (analysis.validation_state or "unavailable")
        )
        if analysis.claim_generator:
            observations.append("Claim generator: " + analysis.claim_generator)
        if analysis.signature_issuer:
            observations.append("Signature issuer: " + analysis.signature_issuer)
        if analysis.signature_time:
            observations.append("Signature time: " + analysis.signature_time)
        if analysis.ingredients_count is not None:
            observations.append(f"Manifest ingredients: {analysis.ingredients_count}")
        if analysis.actions:
            observations.append("Recorded actions: " + ", ".join(analysis.actions))
    elif analysis.manifest_present is False:
        observations.append("No embedded C2PA manifest was found in the supplied file.")
    if analysis.exif_present is True:
        camera = " ".join(
            value
            for value in (analysis.exif_camera_make, analysis.exif_camera_model)
            if value
        )
        if camera:
            observations.append("EXIF camera: " + camera)
        if analysis.exif_software:
            observations.append("EXIF software: " + analysis.exif_software)
        if analysis.exif_datetime_original:
            observations.append("EXIF capture time: " + analysis.exif_datetime_original)
    elif analysis.exif_present is False:
        observations.append("No EXIF capture metadata was found; this is neutral.")
    if not observations and analysis.error:
        observations.append("Provenance check unavailable: " + analysis.error)
    return EvidenceCard(
        dimension="provenance",
        status=analysis.status,
        title="Content provenance" + title_suffix,
        observations=observations,
        limitations=[
            "A valid manifest records provenance assertions; it does not establish that claims are true or that the signer is trustworthy.",
            "Missing C2PA or EXIF metadata is neutral and does not increase suspicion.",
        ],
    )


def _visual_card(analyses: list[ImageForensicsResult]) -> EvidenceCard:
    if analyses and all(
        result.provider == "legacy_forensics_disabled" for result in analyses
    ):
        return EvidenceCard(
            dimension="visual_forensics",
            status="not_run",
            title="Pixel-level image forensics",
            observations=[
                "No standalone pixel-level detector was run; Qwen visual findings are shown in the semantic review."
            ],
            score_semantics="No forensic score was produced.",
            limitations=[
                "Qwen visual observations are not pixel-level evidence and do not establish AI generation or image manipulation."
            ],
        )

    observations: list[str] = []
    signal_present = False
    errors: list[str] = []
    for index, result in enumerate(analyses, start=1):
        generation = result.ai_generation_signal
        if generation is not None and generation.available and generation.score is not None:
            signal_present = True
            observations.append(
                f"Image {index} LOTA signal={generation.score:.3f} "
                f"({generation.score_semantics})."
            )
        if result.localized_manipulation_score is not None:
            signal_present = True
            observations.append(
                f"Image {index} NFA-ViT localized signal="
                f"{result.localized_manipulation_score:.3f}; overlay="
                f"{result.localized_overlay_path or 'unavailable'}."
            )
        trufor = result.trufor_analysis
        if trufor is not None and trufor.available:
            signal_present = True
            observations.append(
                f"Image {index} TruFor detection={trufor.detection_score}; "
                f"localization={trufor.localization_score}; "
                f"reliability-map mean={trufor.reliability_mean}; "
                f"overlay={trufor.overlay_path or 'unavailable'}."
            )
        errors.extend(result.provider_errors)
        if result.error:
            errors.append(result.error)
    if not observations and errors:
        observations.append("Forensic providers unavailable: " + ", ".join(dict.fromkeys(errors)))
    return EvidenceCard(
        dimension="visual_forensics",
        status="inconclusive" if signal_present else "unavailable",
        title="Independent image-forensics signals",
        observations=observations,
        score_semantics="Raw detector outputs are uncalibrated signals, not probabilities.",
        limitations=[
            "No detector threshold is applied until a beauty-domain calibration set establishes the target false-positive rate.",
            "LOTA covers whole-image generation; TruFor targets manipulation; NFA-ViT is a localization hint. Their outputs are not fused or causal explanations of one another.",
        ],
    )


def _claim_status(summary: ClaimEvidenceSummary) -> str:
    if summary.contradicted:
        return "contradicted_claims_present"
    if summary.insufficient_evidence:
        return "insufficient_evidence_present"
    if summary.not_assessed:
        return "incomplete"
    if summary.evidence_required:
        return "supported_by_supplied_evidence"
    return "not_applicable"


def _claim_observation(claim) -> str:
    alignment = claim.evidence_alignment
    if alignment is None:
        return f"{claim.id}: {claim.verification_status or 'not assessed'} — {claim.text}"
    dimensions = ", ".join(
        f"{name}={getattr(alignment, name)}"
        for name in (
            "product_formula",
            "population",
            "endpoint",
            "method",
            "duration",
            "comparator",
            "result",
        )
    )
    reason = f"; {alignment.reason}" if alignment.reason else ""
    return (
        f"{claim.id}: {claim.verification_status or 'not assessed'} — {claim.text}; "
        f"evidence alignment={alignment.status} ({dimensions}){reason}"
    )
