"""Public orchestration API for the Content Trust Engine."""

import time
from collections.abc import Callable
from typing import Any

from content_trust.analyzers.crossmodal import CrossModalAnalyzer
from content_trust.analyzers.comments import CommentAnalyzer
from content_trust.analyzers.image import ImageAnalyzer
from content_trust.analyzers.provenance import ProvenanceAnalyzer
from content_trust.analyzers.text import TextAnalyzer
from content_trust.config import Settings
from content_trust.fusion.assessment import (
    build_evidence_cards,
    claim_evidence_summary,
    review_priority,
)
from content_trust.fusion.explain import ExplanationBuilder
from content_trust.providers.deepseek import DeepSeekClient
from content_trust.providers.qwen_vision import QwenVisionClient
from content_trust.schemas import (
    BatchTrustInput,
    BatchTrustReport,
    CommentAnalysisResult,
    EvidenceItem,
    ImageTrustResult,
    ProvenanceAnalysis,
    TrustInput,
    TrustReport,
)


class ContentTrustEngine:
    """Analyze post text, comment patterns, images, and their consistency."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        claim_provider: Any | None = None,
        image_provider: Any | None = None,
        crossmodal_provider: Any | None = None,
        comment_provider: Any | None = None,
        ai_text_detector: Any | None = None,
        explanation_provider: Any | None = None,
        image_generation_provider: Any | None = None,
        trufor_provider: Any | None = None,
        provenance_provider: Any | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self._initialization_errors: list[str] = []
        self._configured_providers: set[str] = set()
        self._semantic_provider_name = "deepseek"

        deepseek = None
        if any(
            provider is None
            for provider in (
                claim_provider,
                crossmodal_provider,
                comment_provider,
                explanation_provider,
            )
        ) and self.settings.deepseek_api_key:
            try:
                deepseek = DeepSeekClient(self.settings)
                self._semantic_provider_name = deepseek.provider_name
                self._configured_providers.add(self._semantic_provider_name)
            except Exception as exc:
                self._initialization_errors.append(
                    f"{self._semantic_provider_name}:{getattr(exc, 'code', 'provider_error')}"
                )

        self._claim_provider = claim_provider if claim_provider is not None else deepseek
        selected_crossmodal_provider = crossmodal_provider
        if selected_crossmodal_provider is None and (
            self.settings.qwen_api_key or self.settings.dashscope_api_key
        ):
            try:
                selected_crossmodal_provider = QwenVisionClient(self.settings)
                self._configured_providers.add(
                    selected_crossmodal_provider.provider_name
                )
            except Exception as exc:
                self._initialization_errors.append(
                    f"qwen_vision:{getattr(exc, 'code', 'provider_error')}"
                )
        self._crossmodal_provider = selected_crossmodal_provider
        fallback_crossmodal_name = (
            "qwen_vision" if crossmodal_provider is None else self._semantic_provider_name
        )
        self._crossmodal_provider_name = getattr(
            self._crossmodal_provider, "provider_name", fallback_crossmodal_name
        )
        self._crossmodal_model_name = getattr(
            self._crossmodal_provider,
            "model_name",
            (
                self.settings.qwen_vl_model
                if crossmodal_provider is None
                else self.settings.deepseek_model
            ),
        )
        self._crossmodal_prompt_version = getattr(
            self._crossmodal_provider,
            "prompt_version",
            "qwen_v1" if crossmodal_provider is None else "v8",
        )
        self._crossmodal_display_name = (
            "Qwen Vision"
            if self._crossmodal_provider_name == "qwen_vision"
            else "DeepSeek Vision"
        )
        if self._crossmodal_provider is not None:
            self._configured_providers.add(self._crossmodal_provider_name)
        self._comment_provider = (
            comment_provider if comment_provider is not None else deepseek
        )
        self._explanation_provider = (
            explanation_provider if explanation_provider is not None else deepseek
        )
        if any(
            provider is not None
            for provider in (
                claim_provider,
                crossmodal_provider,
                comment_provider,
                explanation_provider,
            )
        ):
            self._configured_providers.add(self._semantic_provider_name)

        if ai_text_detector is not None:
            self._configured_providers.add("ai_text_detector")
        self._image_models_were_injected = any(
            provider is not None
            for provider in (
                image_provider,
                image_generation_provider,
                trufor_provider,
            )
        )
        if image_generation_provider is not None:
            self._configured_providers.add(
                getattr(image_generation_provider, "provider_name", "lota_local")
            )
        if image_provider is not None:
            self._configured_providers.add(
                getattr(image_provider, "provider_name", "image_forensics")
            )

        if trufor_provider is not None:
            self._configured_providers.add(
                getattr(trufor_provider, "provider_name", "trufor_local")
            )
        self._trufor_enabled = trufor_provider is not None

        self._text_analyzer = TextAnalyzer(
            claim_provider=self._claim_provider,
            ai_text_detector=ai_text_detector,
        )
        self._image_analyzer = ImageAnalyzer(
            image_provider,
            generation_provider=image_generation_provider,
            trufor_provider=trufor_provider,
            settings=self.settings,
            enable_legacy_defaults=False,
        )
        self._provenance_analyzer = ProvenanceAnalyzer(provenance_provider)
        self._crossmodal_analyzer = CrossModalAnalyzer(self._crossmodal_provider)
        self._comment_analyzer = CommentAnalyzer(self._comment_provider)
        self._explanation_builder = ExplanationBuilder(self._explanation_provider)

    def analyze(self, input: TrustInput | dict[str, Any]) -> TrustReport:
        request = input if isinstance(input, TrustInput) else TrustInput.model_validate(input)
        started = time.perf_counter()
        provider_errors = list(self._initialization_errors)
        evidence_items = request.evidence_items

        text_analysis = None
        if request.text is not None:
            text_analysis = self._text_analyzer.analyze(
                request.text,
                evidence_items,
                locale=request.locale,
            )
            if text_analysis.provider_error:
                provider_errors.append(
                    f"{self._semantic_provider_name}:{text_analysis.provider_error}"
                )
            signal = text_analysis.ai_text_signal
            if (
                signal is not None
                and not signal.available
                and signal.reason
                and signal.reason != "provider_not_configured"
            ):
                provider_errors.append(f"ai_text_detector:{signal.reason}")

        comment_analysis = None
        if request.comments:
            comment_analysis = self._comment_analyzer.analyze(request.comments)
            if request.comment_scope:
                comment_analysis = comment_analysis.model_copy(
                    update={"sample_scope": request.comment_scope}
                )
            if (
                comment_analysis.error
                and comment_analysis.error not in {"provider_not_configured", "no_comments"}
            ):
                provider_errors.append(
                    f"{self._semantic_provider_name}_comments:{comment_analysis.error}"
                )

        image_analysis = None
        provenance_analysis = None
        image_findings = []
        if request.image_path is not None or request.image_url is not None:
            image_analysis, image_findings = self._image_analyzer.analyze(
                image_path=request.image_path,
                image_url=request.image_url,
            )
            if (
                not image_analysis.available
                and image_analysis.error
                and image_analysis.error != "provider_not_configured"
            ):
                provider_errors.append(f"image_forensics:{image_analysis.error}")
            provider_errors.extend(
                f"image_forensics:{error}" for error in image_analysis.provider_errors
            )
            if image_analysis.nfa_error and image_analysis.nfa_error != "provider_not_configured":
                provider_errors.append(f"nfa_vit:{image_analysis.nfa_error}")
            provenance_analysis = self._provenance_analyzer.analyze(
                image_path=request.image_path,
                image_url=request.image_url,
            )
            if provenance_analysis.error and provenance_analysis.error not in {
                "c2pa_dependency_missing",
                "local_file_required_for_provenance",
            }:
                provider_errors.append(f"provenance:{provenance_analysis.error}")

        crossmodal_analysis = None
        if request.image_path is not None or request.image_url is not None:
            crossmodal_analysis = self._crossmodal_analyzer.analyze(
                image_path=request.image_path,
                image_url=request.image_url,
                text=request.text,
                claims=text_analysis.claims if text_analysis is not None else [],
                evidence_items=evidence_items,
            )
            if crossmodal_analysis.error and crossmodal_analysis.error != "provider_not_configured":
                provider_errors.append(
                    f"{self._crossmodal_provider_name}:{crossmodal_analysis.error}"
                )

        findings = []
        if text_analysis is not None:
            findings.extend(text_analysis.findings)
        findings.extend(image_findings)
        if crossmodal_analysis is not None:
            findings.extend(crossmodal_analysis.image_text_findings)
            findings.extend(crossmodal_analysis.findings)

        evidence_cards = build_evidence_cards(
            provenance_analysis=provenance_analysis,
            image_analyses=[image_analysis] if image_analysis is not None else [],
            crossmodal_analyses=(
                [crossmodal_analysis] if crossmodal_analysis is not None else []
            ),
            text_analysis=text_analysis,
            comment_analysis=comment_analysis,
        )
        priority = review_priority(
            findings=findings,
            text_analysis=text_analysis,
            provenance_analysis=provenance_analysis,
            comment_analysis=comment_analysis,
        )
        explanation, explanation_error = self._explanation_builder.build(
            findings=findings,
            evidence_items=evidence_items,
            review_priority=priority,
            evidence_cards=evidence_cards,
            comment_analysis=comment_analysis,
            locale=request.locale,
        )
        if explanation_error and explanation_error != "provider_not_configured":
            provider_errors.append(
                f"{self._semantic_provider_name}_explanation:{explanation_error}"
            )

        limitations = _limitations(
            request,
            text_analysis,
            image_analysis,
            crossmodal_analysis,
            comment_analysis,
            provenance_analysis,
        )
        model_metadata = {
            "deepseek_model": self.settings.deepseek_model,
            "deepseek_reasoning_effort": getattr(
                self._claim_provider, "reasoning_effort", None
            ),
            "semantic_backend": self._semantic_provider_name,
            "semantic_model": self.settings.deepseek_model,
            "crossmodal_backend": self._crossmodal_provider_name,
            "crossmodal_model": self._crossmodal_model_name,
            "claim_prompt_version": "v10",
            "text_integrity_version": "v1",
            "crossmodal_prompt_version": self._crossmodal_prompt_version,
            "comment_prompt_version": "v3",
            "explanation_prompt_version": "v10",
            "provider_errors": list(dict.fromkeys(provider_errors)),
            "configured_providers": sorted(self._configured_providers),
            "image_forensics_scope": "Qwen Vision visual screening; legacy image-forensics models are disabled in the main engine",
            "image_model_stage": "qwen_vision_primary",
            "legacy_image_models_enabled": self._image_models_were_injected,
            "review_priority_policy": "evidence-backed contradiction, clear image-text mismatch, or invalid C2PA => high; evidence gaps, suspicious claims, or reviewable comment/text patterns => review; uncalibrated detector and AI-text signals do not set priority by themselves",
            "claim_evidence_summary": claim_evidence_summary(text_analysis).model_dump(mode="json"),
            "comment_analysis_runs": 1 if comment_analysis is not None else 0,
            "comment_llm_input_count": comment_analysis.llm_input_count if comment_analysis else 0,
            "comment_scope": request.comment_scope,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        return TrustReport(
            content_id=request.content_id,
            review_priority=priority,
            provenance_analysis=provenance_analysis,
            claim_evidence_summary=claim_evidence_summary(text_analysis),
            evidence_cards=evidence_cards,
            findings=findings,
            text_analysis=text_analysis,
            comment_analysis=comment_analysis,
            image_analysis=image_analysis,
            crossmodal_analysis=crossmodal_analysis,
            summary=explanation.summary,
            recommended_actions=explanation.recommended_actions,
            limitations=limitations,
            model_metadata=model_metadata,
        )

    def analyze_batch(
        self,
        input: BatchTrustInput | dict[str, Any],
        *,
        progress_callback: Callable[[str], None] | None = None,
    ) -> BatchTrustReport:
        """Analyze a batch and optionally report stage progress to the caller."""

        request = (
            input
            if isinstance(input, BatchTrustInput)
            else BatchTrustInput.model_validate(input)
        )
        started = time.perf_counter()
        provider_errors = list(self._initialization_errors)
        evidence_items = request.evidence_items

        text_analysis = None
        if request.text is not None:
            stage_started = time.perf_counter()
            _notify_progress(progress_callback, "DeepSeek 文案与 claim 分析：开始")
            text_analysis = self._text_analyzer.analyze(
                request.text,
                evidence_items,
                locale=request.locale,
            )
            text_detail = (
                f"提取 {len(text_analysis.claims)} 条 claim"
                if text_analysis is not None
                else "完成"
            )
            if text_analysis.provider_error:
                text_detail += f"；已降级：{text_analysis.provider_error}"
            _notify_progress(
                progress_callback,
                _completed_stage("DeepSeek 文案与 claim 分析", stage_started, text_detail),
            )
            if text_analysis.provider_error:
                provider_errors.append(
                    f"{self._semantic_provider_name}:{text_analysis.provider_error}"
                )
            signal = text_analysis.ai_text_signal
            if (
                signal is not None
                and not signal.available
                and signal.reason
                and signal.reason != "provider_not_configured"
            ):
                provider_errors.append(f"ai_text_detector:{signal.reason}")

        comment_analysis = None
        if request.comments:
            stage_started = time.perf_counter()
            _notify_progress(
                progress_callback,
                f"DeepSeek 评论模式分析：开始（{len(request.comments)} 条）",
            )
            comment_analysis = self._comment_analyzer.analyze(request.comments)
            if request.comment_scope:
                comment_analysis = comment_analysis.model_copy(
                    update={"sample_scope": request.comment_scope}
                )
            comment_detail = (
                f"已分析 {comment_analysis.assessed_count} 条评论"
                if comment_analysis.available
                else f"信号不可用：{comment_analysis.error or 'unknown'}"
            )
            _notify_progress(
                progress_callback,
                _completed_stage("DeepSeek 评论模式分析", stage_started, comment_detail),
            )
            if (
                comment_analysis.error
                and comment_analysis.error not in {"provider_not_configured", "no_comments"}
            ):
                provider_errors.append(
                    f"{self._semantic_provider_name}_comments:{comment_analysis.error}"
                )

        image_results: list[ImageTrustResult] = []
        crossmodal_analyses = []
        all_image_text_findings = []
        findings = list(text_analysis.findings) if text_analysis is not None else []

        for image in request.images:
            stage_started = time.perf_counter()
            _notify_progress(
                progress_callback,
                f"图片分析 {image.id}：跳过已停用的旧版图像模型",
            )
            image_analysis, image_findings = self._image_analyzer.analyze(
                image_path=image.image_path,
                image_url=image.image_url,
            )
            provenance_analysis = self._provenance_analyzer.analyze(
                image_path=image.image_path,
                image_url=image.image_url,
            )
            forensic_detail = _image_progress_detail(image_analysis)
            _notify_progress(
                progress_callback,
                _completed_stage(f"图片鉴伪 {image.id}", stage_started, forensic_detail),
            )
            if (
                not image_analysis.available
                and image_analysis.error
                and image_analysis.error != "provider_not_configured"
            ):
                provider_errors.append(f"image_forensics:{image.id}:{image_analysis.error}")
            provider_errors.extend(
                f"image_forensics:{image.id}:{error}"
                for error in image_analysis.provider_errors
            )
            if image_analysis.nfa_error and image_analysis.nfa_error != "provider_not_configured":
                provider_errors.append(f"nfa_vit:{image.id}:{image_analysis.nfa_error}")
            if provenance_analysis.error and provenance_analysis.error not in {
                "c2pa_dependency_missing",
                "local_file_required_for_provenance",
            }:
                provider_errors.append(
                    f"provenance:{image.id}:{provenance_analysis.error}"
                )

            stage_started = time.perf_counter()
            _notify_progress(
                progress_callback,
                f"Qwen 图片分析 {image.id}：开始（{self._crossmodal_display_name}）",
            )
            crossmodal_analysis = self._crossmodal_analyzer.analyze(
                image_path=image.image_path,
                image_url=image.image_url,
                text=request.text,
                claims=text_analysis.claims if text_analysis is not None else [],
                evidence_items=evidence_items,
            )
            consistency_detail = (
                f"状态={crossmodal_analysis.status}"
                if crossmodal_analysis.available
                else f"信号不可用：{crossmodal_analysis.error or 'unknown'}"
            )
            _notify_progress(
                progress_callback,
                _completed_stage(
                    f"Qwen 图片分析 {image.id}", stage_started, consistency_detail
                ),
            )
            crossmodal_analyses.append(crossmodal_analysis)
            if (
                crossmodal_analysis.error
                and crossmodal_analysis.error != "provider_not_configured"
            ):
                provider_errors.append(
                    f"{self._crossmodal_provider_name}:{image.id}:{crossmodal_analysis.error}"
                )

            all_image_text_findings.extend(crossmodal_analysis.image_text_findings)
            image_findings.extend(crossmodal_analysis.image_text_findings)
            image_findings.extend(crossmodal_analysis.findings)
            scoped_findings = [
                _scope_finding(image.id, finding) for finding in image_findings
            ]
            image_results.append(
                ImageTrustResult(
                    image_id=image.id,
                    image_analysis=image_analysis,
                    provenance_analysis=provenance_analysis,
                    crossmodal_analysis=crossmodal_analysis,
                    findings=scoped_findings,
                )
            )
            findings.extend(scoped_findings)

        provenance_analyses = [
            item.provenance_analysis
            for item in image_results
            if item.provenance_analysis is not None
        ]
        evidence_cards = build_evidence_cards(
            provenance_analyses=provenance_analyses,
            image_analyses=[item.image_analysis for item in image_results],
            crossmodal_analyses=crossmodal_analyses,
            text_analysis=text_analysis,
            comment_analysis=comment_analysis,
        )
        priority = review_priority(
            findings=findings,
            text_analysis=text_analysis,
            provenance_analyses=provenance_analyses,
            comment_analysis=comment_analysis,
        )
        stage_started = time.perf_counter()
        _notify_progress(progress_callback, "最终说明整理：开始")
        explanation, explanation_error = self._explanation_builder.build(
            findings=findings,
            evidence_items=evidence_items,
            review_priority=priority,
            evidence_cards=evidence_cards,
            image_results=image_results,
            comment_analysis=comment_analysis,
            locale=request.locale,
        )
        explanation_detail = (
            f"使用本地整理（DeepSeek 信号：{explanation_error}）"
            if explanation_error
            else "完成"
        )
        _notify_progress(
            progress_callback,
            _completed_stage("最终说明整理", stage_started, explanation_detail),
        )
        if explanation_error and explanation_error != "provider_not_configured":
            provider_errors.append(
                f"{self._semantic_provider_name}_explanation:{explanation_error}"
            )

        limitations = _batch_limitations(
            request,
            text_analysis,
            image_results,
            comment_analysis,
        )
        model_metadata = {
            "deepseek_model": self.settings.deepseek_model,
            "semantic_backend": self._semantic_provider_name,
            "semantic_model": self.settings.deepseek_model,
            "crossmodal_backend": self._crossmodal_provider_name,
            "crossmodal_model": self._crossmodal_model_name,
            "claim_prompt_version": "v10",
            "text_integrity_version": "v1",
            "crossmodal_prompt_version": self._crossmodal_prompt_version,
            "comment_prompt_version": "v3",
            "explanation_prompt_version": "v10",
            "provider_errors": list(dict.fromkeys(provider_errors)),
            "configured_providers": sorted(self._configured_providers),
            "image_forensics_scope": "Qwen Vision visual screening; legacy image-forensics models are disabled in the main engine",
            "image_model_stage": "qwen_vision_primary",
            "legacy_image_models_enabled": self._image_models_were_injected,
            "text_analysis_runs": 1 if text_analysis is not None else 0,
            "image_vision_runs": len(image_results),
            "explanation_runs": 1,
            "comment_analysis_runs": 1 if comment_analysis is not None else 0,
            "comment_llm_input_count": comment_analysis.llm_input_count if comment_analysis else 0,
            "comment_scope": request.comment_scope,
            "review_priority_policy": "evidence-backed contradiction, clear image-text mismatch, or invalid C2PA => high; evidence gaps, suspicious claims, or reviewable comment/text patterns => review; uncalibrated detector and AI-text signals do not set priority by themselves",
            "claim_evidence_summary": claim_evidence_summary(text_analysis).model_dump(mode="json"),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        return BatchTrustReport(
            content_id=request.content_id,
            review_priority=priority,
            claim_evidence_summary=claim_evidence_summary(text_analysis),
            evidence_cards=evidence_cards,
            findings=findings,
            text_analysis=text_analysis,
            comment_analysis=comment_analysis,
            images=image_results,
            summary=explanation.summary,
            recommended_actions=explanation.recommended_actions,
            limitations=limitations,
            model_metadata=model_metadata,
        )

    @property
    def configured_providers(self) -> set[str]:
        return set(self._configured_providers)

def analyze_content(
    input: TrustInput | dict[str, Any],
    *,
    engine: ContentTrustEngine | None = None,
) -> TrustReport:
    """Convenience entry point for backend callers."""

    return (engine or ContentTrustEngine()).analyze(input)


def analyze_content_batch(
    input: BatchTrustInput | dict[str, Any],
    *,
    engine: ContentTrustEngine | None = None,
) -> BatchTrustReport:
    """Convenience entry point for analyzing one post with multiple images."""

    return (engine or ContentTrustEngine()).analyze_batch(input)


def _notify_progress(
    callback: Callable[[str], None] | None, message: str
) -> None:
    if callback is None:
        return
    try:
        callback(message)
    except Exception:
        # Progress output must never break content analysis.
        return


def _completed_stage(stage: str, started: float, detail: str) -> str:
    elapsed = time.perf_counter() - started
    return f"{stage}：完成，耗时 {elapsed:.1f}s；{detail}"


def _image_progress_detail(result) -> str:
    if result.provider == "legacy_forensics_disabled":
        return "旧版图像模型已停用；Qwen 图片分析在下一阶段运行"
    details: list[str] = []
    generation = result.ai_generation_signal
    if generation is not None:
        details.append(
            f"LOTA={generation.score:.2f}"
            if generation.available and generation.score is not None
            else f"LOTA 不可用：{generation.error or 'unknown'}"
        )
    if result.localized_manipulation_score is not None:
        details.append(f"NFA-ViT localization={result.localized_manipulation_score:.2f}")
    elif result.nfa_error and result.nfa_error != "provider_not_configured":
        details.append(f"NFA-ViT 不可用：{result.nfa_error}")
    trufor = result.trufor_analysis
    if trufor is not None:
        details.append(
            f"TruFor localization={trufor.localization_score:.2f}"
            if trufor.available and trufor.localization_score is not None
            else f"TruFor 不可用：{trufor.error or 'unknown'}"
        )
    return "；".join(details) if details else f"图像信号不可用：{result.error or 'not_detected'}"


def _append_image_limitations(limits: list[str], results: list) -> None:
    if not results:
        return
    if all(not result.available for result in results):
        limits.append(
            "本次未运行独立像素级图像取证模型；Qwen 的图像结论是语义视觉线索，不能证明 AI 生成或局部篡改。"
        )
    elif any(not result.available for result in results):
        limits.append("至少一张图片没有独立取证模型信号；Qwen 视觉线索不能替代像素级鉴伪。")
    signals = [result.ai_generation_signal for result in results]
    if any(signal is not None and signal.available for signal in signals):
        limits.append(
            "LOTA 分数是未校准的模型信号，不是 AI 生成概率；当前模型在 GenImage 上评估，"
            "尚未验证美妆种草图的误报率。"
        )
    if any(
        result.localized_manipulation_score is not None
        or "nfa_vit" in result.provider
        for result in results
    ):
        limits.append(
            "NFA-ViT 只给出局部 AI 生成/修补候选区域；红色叠加和外接框不证明该处被篡改，"
            "也不能解释 LOTA 整图分数的成因。"
        )
    if any(
        result.trufor_analysis is not None and result.trufor_analysis.available
        for result in results
    ):
        limits.append(
            "TruFor 输出图像完整性与局部定位信号，不区分 AI 全图生成与所有类型的局部编辑；分数和热图均未在美妆域校准。"
        )


def _limitations(
    request: TrustInput,
    text_analysis,
    image_analysis,
    crossmodal_analysis,
    comment_analysis: CommentAnalysisResult | None,
    provenance_analysis: ProvenanceAnalysis | None,
) -> list[str]:
    limits = [
        "人工复核优先级是分流建议，不是真假结论或校准概率。",
    ]
    if not request.evidence_items:
        limits.append("未提供外部证据；事实性 claim 不应据此判为虚假。")
    _append_image_limitations(limits, [image_analysis] if image_analysis is not None else [])
    if provenance_analysis is not None:
        limits.append(
            "C2PA manifest 或 EXIF 缺失按中性处理；有效 provenance 也只记录来源声明，不证明文案功效或背书真实。"
        )
    if text_analysis is not None:
        limits.extend(_text_integrity_limitations(text_analysis))
    if crossmodal_analysis is not None and not crossmodal_analysis.available:
        limits.append("图文一致性未能完成；当前没有可用的跨模态结论。")
    if (
        text_analysis is not None
        and text_analysis.ai_text_signal is not None
        and not text_analysis.ai_text_signal.available
    ):
        limits.append(
            "AI 文本检测未提供有效信号；即使有结果，它也只表示来源/风格线索，不代表内容失实。"
        )
    elif (
        text_analysis is not None
        and text_analysis.ai_text_signal is not None
        and text_analysis.ai_text_signal.available
        and text_analysis.ai_text_signal.provider == "deepseek_style_review"
    ):
        limits.append(
            "DeepSeek AI 文风疑似度是未校准的风格信号，不是作者来源概率；它不参与整体风险评分。"
        )
    if comment_analysis is not None:
        limits.extend(_comment_limitations(comment_analysis))
    return list(dict.fromkeys(limits))


def _batch_limitations(
    request: BatchTrustInput,
    text_analysis,
    image_results: list[ImageTrustResult],
    comment_analysis: CommentAnalysisResult | None,
) -> list[str]:
    limits = [
        "人工复核优先级是分流建议，不是真假结论或校准概率。",
    ]
    if not request.evidence_items:
        limits.append("未提供外部证据；事实性 claim 不应据此判为虚假。")
    _append_image_limitations(limits, [item.image_analysis for item in image_results])
    if any(item.provenance_analysis is not None for item in image_results):
        limits.append(
            "C2PA manifest 或 EXIF 缺失按中性处理；有效 provenance 也只记录来源声明，不证明文案功效或背书真实。"
        )
    if text_analysis is not None:
        limits.extend(_text_integrity_limitations(text_analysis))
    if any(not item.crossmodal_analysis.available for item in image_results):
        limits.append("至少一张图片的文字提取或图文一致性检查未能完成。")
    if (
        text_analysis is not None
        and text_analysis.ai_text_signal is not None
        and not text_analysis.ai_text_signal.available
    ):
        limits.append(
            "AI 文本检测未提供有效信号；即使有结果，它也只表示来源/风格线索，不代表内容失实。"
        )
    elif (
        text_analysis is not None
        and text_analysis.ai_text_signal is not None
        and text_analysis.ai_text_signal.available
        and text_analysis.ai_text_signal.provider == "deepseek_style_review"
    ):
        limits.append(
            "DeepSeek AI 文风疑似度是未校准的风格信号，不是作者来源概率；它不参与整体风险评分。"
        )
    if comment_analysis is not None:
        limits.extend(_comment_limitations(comment_analysis))
    return list(dict.fromkeys(limits))


def _comment_limitations(comment_analysis: CommentAnalysisResult) -> list[str]:
    limits = [
        "评论分析仅识别传入文本中的灌水或促销模式，无法凭文本确认评论者身份、真实购买/使用或付费关系。",
        "评论结果是文本模式线索，不是评论真假概率或账号级协同行为结论。",
    ]
    if not comment_analysis.available:
        limits.append("评论分析 provider 不可用；本次没有有效的评论风险判断。")
    elif not comment_analysis.model_reviewed:
        limits.append("评论只经过本地重复/促销规则筛查，未经过 DeepSeek 模式复核。")
    if comment_analysis.error:
        limits.append(f"评论模式 provider 降级：{comment_analysis.error}。")
    if comment_analysis.sample_scope:
        limits.append("评论样本范围：" + comment_analysis.sample_scope)
    return limits


def _text_integrity_limitations(text_analysis) -> list[str]:
    limits = [
        "文本完整性结果只表示文风、重复或内部一致性线索；单篇最终文本不能证明 AI 生成或后期编辑。确认拼接/改写需要原稿、版本记录或可核验来源。"
    ]
    if not text_analysis.text_integrity.model_reviewed:
        limits.append(
            "DeepSeek 文案复核不可用；文本完整性仅运行确定性重复句段检查，未评估文风变化。"
        )
    return limits


def _scope_finding(image_id: str, finding):
    observation = f"[{image_id}] {finding.observation}" if finding.observation else image_id
    return finding.model_copy(
        update={
            "id": f"{image_id}:{finding.id}",
            "observation": observation,
        }
    )


