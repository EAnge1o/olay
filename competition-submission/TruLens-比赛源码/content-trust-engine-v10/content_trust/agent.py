"""Unified dimension routing, risk scoring, evidence chain, and task follow-up."""

from __future__ import annotations

import re
import time
from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from content_trust.analyzers.account import AccountHistoryAnalyzer
from content_trust.config import Settings
from content_trust.providers.deepseek import DeepSeekClient
from content_trust.providers.qwen_vision import QwenVisionClient
from content_trust.schemas import (
    AgentAnalysisResult,
    AgentDebugEvent,
    AgentInput,
    BatchTrustInput,
    BatchTrustReport,
    DimensionAssessment,
    EvidenceChainItem,
    FollowUpResult,
    TrustImageInput,
    TrustInput,
)
from content_trust.schemas.agent import (
    AccountAnalysisResult,
    AccountHistoryInput,
    AgentDimension,
    AgentRoleRun,
    AgentRiskLevel,
    FetchedPostData,
    ScreenshotIntakeResult,
)
from content_trust.schemas.output import Finding, ImageTrustResult, TrustReport
from content_trust.service import ContentTrustEngine


class PostDataFetcher(Protocol):
    """Optional adapter for a platform-specific post URL fetcher."""

    def fetch(self, url: str) -> FetchedPostData: ...


@dataclass(frozen=True)
class AgentRiskConfig:
    """Configurable dimension weights and triage thresholds."""

    weights: dict[AgentDimension, float] | None = None
    medium_threshold: float = 30.0
    high_threshold: float = 60.0

    def resolved_weights(self) -> dict[AgentDimension, float]:
        weights = self.weights or {
            "image": 0.30,
            "text": 0.35,
            "comment": 0.20,
            "account": 0.15,
        }
        if set(weights) != {"image", "text", "comment", "account"}:
            raise ValueError("weights must define image, text, comment, and account")
        if any(
            not isinstance(value, (float, int)) or value < 0 or value > 1
            for value in weights.values()
        ):
            raise ValueError("dimension weights must be numbers from 0 to 1")
        if sum(weights.values()) <= 0:
            raise ValueError("at least one dimension weight must be positive")
        if not 0 <= self.medium_threshold < self.high_threshold <= 100:
            raise ValueError("risk thresholds must satisfy 0 <= medium < high <= 100")
        return dict(weights)


class TaskNotFoundError(LookupError):
    """Raised when a follow-up references a task absent from the context store."""


class ContentTrustAgent:
    """Public facade for automatic dimension selection and task-bound analysis."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        engine: ContentTrustEngine | None = None,
        screenshot_provider: Any | None = None,
        account_provider: Any | None = None,
        follow_up_provider: Any | None = None,
        post_fetcher: PostDataFetcher | None = None,
        task_store: MutableMapping[str, dict[str, Any]] | None = None,
        risk_config: AgentRiskConfig | None = None,
    ) -> None:
        self.settings = settings or (engine.settings if engine else Settings.from_env())
        self.post_fetcher = post_fetcher
        self.risk_config = risk_config or AgentRiskConfig()
        self.weights = self.risk_config.resolved_weights()
        self._tasks = task_store if task_store is not None else {}

        deepseek = None
        if self.settings.deepseek_api_key and any(
            provider is None for provider in (account_provider, follow_up_provider)
        ):
            try:
                deepseek = DeepSeekClient(self.settings)
            except Exception:
                deepseek = None
        qwen = None
        if self.settings.qwen_api_key or self.settings.dashscope_api_key:
            try:
                qwen = QwenVisionClient(self.settings)
            except Exception:
                qwen = None

        self._screenshot_provider = screenshot_provider or qwen
        self._account_provider = account_provider or deepseek
        self._follow_up_provider = follow_up_provider or deepseek
        if engine is None:
            self.engine = ContentTrustEngine(
                self.settings,
                claim_provider=deepseek,
                crossmodal_provider=qwen,
                comment_provider=deepseek,
                explanation_provider=deepseek,
            )
        else:
            self.engine = engine
        self._account_analyzer = AccountHistoryAnalyzer(self._account_provider)

    def analyze(self, input: AgentInput | dict[str, Any]) -> AgentAnalysisResult:
        request = input if isinstance(input, AgentInput) else AgentInput.model_validate(input)
        debug: list[AgentDebugEvent] = []
        started = time.perf_counter()
        self._event(debug, "input_routing", "started", "Inspecting supplied input sources.")

        resolved = self._resolve_sources(request, debug)
        text = resolved["text"]
        images: list[TrustImageInput] = resolved["images"]
        comments = resolved["comments"]
        history: AccountHistoryInput | None = resolved["account_history"]
        evidence_items = resolved["evidence_items"]
        content_id = resolved["content_id"]
        fetch_error = resolved["fetch_error"]
        screenshot_intakes: dict[str, ScreenshotIntakeResult] = resolved["screenshot_intakes"]
        post_url_mode = request.post_url is not None

        enabled = self._select_dimensions(
            request,
            text=text,
            images=images,
            comments=comments,
            history=history,
            screenshot_dimensions=resolved["screenshot_dimensions"],
            post_url_mode=post_url_mode,
        )
        self._event(
            debug,
            "input_routing",
            "completed",
            "Enabled dimensions: " + ", ".join(enabled),
        )

        report: TrustReport | BatchTrustReport | None = None
        account_result: AccountAnalysisResult | None = None
        if text or images or comments:
            callback = lambda message: self._record_engine_progress(debug, message)
            try:
                if images:
                    report = self.engine.analyze_batch(
                        BatchTrustInput(
                            content_id=content_id,
                            text=text,
                            images=images,
                            evidence_items=evidence_items,
                            comments=comments,
                            comment_scope=resolved["comment_scope"],
                            locale=request.locale,
                        ),
                        progress_callback=callback,
                    )
                else:
                    report = self.engine.analyze(
                        TrustInput(
                            content_id=content_id,
                            text=text,
                            comments=comments,
                            evidence_items=evidence_items,
                            comment_scope=resolved["comment_scope"],
                            locale=request.locale,
                        )
                    )
                self._event(
                    debug,
                    "content_analysis",
                    "completed",
                    "Core text, image, and/or comment analysis completed.",
                )
            except Exception as exc:
                self._event(
                    debug,
                    "content_analysis",
                    "failed",
                    "Core analysis failed; other dimensions remain available.",
                    error_code=getattr(exc, "code", "analysis_error"),
                )

        if "account" in enabled and history is not None:
            stage_started = time.perf_counter()
            self._event(debug, "account_analysis", "started", dimension="account")
            account_result = self._account_analyzer.analyze(history, locale=request.locale)
            elapsed = (time.perf_counter() - stage_started) * 1000
            status = "completed" if account_result.available else "unavailable"
            self._event(
                debug,
                "account_analysis",
                status,
                account_result.summary,
                dimension="account",
                elapsed_ms=elapsed,
                error_code=account_result.error,
            )

        assessments = self._build_assessments(
            enabled,
            report=report,
            account_result=account_result,
            text_present=bool(text),
            image_present=bool(images),
            comments_present=bool(comments),
            history_present=history is not None,
            post_url_mode=post_url_mode,
            fetch_error=fetch_error,
        )
        overall_score, overall_level = self._aggregate(assessments)
        evidence_chain = self._build_evidence_chain(
            report=report,
            account_result=account_result,
            text=text,
            comments=comments,
            history=history,
            evidence_items=evidence_items,
            images=images,
            screenshot_intakes=screenshot_intakes,
        )
        role_runs = self._build_role_runs(
            report=report,
            account_result=account_result,
            text_present=bool(text),
            image_present=bool(images),
            comments_present=bool(comments),
            history_present=history is not None,
            screenshot_count=len(request.screenshots),
            screenshot_intakes=screenshot_intakes,
            evidence_chain=evidence_chain,
        )
        major_evidence = self._major_evidence(report, assessments, account_result)
        limitations = self._limitations(
            report,
            assessments,
            post_url_mode=post_url_mode,
            fetch_error=fetch_error,
            screenshot_intakes=screenshot_intakes,
        )
        summary = self._summary(overall_score, overall_level, assessments, major_evidence)
        self._event(
            debug,
            "report_assembly",
            "completed",
            "Structured report and evidence chain assembled.",
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

        provider_errors = [event.error_code for event in debug if event.error_code]
        if report is not None:
            provider_errors.extend(report.model_metadata.get("provider_errors", []))
        result = AgentAnalysisResult(
            task_id=request.task_id,
            content_id=content_id,
            input_kind=self._input_kind(request, enabled),
            enabled_dimensions=enabled,
            overall_score=overall_score,
            overall_risk_level=overall_level,
            major_evidence=major_evidence,
            dimension_assessments=assessments,
            role_runs=role_runs,
            evidence_chain=evidence_chain,
            summary=summary,
            analysis_report=report,
            account_analysis=account_result,
            screenshot_intakes=screenshot_intakes,
            limitations=limitations,
            model_metadata={
                "dimension_weights": self.weights,
                "normalized_weights": {
                    item.dimension: item.normalized_weight
                    for item in assessments
                    if item.normalized_weight is not None
                },
                "risk_score_semantics": "internal risk aggregation; not a calibrated probability that the content or account is fake",
                "claim_prompt_version": "v10",
                "explanation_prompt_version": "v10",
                "deepseek_model": self.settings.deepseek_model,
                "deepseek_reasoning_effort": getattr(self._account_provider, "reasoning_effort", None),
                "qwen_vision_model": getattr(self._screenshot_provider, "model_name", None),
                "role_provider_map": {
                    "text": "deepseek",
                    "comment": "deepseek",
                    "account": "deepseek",
                    "image": "qwen_vision",
                },
                "role_statuses": {item.role: item.status for item in role_runs},
                "evidence_chain_item_count": len(evidence_chain),
                "ai_text_signal_semantics": (
                    "uncalibrated DeepSeek style-suspicion score; not an authorship probability"
                ),
                "screenshot_prompt_version": getattr(
                    self._screenshot_provider, "screenshot_prompt_version", None
                ),
                "account_prompt_version": "v1",
                "follow_up_prompt_version": "v1",
                "post_fetcher_configured": self.post_fetcher is not None,
                "provider_errors": list(dict.fromkeys(provider_errors)),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            },
            debug_log=debug if request.include_debug_log else [],
        )

        # Store the normalized, resolved task context. It is in memory by default;
        # callers can supply a persistent mutable mapping when needed.
        self._tasks[request.task_id] = {
            "input": request.model_dump(mode="json"),
            "resolved_text": text,
            "resolved_comments": [item.model_dump(mode="json") for item in comments],
            "resolved_account_history": (
                history.model_dump(mode="json") if history is not None else None
            ),
            "result": result.model_dump(mode="json"),
            "debug_log": [item.model_dump(mode="json") for item in debug],
        }
        return result

    def follow_up(self, task_id: str, question: str) -> FollowUpResult:
        """Answer only from the stored report and evidence for this task."""

        if not question.strip():
            raise ValueError("question must not be empty")
        context = self._tasks.get(task_id)
        if context is None:
            raise TaskNotFoundError(f"No stored analysis context for task_id={task_id}")
        result = context["result"]
        locale = context["input"].get("locale", "zh-CN")
        provider_method = getattr(self._follow_up_provider, "answer_follow_up", None)
        if not callable(provider_method):
            return FollowUpResult(
                task_id=task_id,
                answer="当前追问模型不可用。请查看本任务已生成的报告和证据链；系统不会脱离本次分析补答。",
                grounded=False,
            )
        compact_context = {
            "input": context["input"],
            "resolved_text": context["resolved_text"],
            "resolved_comments": context["resolved_comments"],
            "resolved_account_history": context["resolved_account_history"],
            "report": result,
            "debug_events": context["debug_log"],
        }
        try:
            answer = provider_method(
                question,
                compact_context,
                task_id=task_id,
                locale=locale,
            )
        except Exception:
            return FollowUpResult(
                task_id=task_id,
                answer="本次追问处理失败，现有任务报告仍可查看；请基于报告中的证据链复核。",
                grounded=False,
            )
        valid_dimensions = set(result.get("enabled_dimensions", []))
        valid_chain_ids = {
            item.get("id") for item in result.get("evidence_chain", [])
        }
        if (
            not set(answer.related_dimensions).issubset(valid_dimensions)
            or not set(answer.evidence_chain_ids).issubset(valid_chain_ids)
        ):
            return FollowUpResult(
                task_id=task_id,
                answer="模型返回了本任务中不存在的维度或证据引用，已拒绝该回答。请直接查看原报告。",
                grounded=False,
            )
        return answer.model_copy(update={"task_id": task_id})

    def _resolve_sources(
        self, request: AgentInput, debug: list[AgentDebugEvent]
    ) -> dict[str, Any]:
        text = request.text
        images = list(request.images)
        comments = list(request.comments)
        history = request.account_history
        evidence_items = list(request.evidence_items)
        comment_scope = request.comment_scope
        content_id = request.content_id or request.task_id
        fetch_error = None
        screenshot_intakes: dict[str, ScreenshotIntakeResult] = {}
        screenshot_dimensions: set[AgentDimension] = set()

        if request.post_url is not None:
            self._event(debug, "post_fetch", "started", "Post URL supplied; all four dimensions requested.")
            if self.post_fetcher is None:
                fetch_error = "post_fetcher_not_configured"
            else:
                try:
                    fetched = self.post_fetcher.fetch(str(request.post_url))
                    fetched = FetchedPostData.model_validate(fetched)
                    content_id = request.content_id or fetched.content_id or content_id
                    text = text or fetched.text
                    images.extend(fetched.images)
                    comments.extend(fetched.comments)
                    history = history or fetched.account_history
                    evidence_items.extend(fetched.evidence_items)
                    comment_scope = comment_scope or fetched.comment_scope
                except Exception as exc:
                    fetch_error = getattr(exc, "code", "post_fetch_failed")
            self._event(
                debug,
                "post_fetch",
                "completed" if fetch_error is None else "unavailable",
                "Post data normalized." if fetch_error is None else "URL data could not be fetched.",
                error_code=fetch_error,
            )

        for screenshot in request.screenshots:
            self._event(
                debug,
                "screenshot_intake",
                "started",
                f"Classifying screenshot {screenshot.id} and extracting visible content.",
                dimension="image",
            )
            analyze_screenshot = getattr(self._screenshot_provider, "analyze_screenshot", None)
            if not callable(analyze_screenshot):
                self._event(
                    debug,
                    "screenshot_intake",
                    "unavailable",
                    "Screenshot extraction provider is not configured.",
                    dimension="image",
                    error_code="provider_not_configured",
                )
                # Keep the screenshot available to the regular image pipeline.
                images.append(
                    TrustImageInput(
                        id=screenshot.id,
                        image_path=screenshot.image_path,
                        image_url=screenshot.image_url,
                    )
                )
                continue
            try:
                intake = analyze_screenshot(
                    image_path=screenshot.image_path,
                    image_url=screenshot.image_url,
                    locale=request.locale,
                )
                intake = ScreenshotIntakeResult.model_validate(intake)
                screenshot_intakes[screenshot.id] = intake
                screenshot_dimensions.update(intake.dimensions_present)
                if intake.post_text:
                    text = "\n".join(part for part in (text, intake.post_text) if part)
                for index, comment in enumerate(intake.comments, start=1):
                    comments.append(
                        self._comment_from_extraction(screenshot.id, index, comment)
                    )
                if intake.account_snapshot is not None:
                    history = self._merge_account_history(history, intake.account_snapshot)
                if "image" in intake.dimensions_present:
                    images.append(
                        TrustImageInput(
                            id=screenshot.id,
                            image_path=screenshot.image_path,
                            image_url=screenshot.image_url,
                        )
                    )
                self._event(
                    debug,
                    "screenshot_intake",
                    "completed",
                    f"Recognized screenshot type={intake.screen_type}; dimensions={','.join(intake.dimensions_present)}.",
                    dimension="image",
                )
            except Exception as exc:
                code = getattr(exc, "code", "screenshot_intake_failed")
                self._event(
                    debug,
                    "screenshot_intake",
                    "failed",
                    "Screenshot extraction failed; screenshot retained as an image input.",
                    dimension="image",
                    error_code=code,
                )
                images.append(
                    TrustImageInput(
                        id=screenshot.id,
                        image_path=screenshot.image_path,
                        image_url=screenshot.image_url,
                    )
                )

        images = _dedupe_by_id(images)
        comments = _dedupe_by_id(comments)
        evidence_items = _dedupe_by_id(evidence_items)
        return {
            "content_id": content_id,
            "text": text,
            "images": images,
            "comments": comments,
            "account_history": history,
            "evidence_items": evidence_items,
            "comment_scope": comment_scope,
            "fetch_error": fetch_error,
            "screenshot_intakes": screenshot_intakes,
            "screenshot_dimensions": screenshot_dimensions,
        }

    def _select_dimensions(
        self,
        request: AgentInput,
        *,
        text: str | None,
        images: list[TrustImageInput],
        comments: list[Any],
        history: AccountHistoryInput | None,
        screenshot_dimensions: set[AgentDimension],
        post_url_mode: bool,
    ) -> list[AgentDimension]:
        if post_url_mode:
            return ["image", "text", "comment", "account"]
        selected: list[AgentDimension] = []
        if images:
            selected.append("image")
        if text or "text" in screenshot_dimensions:
            selected.append("text")
        if comments or "comment" in screenshot_dimensions:
            selected.append("comment")
        if history is not None or "account" in screenshot_dimensions:
            selected.append("account")
        return selected

    def _build_assessments(
        self,
        enabled: list[AgentDimension],
        *,
        report: TrustReport | BatchTrustReport | None,
        account_result: AccountAnalysisResult | None,
        text_present: bool,
        image_present: bool,
        comments_present: bool,
        history_present: bool,
        post_url_mode: bool,
        fetch_error: str | None,
    ) -> list[DimensionAssessment]:
        text_analysis = report.text_analysis if report is not None else None
        comment_analysis = report.comment_analysis if report is not None else None
        assessments: list[DimensionAssessment] = []
        for dimension in enabled:
            relevant = _findings_for_dimension(report, dimension)
            if dimension == "text":
                relevant = [
                    finding
                    for finding in relevant
                    if finding.category != "ai_generated_text"
                ]
            score: float | None = None
            status: Literal["assessed", "insufficient_data", "unavailable"] = "unavailable"
            summary = "本维度没有可用输入或分析信号。"
            evidence: list[str] = []
            limitations: list[str] = []

            if dimension == "text" and text_present:
                if text_analysis is not None:
                    scores = [finding.risk_score for finding in relevant if finding.risk_score is not None]
                    scores.extend(claim.risk_score for claim in text_analysis.claims)
                    score = max(scores, default=0.0) * 100
                    status = "assessed"
                    evidence = [finding.observation for finding in relevant[:5]] or [
                        f"已抽取 {len(text_analysis.claims)} 条原子 claim。"
                    ]
                    limitations.append("文案风险分不是文案为假的概率；证据不足不等于虚假。")
                else:
                    status, summary = "unavailable", "文案分析未能完成。"
            elif dimension == "image" and image_present:
                image_results = _report_images(report)
                signals = []
                for item in image_results:
                    forensic = item.image_analysis
                    if forensic.available:
                        if forensic.ai_generation_signal and forensic.ai_generation_signal.score is not None:
                            signals.append(forensic.ai_generation_signal.score)
                        if forensic.localized_manipulation_score is not None:
                            signals.append(forensic.localized_manipulation_score)
                        if forensic.trufor_analysis and forensic.trufor_analysis.localization_score is not None:
                            signals.append(forensic.trufor_analysis.localization_score)
                signals.extend(
                    finding.risk_score for finding in relevant if finding.risk_score is not None
                )
                if any(item.crossmodal_analysis.available for item in image_results) or signals:
                    score = max(signals, default=0.0) * 100
                    status = "assessed"
                    evidence = [finding.observation for finding in relevant[:5]]
                    if not evidence:
                        evidence = ["已运行图片语义分析；未生成可报告的具体风险 finding。"]
                    limitations.append("视觉语义线索不能替代像素级鉴伪；模型分数不是 AI 生成概率。")
                else:
                    status, summary = "unavailable", "图片分析模型未提供可用结果。"
            elif dimension == "comment" and comments_present:
                if comment_analysis is not None and comment_analysis.available:
                    cluster_scores = [
                        0.82 if cluster.verdict == "suspicious" else
                        0.20 if cluster.verdict == "uncertain" and cluster.pattern_type != "sampled_unique_comments" else
                        0.0
                        for cluster in comment_analysis.pattern_clusters
                    ]
                    assessment_scores = [item.risk_score for item in comment_analysis.assessments]
                    score = max(cluster_scores + assessment_scores, default=0.0) * 100
                    status = "assessed"
                    evidence = [
                        f"{cluster.pattern_type}: {cluster.count} 条；{cluster.representative_text[:160]}"
                        for cluster in comment_analysis.pattern_clusters
                        if cluster.pattern_type != "sampled_unique_comments"
                    ][:5]
                    if not evidence:
                        evidence = [f"已覆盖 {comment_analysis.assessed_count} 条评论，未发现明确重复/导流模式。"]
                    limitations.append("评论文本模式只能提示异常，不能证明评论为假、付费或协同。")
                else:
                    status, summary = "unavailable", "评论分析不可用。"
            elif dimension == "account":
                if account_result is None or not history_present:
                    status = "unavailable"
                    summary = (
                        "帖子链接数据未成功获取。" if post_url_mode and fetch_error
                        else "没有提供账号历史记录。"
                    )
                elif not account_result.available:
                    status = (
                        "insufficient_data"
                        if account_result.error == "insufficient_history"
                        else "unavailable"
                    )
                    summary = account_result.summary
                    limitations.append("账号历史样本不足或提供方不可用；未据此判定账号异常。")
                else:
                    status = "assessed"
                    score = (account_result.risk_score or 0.0) * 100
                    evidence = [item.observation for item in account_result.findings[:5]] or [account_result.summary]
                    limitations.append("账号分析只覆盖调用方提供的历史样本，不代表完整账号画像。")

            level = _risk_level(score, self.risk_config) if score is not None else None
            assessments.append(
                DimensionAssessment(
                    dimension=dimension,
                    status=status,
                    score=round(score, 2) if score is not None else None,
                    risk_level=level,
                    configured_weight=self.weights[dimension],
                    summary=summary if status != "assessed" else _dimension_summary(dimension, score or 0.0, evidence),
                    evidence=evidence,
                    limitations=limitations,
                )
            )
        available_weight = sum(
            item.configured_weight
            for item in assessments
            if item.status == "assessed" and item.score is not None
        )
        if available_weight:
            assessments = [
                item.model_copy(
                    update={
                        "normalized_weight": round(item.configured_weight / available_weight, 6)
                        if item.status == "assessed" and item.score is not None
                        else None
                    }
                )
                for item in assessments
            ]
        return assessments

    def _aggregate(
        self, assessments: list[DimensionAssessment]
    ) -> tuple[float | None, AgentRiskLevel | None]:
        included = [
            item for item in assessments if item.status == "assessed" and item.score is not None
        ]
        denominator = sum(item.configured_weight for item in included)
        if denominator <= 0:
            return None, None
        score = sum(item.score * item.configured_weight for item in included) / denominator
        score = min(100.0, max(0.0, score))
        return round(score, 2), _risk_level(score, self.risk_config)

    def _build_role_runs(
        self,
        *,
        report: TrustReport | BatchTrustReport | None,
        account_result: AccountAnalysisResult | None,
        text_present: bool,
        image_present: bool,
        comments_present: bool,
        history_present: bool,
        screenshot_count: int,
        screenshot_intakes: dict[str, ScreenshotIntakeResult],
        evidence_chain: list[EvidenceChainItem],
    ) -> list[AgentRoleRun]:
        text_analysis = report.text_analysis if report is not None else None
        comment_analysis = _report_comment_analysis(report)
        image_results = _report_images(report)
        chain_ids = {
            dimension: [item.id for item in evidence_chain if item.dimension == dimension]
            for dimension in ("text", "comment", "account", "image")
        }

        claim_provider = getattr(self.engine, "_claim_provider", None)
        text_model = getattr(claim_provider, "model_name", self.settings.deepseek_model)
        text_status = "skipped"
        text_summary = "未提供正文，跳过文案角色。"
        text_error = None
        if text_present:
            if text_analysis is None:
                text_status, text_summary = "unavailable", "文案分析结果缺失。"
            elif text_analysis.provider_error:
                text_status = "fallback"
                text_error = text_analysis.provider_error
                text_summary = (
                    f"DeepSeek 文案分析失败，已使用本地规则回退；"
                    f"保留 {len(text_analysis.claims)} 条 claim。"
                )
            elif claim_provider is None:
                text_status = "fallback"
                text_summary = (
                    f"DeepSeek 未配置，已使用本地规则；"
                    f"提取 {len(text_analysis.claims)} 条 claim。"
                )
            else:
                text_status = "completed"
                text_summary = f"DeepSeek 完成文案与 claim 分析，提取 {len(text_analysis.claims)} 条 claim。"
            signal = text_analysis.ai_text_signal if text_analysis is not None else None
            if signal is not None and signal.available and signal.score is not None:
                text_summary += f" AI 文风疑似度 {signal.score:.2f}/1.00（未校准分数）。"

        comment_provider = getattr(self.engine, "_comment_provider", None)
        comment_analysis_status = "skipped"
        comment_summary = "未提供评论，跳过评论角色。"
        comment_error = None
        if comments_present:
            if comment_analysis is None:
                comment_analysis_status, comment_summary = "unavailable", "评论分析结果缺失。"
            elif comment_analysis.model_reviewed:
                comment_analysis_status = "completed"
                comment_summary = (
                    f"DeepSeek 完成评论模式分析，覆盖 {comment_analysis.assessed_count} 条评论。"
                )
            elif comment_analysis.error and comment_analysis.error != "provider_not_configured":
                comment_analysis_status = "fallback"
                comment_error = comment_analysis.error
                comment_summary = (
                    f"DeepSeek 评论分析不可用，保留本地模式筛查；"
                    f"覆盖 {comment_analysis.assessed_count} 条评论。"
                )
            elif comment_provider is None:
                comment_analysis_status = "fallback"
                comment_summary = (
                    f"DeepSeek 未配置，使用本地评论模式筛查；"
                    f"覆盖 {comment_analysis.assessed_count} 条评论。"
                )
            else:
                comment_analysis_status = "fallback"
                comment_summary = (
                    f"评论仅完成本地模式筛查，覆盖 {comment_analysis.assessed_count} 条；"
                    "未获得 DeepSeek 复核。"
                )

        account_provider = self._account_provider
        account_model = getattr(account_provider, "model_name", self.settings.deepseek_model)
        account_status = "skipped"
        account_summary = "未提供账号历史，跳过账号角色。"
        account_error = None
        if history_present:
            if account_result is None:
                account_status, account_summary = "unavailable", "账号历史分析结果缺失。"
            elif account_result.error == "insufficient_history":
                account_status = "insufficient_data"
                account_summary = account_result.summary
            elif account_result.available:
                account_status = "completed"
                account_summary = account_result.summary
            else:
                account_status = "unavailable"
                account_error = account_result.error
                account_summary = account_result.summary

        crossmodal = [item.crossmodal_analysis for item in image_results]
        available_crossmodal = sum(item.available for item in crossmodal)
        image_requested = image_present or screenshot_count > 0
        image_status = "skipped"
        image_summary = "未提供图片或截图，跳过 Qwen 视觉角色。"
        image_error = None
        if image_requested:
            if crossmodal and available_crossmodal == len(crossmodal):
                image_status = "completed"
                image_summary = (
                    f"Qwen 完成 {available_crossmodal} 张图片的视觉语义/图文一致性分析。"
                )
            elif available_crossmodal:
                image_status = "partial"
                image_summary = (
                    f"Qwen 完成 {available_crossmodal}/{len(crossmodal)} 张图片的视觉语义分析。"
                )
                image_error = next(
                    (item.error for item in crossmodal if not item.available and item.error),
                    None,
                )
            elif screenshot_intakes:
                image_status = "partial" if image_present else "completed"
                image_summary = (
                    f"Qwen 完成 {len(screenshot_intakes)} 张截图的内容抽取；"
                    "图文一致性检查未提供可用结果。"
                )
                image_error = next(
                    (item.error for item in crossmodal if item.error), None
                )
            else:
                image_status = "unavailable"
                image_error = next(
                    (item.error for item in crossmodal if item.error),
                    "provider_not_configured",
                )
                image_summary = "Qwen 视觉分析不可用；没有据此生成图像语义结论。"

        crossmodal_provider = getattr(self.engine, "_crossmodal_provider", None)
        qwen_model = getattr(
            crossmodal_provider,
            "model_name",
            self.settings.qwen_vl_model,
        )
        return [
            AgentRoleRun(
                role="text",
                provider="deepseek",
                model=text_model,
                prompt_version="v10",
                status=text_status,
                summary=text_summary,
                error_code=text_error,
                evidence_chain_ids=chain_ids["text"],
            ),
            AgentRoleRun(
                role="comment",
                provider="deepseek",
                model=self.settings.deepseek_model,
                prompt_version="v3",
                status=comment_analysis_status,
                summary=comment_summary,
                error_code=comment_error,
                evidence_chain_ids=chain_ids["comment"],
            ),
            AgentRoleRun(
                role="account",
                provider="deepseek",
                model=account_model,
                prompt_version="v1",
                status=account_status,
                summary=account_summary,
                error_code=account_error,
                evidence_chain_ids=chain_ids["account"],
            ),
            AgentRoleRun(
                role="image",
                provider="qwen_vision",
                model=qwen_model,
                prompt_version=getattr(
                    crossmodal_provider, "prompt_version", "qwen_v1"
                ),
                status=image_status,
                summary=image_summary,
                error_code=image_error,
                evidence_chain_ids=chain_ids["image"],
            ),
        ]

    def _build_evidence_chain(
        self,
        *,
        report: TrustReport | BatchTrustReport | None,
        account_result: AccountAnalysisResult | None,
        text: str | None,
        comments: list[Any],
        history: AccountHistoryInput | None,
        evidence_items: list[Any],
        images: list[TrustImageInput],
        screenshot_intakes: dict[str, ScreenshotIntakeResult],
    ) -> list[EvidenceChainItem]:
        chain: list[EvidenceChainItem] = []
        if text:
            text_analysis = report.text_analysis if report else None
            text_cursor = 0
            for claim in (text_analysis.claims if text_analysis else []):
                start = text.find(claim.text, text_cursor) if claim.text else -1
                if start < 0 and claim.text:
                    start = text.find(claim.text)
                if start >= 0:
                    text_cursor = start + len(claim.text)
                screenshot_match = _match_screenshot_region(
                    screenshot_intakes, claim.text, expected_kind="post_text"
                )
                chain.append(
                    EvidenceChainItem(
                        id=f"text-{claim.id}",
                        dimension="text",
                        source_kind="image" if screenshot_match else "text",
                        source_id=screenshot_match[0] if screenshot_match else "post_text",
                        excerpt=claim.text,
                        start_char=start if start >= 0 else None,
                        end_char=start + len(claim.text) if start >= 0 else None,
                        image_id=screenshot_match[0] if screenshot_match else None,
                        bbox=screenshot_match[1].bbox if screenshot_match else None,
                        claim_id=claim.id,
                        related_ids=(
                            [
                                f"screenshot-{screenshot_match[0]}-{screenshot_match[1].id}",
                                *[f"evidence-{item_id}" for item_id in claim.evidence_ids],
                            ]
                            if screenshot_match
                            else [f"evidence-{item_id}" for item_id in claim.evidence_ids]
                        ),
                        explanation=claim.reason or "抽取到的原子 claim。",
                    )
                )
            ai_signal = text_analysis.ai_text_signal if text_analysis else None
            if ai_signal is not None:
                score_text = (
                    f"{ai_signal.score:.2f}/1.00"
                    if ai_signal.score is not None
                    else "不可用"
                )
                cue_ids: list[str] = []
                for index, cue in enumerate(ai_signal.observed_cues, start=1):
                    excerpt = _quoted_text(cue)
                    start = text.find(excerpt) if excerpt else -1
                    cue_id = f"text-ai-cue-{index}"
                    cue_ids.append(cue_id)
                    chain.append(
                        EvidenceChainItem(
                            id=cue_id,
                            dimension="text",
                            source_kind="text",
                            source_id="post_text",
                            excerpt=excerpt or cue,
                            start_char=start if start >= 0 else None,
                            end_char=(start + len(excerpt)) if start >= 0 and excerpt else None,
                            explanation=cue,
                        )
                    )
                chain.append(
                    EvidenceChainItem(
                        id="text-ai-style-signal",
                        dimension="text",
                        source_kind="text",
                        source_id="post_text",
                        excerpt="；".join(ai_signal.observed_cues)
                        or ai_signal.explanation
                        or f"AI 文风疑似度分数：{score_text}",
                        related_ids=cue_ids + [
                            f"text-signal-{signal.id}"
                            for signal in (
                                text_analysis.text_integrity.signals
                                if text_analysis
                                else []
                            )
                        ],
                        explanation=(
                            f"来源：{ai_signal.provider or '未配置'}；分数：{score_text}；"
                            f"语义：{ai_signal.score_semantics or ai_signal.reason or '未提供'}"
                        ),
                    )
                )
            for signal in (text_analysis.text_integrity.signals if text_analysis else []):
                start = signal.start_char
                end = signal.end_char
                if start is None or end is None:
                    found = text.find(signal.span)
                    start, end = (found, found + len(signal.span)) if found >= 0 else (None, None)
                chain.append(
                    EvidenceChainItem(
                        id=f"text-signal-{signal.id}",
                        dimension="text",
                        source_kind="text",
                        source_id="post_text",
                        excerpt=signal.span,
                        start_char=start,
                        end_char=end,
                        explanation=signal.observation,
                    )
                )
            for finding in (text_analysis.findings if text_analysis else []):
                excerpt = _quoted_text(finding.observation)
                related_claim_ids = [
                    f"text-{claim.id}"
                    for claim in (text_analysis.claims if text_analysis else [])
                    if excerpt and excerpt in claim.text
                ]
                start = text.find(excerpt) if excerpt else -1
                if start < 0 and finding.observation:
                    excerpt = finding.observation
                    start = text.find(excerpt)
                screenshot_match = _match_screenshot_region(
                    screenshot_intakes, excerpt or "", expected_kind="post_text"
                )
                chain.append(
                    EvidenceChainItem(
                        id=f"text-finding-{finding.id}",
                        dimension="text",
                        source_kind="image" if screenshot_match else "text",
                        source_id=screenshot_match[0] if screenshot_match else "post_text",
                        finding_id=finding.id,
                        excerpt=excerpt or finding.observation,
                        start_char=start if start >= 0 else None,
                        end_char=start + len(excerpt) if start >= 0 and excerpt else None,
                        image_id=screenshot_match[0] if screenshot_match else None,
                        bbox=screenshot_match[1].bbox if screenshot_match else None,
                        related_ids=(
                            [
                                f"screenshot-{screenshot_match[0]}-{screenshot_match[1].id}",
                                *related_claim_ids,
                                *[f"evidence-{item_id}" for item_id in finding.evidence_ids],
                            ]
                            if screenshot_match
                            else [
                                *related_claim_ids,
                                *[f"evidence-{item_id}" for item_id in finding.evidence_ids],
                            ]
                        ),
                        explanation=finding.reason,
                    )
                )

        comments_by_id = {item.id: item for item in comments}
        for item in comments:
            chain.append(
                EvidenceChainItem(
                    id=f"comment-{item.id}",
                    dimension="comment",
                    source_kind="comment",
                    source_id=item.id,
                    excerpt=item.text,
                    explanation="用户提供或从截图可读抽取的评论文本。",
                )
            )
        comment_analysis = _report_comment_analysis(report)
        for item in comment_analysis.pattern_clusters if comment_analysis else []:
            for comment_id in item.member_comment_ids:
                comment = comments_by_id.get(comment_id)
                chain.append(
                    EvidenceChainItem(
                        id=f"comment-cluster-{item.cluster_id}-{comment_id}",
                        dimension="comment",
                        source_kind="comment",
                        source_id=comment_id,
                        excerpt=comment.text if comment else item.representative_text,
                        related_ids=[item.cluster_id],
                        explanation=item.reason,
                    )
                )

        image_finding_ids = {
            finding.id
            for image_result in _report_images(report)
            for finding in image_result.findings
        }
        for image_result in _report_images(report):
            forensic = image_result.image_analysis
            crossmodal = image_result.crossmodal_analysis
            observed_texts: set[str] = set()
            for observation in crossmodal.observations:
                excerpt = observation.visible_text or observation.observation
                if observation.visible_text:
                    observed_texts.add(observation.visible_text)
                chain.append(
                    EvidenceChainItem(
                        id=f"image-observation-{image_result.image_id}-{observation.id}",
                        dimension="image",
                        source_kind="image",
                        source_id=image_result.image_id,
                        image_id=image_result.image_id,
                        excerpt=excerpt,
                        explanation=(
                            observation.observation
                            + (f"；区域：{observation.region}" if observation.region else "")
                            + "；来源：Qwen 视觉观察。"
                        ),
                    )
                )
            for index, visible_text in enumerate(crossmodal.visible_text, start=1):
                if visible_text in observed_texts:
                    continue
                chain.append(
                    EvidenceChainItem(
                        id=f"image-visible-text-{image_result.image_id}-{index}",
                        dimension="image",
                        source_kind="image",
                        source_id=image_result.image_id,
                        image_id=image_result.image_id,
                        excerpt=visible_text,
                        explanation="Qwen 从图片中读取到的可见文字；未据此单独验证其事实性。",
                    )
                )
            for claim in crossmodal.image_text_claims:
                chain.append(
                    EvidenceChainItem(
                        id=f"image-text-claim-{image_result.image_id}-{claim.id}",
                        dimension="image",
                        source_kind="image",
                        source_id=image_result.image_id,
                        image_id=image_result.image_id,
                        related_ids=[
                            f"evidence-{item_id}" for item_id in claim.evidence_ids
                        ],
                        excerpt=claim.text,
                        explanation=claim.reason or "从图片文字中提取的 claim。",
                    )
                )
            provenance = image_result.provenance_analysis
            if provenance is not None:
                provenance_details = [f"C2PA 状态：{provenance.status}"]
                if provenance.exif_camera_make or provenance.exif_camera_model:
                    provenance_details.append(
                        "EXIF 设备："
                        + " ".join(
                            part
                            for part in (
                                provenance.exif_camera_make,
                                provenance.exif_camera_model,
                            )
                            if part
                        )
                    )
                chain.append(
                    EvidenceChainItem(
                        id=f"image-provenance-{image_result.image_id}",
                        dimension="image",
                        source_kind="image",
                        source_id=image_result.image_id,
                        image_id=image_result.image_id,
                        excerpt="；".join(provenance_details),
                        explanation=(
                            "本地文件元数据观察；C2PA/EXIF 信息只提供来源线索，"
                            "不能单独证明图片真实或被篡改。"
                        ),
                    )
                )
            for finding in image_result.findings:
                chain.append(
                    EvidenceChainItem(
                        id=f"image-finding-{finding.id}",
                        dimension="image",
                        source_kind="image",
                        source_id=image_result.image_id,
                        finding_id=finding.id,
                        image_id=image_result.image_id,
                        bbox=(
                            forensic.localized_region_bbox
                            if finding.category == "localized_image_manipulation"
                            else None
                        ),
                        related_ids=[
                            f"evidence-{item_id}" for item_id in finding.evidence_ids
                        ],
                        excerpt=finding.observation,
                        explanation=finding.reason,
                    )
                )

        text_analysis = report.text_analysis if report is not None else None
        claims = text_analysis.claims if text_analysis is not None else []
        report_findings = _report_findings(report)
        for evidence in evidence_items:
            related_ids = [
                f"text-{claim.id}"
                for claim in claims
                if evidence.id in claim.evidence_ids
            ]
            related_ids.extend(
                f"image-finding-{finding.id}"
                if finding.id in image_finding_ids
                else f"text-finding-{finding.id}"
                for finding in report_findings
                if evidence.id in finding.evidence_ids
            )
            chain.append(
                EvidenceChainItem(
                    id=f"evidence-{evidence.id}",
                    dimension="text",
                    source_kind="external_evidence",
                    source_id=evidence.id,
                    excerpt=evidence.text,
                    related_ids=list(dict.fromkeys(related_ids)),
                    explanation=(
                        f"来源：{evidence.source}"
                        + (f"；URL：{evidence.url}" if evidence.url else "")
                    ),
                )
            )

        for screenshot_id, intake in screenshot_intakes.items():
            for region in intake.regions:
                dimension: AgentDimension = (
                    "comment" if region.kind == "comment" else
                    "account" if region.kind in {"account", "profile"} else
                    "image" if region.kind == "product" else "text"
                )
                chain.append(
                    EvidenceChainItem(
                        id=f"screenshot-{screenshot_id}-{region.id}",
                        dimension=dimension,
                        source_kind="image",
                        source_id=screenshot_id,
                        image_id=screenshot_id,
                        excerpt=region.text or region.description,
                        bbox=region.bbox,
                        explanation="Qwen 截图读取结果；坐标是相对原截图的归一化框。",
                    )
                )

        if history is not None:
            for post in history.posts:
                chain.append(
                    EvidenceChainItem(
                        id=f"account-post-{post.id}",
                        dimension="account",
                        source_kind="account",
                        source_id=post.id,
                        excerpt=post.text,
                        explanation=f"账号历史帖子记录；发布时间：{post.published_at or '未提供'}。",
                    )
                )
        if account_result is not None:
            for finding in account_result.findings:
                chain.append(
                    EvidenceChainItem(
                        id=f"account-finding-{finding.id}",
                        dimension="account",
                        source_kind="account",
                        source_id=finding.id,
                        finding_id=finding.id,
                        related_ids=finding.post_ids,
                        excerpt=finding.observation,
                        explanation=finding.reason,
                    )
                )
        # Keep the list stable and avoid duplicated keys if a provider repeats a ref.
        unique: dict[str, EvidenceChainItem] = {}
        for item in chain:
            unique.setdefault(item.id, item)
        return list(unique.values())

    def _major_evidence(
        self,
        report: TrustReport | BatchTrustReport | None,
        assessments: list[DimensionAssessment],
        account_result: AccountAnalysisResult | None,
    ) -> list[str]:
        items = [
            finding.observation
            for finding in _report_findings(report)
            if finding.observation and finding.category != "ai_generated_text"
        ]
        if account_result is not None:
            items.extend(finding.observation for finding in account_result.findings)
        if not items:
            items.extend(
                evidence
                for item in assessments
                if item.status == "assessed"
                for evidence in item.evidence
            )
        return list(dict.fromkeys(items))[:6]

    def _limitations(
        self,
        report: TrustReport | BatchTrustReport | None,
        assessments: list[DimensionAssessment],
        *,
        post_url_mode: bool,
        fetch_error: str | None,
        screenshot_intakes: dict[str, ScreenshotIntakeResult],
    ) -> list[str]:
        limitations = list(report.limitations) if report else []
        limitations.extend(note for item in assessments for note in item.limitations)
        if post_url_mode and fetch_error:
            limitations.append(
                f"帖子链接未能抓取完整内容（{fetch_error}）；URL 模式请求了四个维度，但不可用维度未参与总分。"
            )
        if screenshot_intakes:
            limitations.append("截图 OCR/区域坐标来自视觉模型抽取，可能有漏识别或定位误差；请对照原截图复核。")
        if not limitations:
            limitations.append("总风险分为当前可用维度信号的加权聚合，不是内容为假的概率。")
        return list(dict.fromkeys(limitations))

    def _summary(
        self,
        score: float | None,
        level: AgentRiskLevel | None,
        assessments: list[DimensionAssessment],
        major_evidence: list[str],
    ) -> str:
        if score is None or level is None:
            return "当前没有可参与评分的已完成维度分析；请检查输入内容、提供方配置或链接抓取状态。"
        level_zh = {"low": "低风险", "medium": "中风险", "high": "高风险"}[level]
        labels = {"image": "图片", "text": "文案", "comment": "评论", "account": "账号历史"}
        dimensions = "、".join(
            f"{labels[item.dimension]} {item.score:.1f}分"
            for item in assessments
            if item.status == "assessed" and item.score is not None
        )
        evidence = "；主要依据：" + "；".join(major_evidence[:3]) if major_evidence else "；未发现具体可报告问题，低风险不代表已证实真实。"
        return (
            f"综合风险分 {score:.1f}/100（{level_zh}）。已评估维度：{dimensions}。"
            f"{evidence} 分数是内部风险聚合，不是真假概率。"
        )

    def _input_kind(
        self, request: AgentInput, enabled: list[AgentDimension]
    ) -> str:
        if request.post_url is not None:
            return "post_url"
        if request.screenshots and not request.text and not request.images and not request.comments and not request.account_history:
            return "screenshot"
        if request.account_history and enabled == ["account"]:
            return "account_history"
        if request.text and not request.images and not request.screenshots and not request.comments and not request.account_history:
            return "text"
        if request.images and not request.text and not request.screenshots and not request.comments and not request.account_history:
            return "image"
        if len(enabled) > 1 or request.screenshots:
            return "mixed"
        return "image" if "image" in enabled else "text"

    @staticmethod
    def _event(
        events: list[AgentDebugEvent],
        stage: str,
        status: str,
        summary: str = "",
        *,
        dimension: AgentDimension | None = None,
        elapsed_ms: float | None = None,
        error_code: str | None = None,
    ) -> None:
        events.append(
            AgentDebugEvent(
                stage=stage,
                status=status,
                summary=summary,
                dimension=dimension,
                elapsed_ms=elapsed_ms,
                error_code=error_code,
            )
        )

    def _record_engine_progress(self, events: list[AgentDebugEvent], message: str) -> None:
        status = "started" if "开始" in message else "completed"
        dimension: AgentDimension | None = None
        if "评论" in message:
            dimension = "comment"
        elif "账号" in message:
            dimension = "account"
        elif "图片" in message or "Qwen" in message:
            dimension = "image"
        elif "文案" in message or "claim" in message:
            dimension = "text"
        self._event(
            events,
            "content_analysis_progress",
            status,
            message,
            dimension=dimension,
        )

    @staticmethod
    def _comment_from_extraction(screenshot_id: str, index: int, comment: Any):
        from content_trust.schemas.input import CommentInput

        return CommentInput(
            id=f"{screenshot_id}-comment-{index}",
            text=comment.text,
            author=comment.author,
            published_at=comment.published_at,
        )

    @staticmethod
    def _merge_account_history(
        existing: AccountHistoryInput | None, incoming: AccountHistoryInput
    ) -> AccountHistoryInput:
        if existing is None:
            return incoming
        posts = {item.id: item for item in existing.posts}
        posts.update({item.id: item for item in incoming.posts})
        return existing.model_copy(
            update={
                "handle": existing.handle or incoming.handle,
                "profile_text": existing.profile_text or incoming.profile_text,
                "follower_count": existing.follower_count if existing.follower_count is not None else incoming.follower_count,
                "total_post_count": existing.total_post_count if existing.total_post_count is not None else incoming.total_post_count,
                "posts": list(posts.values()),
            }
        )


def analyze_with_agent(
    input: AgentInput | dict[str, Any], *, agent: ContentTrustAgent | None = None
) -> AgentAnalysisResult:
    """Convenience entry point for the complete dimension-routing feature."""

    return (agent or ContentTrustAgent()).analyze(input)


def _risk_level(score: float | None, config: AgentRiskConfig) -> AgentRiskLevel | None:
    if score is None:
        return None
    if score >= config.high_threshold:
        return "high"
    if score >= config.medium_threshold:
        return "medium"
    return "low"


def _dimension_summary(
    dimension: AgentDimension, score: float, evidence: list[str]
) -> str:
    labels = {"image": "图片", "text": "文案", "comment": "评论", "account": "账号历史"}
    level = _risk_level(score, AgentRiskConfig())
    level_label = {"low": "低", "medium": "中", "high": "高"}.get(level, "未定")
    if evidence:
        return f"{labels[dimension]}维度风险分 {score:.1f}/100（{level_label}风险）；{evidence[0]}"
    return f"{labels[dimension]}维度风险分 {score:.1f}/100（{level_label}风险）。"


def _report_findings(report: TrustReport | BatchTrustReport | None) -> list[Finding]:
    return list(report.findings) if report is not None else []


def _report_images(report: TrustReport | BatchTrustReport | None) -> list[ImageTrustResult]:
    if isinstance(report, BatchTrustReport):
        return report.images
    if isinstance(report, TrustReport) and report.image_analysis is not None:
        text_finding_ids = {
            item.id for item in report.text_analysis.findings
        } if report.text_analysis is not None else set()
        return [
            ImageTrustResult(
                image_id="image-1",
                image_analysis=report.image_analysis,
                provenance_analysis=report.provenance_analysis,
                crossmodal_analysis=report.crossmodal_analysis,
                findings=[
                    item for item in report.findings
                    if item.id not in text_finding_ids
                ],
            )
        ]
    return []


def _findings_for_dimension(
    report: TrustReport | BatchTrustReport | None,
    dimension: AgentDimension,
) -> list[Finding]:
    if report is None:
        return []
    if dimension == "text":
        return report.text_analysis.findings if report.text_analysis is not None else []
    if dimension == "image":
        return [finding for image in _report_images(report) for finding in image.findings]
    return []


def _report_comment_analysis(report: TrustReport | BatchTrustReport | None):
    return report.comment_analysis if report is not None else None


def _finding_dimension(finding: Finding) -> AgentDimension:
    if finding.category in {"ai_generated_image", "localized_image_manipulation", "image_text_mismatch"}:
        return "image"
    if finding.category == "ai_generated_text" or finding.category in {
        "exaggerated_claim", "unsupported_claim", "contradicted_claim", "semantic_anomaly", "fake_endorsement_risk"
    }:
        return "text"
    return "image"


def _dedupe_by_id(items: list[Any]) -> list[Any]:
    output = {}
    for item in items:
        output.setdefault(item.id, item)
    return list(output.values())


def _quoted_text(text: str) -> str | None:
    for pattern in (r"[“‘「『]([^”’」』]{2,})[”’」』]", r'"([^\"]{2,})"'):
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return None


def _match_screenshot_region(
    intakes: dict[str, ScreenshotIntakeResult],
    excerpt: str,
    *,
    expected_kind: str,
):
    normalized = excerpt.strip()
    if not normalized:
        return None
    for screenshot_id, intake in intakes.items():
        for region in intake.regions:
            if region.kind != expected_kind or not region.text:
                continue
            if normalized in region.text or region.text in normalized:
                return screenshot_id, region
    return None

