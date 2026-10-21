"""Analysis orchestration — the heart of the platform.

Responsibilities:
  * create tasks and validate requested agents
  * execute the agent pipeline (concurrently) and persist each result
  * run the ReportAgent fuse and persist the final report
  * keep task status/progress observable for ``GET /api/status``

Concurrency note: agent ``analyze`` calls are pure and run under
``asyncio.gather``. All database writes stay on the single owning AsyncSession
and therefore happen sequentially — an AsyncSession is not concurrency-safe.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.base import BaseAgent
from app.agents.registry import AgentRegistry
from app.core.enums import DIMENSION_ORDER, AgentName, RiskLevel, TaskStatus
from app.core.exceptions import NotFoundError, ValidationError
from app.core.utils import utcnow
from app.models.account_snapshot import AccountSnapshot
from app.models.agent_result import AgentResult
from app.models.analysis_task import AnalysisTask
from app.models.content_sample import ContentSample
from app.models.engine_trace import EngineTrace
from app.models.report import Report
from app.schemas.agent import AgentInput, AgentOutput
from app.services.file_service import FileService
from app.services.trust_engine_runner import EngineRunResult, TrustEngineRunner
from app.services.xhs_service import XhsError, XhsService

logger = logging.getLogger(__name__)


class AnalysisService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        registry: AgentRegistry,
        default_use_llm: bool = False,
        engine_runner: TrustEngineRunner | None = None,
        file_service: FileService | None = None,
        xhs_service: XhsService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry
        self._default_use_llm = default_use_llm
        # Optional Applied-AI engine. When present it replaces the heuristic
        # agents for the analysis dimensions; if it cannot run, we fall back.
        self._engine_runner = engine_runner
        # Needed to persist images pulled in by link ingestion.
        self._file_service = file_service
        # Link ingestion (paste a Xiaohongshu URL). Optional; when absent a
        # link-only upload fails with a clear reason instead of guessing.
        self._xhs_service = xhs_service
        # Task ids currently executing in this process. Prevents the upload
        # auto-analyze background task from racing a manual POST /analyze.
        # (Single-process scope; a multi-worker deployment would need a DB lock.)
        self._running: set[str] = set()

    @property
    def engine_runner(self) -> TrustEngineRunner | None:
        return self._engine_runner

    @property
    def registry(self) -> AgentRegistry:
        return self._registry

    # -- Task lifecycle -----------------------------------------------------

    async def create_task(
        self,
        db: AsyncSession,
        sample: ContentSample,
        requested_agents: list[str] | None = None,
        use_llm: bool | None = None,
    ) -> AnalysisTask:
        agents = self._registry.resolve_subset(requested_agents)
        task = AnalysisTask(
            sample_id=sample.id,
            status=TaskStatus.PENDING.value,
            requested_agents=agents,
            run_options={"use_llm": self._default_use_llm if use_llm is None else use_llm},
            progress=0,
        )
        db.add(task)
        await db.flush()
        return task

    async def get_task(self, db: AsyncSession, task_id: str) -> AnalysisTask:
        task = await db.get(AnalysisTask, task_id)
        if task is None:
            raise NotFoundError(f"analysis task not found: {task_id}")
        return task

    async def get_status(self, db: AsyncSession, task_id: str) -> tuple[AnalysisTask, list[AgentResult]]:
        task = await self.get_task(db, task_id)
        result = await db.execute(
            select(AgentResult)
            .where(AgentResult.task_id == task_id)
            .order_by(AgentResult.created_at)
        )
        return task, list(result.scalars().all())

    async def mark_queued(self, db: AsyncSession, task: AnalysisTask) -> None:
        """Flip a pending task to 'running' synchronously so polling shows progress."""
        task.status = TaskStatus.RUNNING.value
        await db.commit()

    # -- Pipeline -----------------------------------------------------------

    async def run_task(self, task_id: str) -> None:
        """Full pipeline. Designed to run as a background task with its own session."""
        if task_id in self._running:
            logger.info("run_task: task %s is already running; skipping duplicate", task_id)
            return
        self._running.add(task_id)
        try:
            async with self._session_factory() as db:
                task = await db.get(AnalysisTask, task_id)
                if task is None:
                    logger.warning("run_task: unknown task %s", task_id)
                    return
                if task.status == TaskStatus.COMPLETED.value:
                    logger.info("run_task: task %s already completed", task_id)
                    return

                use_llm = bool((task.run_options or {}).get("use_llm", self._default_use_llm))

                try:
                    await self._execute(db, task, use_llm)
                except Exception as exc:  # noqa: BLE001 - surface any failure to the task
                    logger.exception("analysis task %s failed", task_id)
                    await db.rollback()
                    await self._fail(db, task_id, exc)
        finally:
            self._running.discard(task_id)

    async def _ensure_ingested(
        self, db: AsyncSession, task: AnalysisTask, sample: ContentSample
    ) -> None:
        """Materialise a link-only sample by crawling its source URL.

        Runs at most once per sample (guarded by ``raw_metadata["ingested"]``), so
        re-analysing an already-crawled sample never opens a browser again.
        Failures surface as a task failure with the crawl's own reason — the
        pipeline never invents content it could not fetch.
        """
        metadata = dict(sample.raw_metadata or {})
        source_url = metadata.get("source_url")
        if not source_url or metadata.get("ingested"):
            return

        if self._xhs_service is None:
            raise ValidationError("无法抓取该链接：未注册链接抓取服务")
        if not self._xhs_service.available:
            raise ValidationError(f"无法抓取该链接：{self._xhs_service.unavailable_reason()}")

        # Long-running (real browser): report progress before and after.
        task.progress = 12
        await db.commit()

        account_url = str(metadata.get("account_url") or "").strip() or None
        try:
            note = await self._xhs_service.fetch(source_url, account_url=account_url)
        except XhsError as exc:
            raise ValidationError(f"小红书链接抓取失败：{exc.reason}") from exc

        if note.text.strip():
            sample.text_content = note.text
            sample.text_length = len(note.text)

        # Caller-supplied comments win: they may have pasted more than we scraped.
        if note.comments and not (sample.comment_payload or []):
            sample.comment_payload = note.comments
            sample.comment_count = len(note.comments)

        if note.image_paths and not sample.image_path and self._file_service is not None:
            try:
                stored = self._file_service.save_local_image(Path(note.image_paths[0]))
            except Exception as exc:  # noqa: BLE001 - image ingest must not fail the task
                logger.warning("xhs image ingest failed: %s", exc)
            else:
                sample.image_path = stored["path"]
                sample.image_filename = stored["filename"]
                sample.image_meta = {**stored["meta"], "origin": "xiaohongshu"}

        # Prefer the author-homepage crawl. The T agent requires at least three
        # comparable posts; a partial homepage still keeps the account context
        # visible while honestly remaining "暂不评分".
        if sample.account_snapshot is None:
            account = note.account
            handle = (account.handle if account else None) or note.author_handle
            if handle:
                sample.account_snapshot = AccountSnapshot(
                    sample_id=sample.id,
                    handle=handle,
                    profile_text=account.profile_text if account else None,
                    follower_count=account.follower_count if account else None,
                    total_post_count=account.total_post_count if account else None,
                    posts=account.posts if account else [],
                    post_count=len(account.posts) if account else 0,
                )

        sample.source = "xiaohongshu"
        sample.raw_metadata = {
            **metadata,
            "ingested": True,
            "ingested_at": utcnow().isoformat(),
            "xhs_title": note.title,
            "xhs_author": note.author_handle,
            "xhs_image_count": note.image_count,
            "xhs_comment_count": len(note.comments),
            "xhs_warning": note.warning,
            "account_profile_url": note.account.profile_url if note.account else account_url,
            "account_post_count": len(note.account.posts) if note.account else 0,
            "account_warning": note.account.warning if note.account else note.warning,
            "analysed_image_count": 1 if sample.image_path else 0,
        }
        task.progress = 20
        await db.commit()
        logger.info(
            "task %s ingested from xhs: %s chars, %s comments, %s images",
            task.id,
            sample.text_length,
            sample.comment_count,
            note.image_count,
        )

    async def _execute(self, db: AsyncSession, task: AnalysisTask, use_llm: bool) -> None:
        sample = await db.get(ContentSample, task.sample_id)
        if sample is None:
            raise ValidationError("content sample missing for task")

        # A link-only upload is completed here: the browser crawl is slow, so it
        # runs inside this background job rather than blocking POST /api/upload.
        await self._ensure_ingested(db, task, sample)

        requested: list[str] = list(task.requested_agents or [])
        agents: list[BaseAgent] = []
        for name in requested:
            agent = self._registry.get(name)
            if agent is None:
                raise ValidationError(f"agent not registered: {name}")
            agents.append(agent)

        # Reset any previous run's artifacts so re-analysis is idempotent.
        await db.execute(delete(AgentResult).where(AgentResult.task_id == task.id))
        await db.execute(delete(Report).where(Report.task_id == task.id))
        await db.execute(delete(EngineTrace).where(EngineTrace.task_id == task.id))

        task.status = TaskStatus.RUNNING.value
        task.started_at = utcnow()
        task.error_message = None
        task.progress = 5
        await db.commit()

        rows: dict[str, AgentResult] = {}
        for name in requested:
            row = AgentResult(
                task_id=task.id,
                agent_name=name,
                status=TaskStatus.PENDING.value,
                score=0.0,
            )
            db.add(row)
            rows[name] = row
        task.progress = 10
        await db.commit()

        snapshot = sample.account_snapshot
        agent_input = AgentInput(
            task_id=task.id,
            text=sample.text_content,
            image_path=sample.image_path,
            image_meta=sample.image_meta or {},
            comments=sample.comment_payload or [],
            account_posts=list((snapshot.posts if snapshot else None) or []),
            account_meta={
                "handle": getattr(snapshot, "handle", None),
                "profile_text": getattr(snapshot, "profile_text", None),
                "follower_count": getattr(snapshot, "follower_count", None),
                "total_post_count": getattr(snapshot, "total_post_count", None),
            },
            options={"use_llm": use_llm},
        )

        # --- Produce one outcome per requested dimension -------------------
        # Either the Applied-AI engine (single call, all dimensions) or the
        # built-in heuristics run concurrently.
        outcomes, engine_run = await self._produce_outcomes(
            task=task,
            sample=sample,
            requested=requested,
            agents=agents,
            agent_input=agent_input,
        )

        # --- Persist sequentially ---
        outputs: dict[str, AgentOutput] = {}
        step = 55 // max(1, len(requested))
        for name, output, duration_ms, error in outcomes:
            row = rows[name]
            row.duration_ms = duration_ms
            if error or output is None:
                row.status = TaskStatus.FAILED.value
                row.error_message = error or "agent returned no output"
            else:
                row.status = TaskStatus.COMPLETED.value
                row.risk_level = output.risk_level.value
                row.score = output.score
                row.summary = output.summary
                row.findings = self._as_json(output.findings)
                row.recommendations = output.recommendations
                row.metrics = output.metrics
                row.raw_output = output.meta
                outputs[name] = output
            task.progress += step
            await db.commit()

        if not outputs:
            raise ValidationError("all analysis agents failed")

        # --- Persist the engine's rich trace (served by /api/engine-trace) ---
        if engine_run is not None and engine_run.trace is not None:
            trace = engine_run.trace
            db.add(
                EngineTrace(
                    task_id=task.id,
                    backend=str(trace.get("backend") or "trust_engine"),
                    engine_schema_version=trace.get("engine_schema_version"),
                    overall_score=engine_run.overall_score,
                    overall_risk_level=engine_run.overall_risk_level,
                    visible_roles=trace.get("visible_roles"),
                    evidence_chain=trace.get("evidence_chain"),
                    major_evidence=trace.get("major_evidence"),
                    limitations=trace.get("limitations"),
                    dimension_assessments=trace.get("dimension_assessments"),
                    provider_errors=trace.get("provider_errors"),
                    model_metadata=trace.get("model_metadata"),
                    evidence_count=len(trace.get("evidence_chain") or []),
                    latency_ms=engine_run.latency_ms,
                )
            )
            await db.commit()

        # --- Fuse into the final report ---
        task.progress = 90
        await db.commit()

        report_input = AgentInput(
            task_id=task.id,
            text=sample.text_content,
            comments=sample.comment_payload or [],
            options={"use_llm": use_llm, "agent_results": outputs},
        )
        content = await self._registry.report_agent.analyze(report_input)

        # When the engine ran, its own aggregate (thresholds: medium 30 / high 60)
        # and its caveat-bearing summary are authoritative over the heuristic fuse.
        if engine_run is not None and engine_run.overall_score is not None:
            engine_level = content.risk_level
            if engine_run.overall_risk_level:
                try:
                    engine_level = RiskLevel(engine_run.overall_risk_level)
                except ValueError:
                    pass
            content = content.model_copy(
                update={
                    "overall_score": engine_run.overall_score,
                    "trust_score": round(100.0 - engine_run.overall_score, 1),
                    "risk_level": engine_level,
                    "summary": (engine_run.trace or {}).get("summary") or content.summary,
                }
            )

        db.add(
            Report(
                task_id=task.id,
                overall_score=content.overall_score,
                trust_score=content.trust_score,
                risk_level=content.risk_level.value,
                summary=content.summary,
                evidence=self._as_json(content.evidence),
                recommendations=content.recommendations,
                dimension_scores=content.dimension_scores,
                generated_at=utcnow(),
            )
        )
        task.status = TaskStatus.COMPLETED.value
        task.progress = 100
        task.finished_at = utcnow()
        await db.commit()
        logger.info(
            "analysis task %s completed: risk=%s score=%s",
            task.id,
            content.risk_level.value,
            content.overall_score,
        )

    async def _produce_outcomes(
        self,
        *,
        task: AnalysisTask,
        sample: ContentSample,
        requested: list[str],
        agents: list[BaseAgent],
        agent_input: AgentInput,
    ) -> tuple[
        list[tuple[str, AgentOutput | None, int | None, str | None]],
        EngineRunResult | None,
    ]:
        """One outcome per requested dimension, from the engine or the heuristics.

        The engine covers all dimensions in a single call, so it is invoked
        exactly once here; every requested dimension then reads its slice out
        of the returned bundle.
        """
        if self._engine_runner is not None and self._engine_runner.available:
            run = await self._engine_runner.run(task_id=task.id, sample=sample)
            if run.available:
                logger.info(
                    "task %s analysed by trust engine (overall=%s, %sms)",
                    task.id,
                    run.overall_score,
                    run.latency_ms,
                )
                return (
                    [
                        (name, run.dimension_outputs.get(name), run.latency_ms, None)
                        for name in requested
                    ],
                    run,
                )
            logger.info(
                "task %s: trust engine produced no usable result (%s); using heuristics",
                task.id,
                run.error,
            )

        gathered = await asyncio.gather(
            *(self._run_agent(agent, agent_input) for agent in agents)
        )
        return (
            [
                (name, output, duration_ms, error)
                for name, (output, duration_ms, error) in zip(requested, gathered)
            ],
            None,
        )

    async def _run_agent(
        self, agent: BaseAgent, payload: AgentInput
    ) -> tuple[AgentOutput | None, int, str | None]:
        started = time.perf_counter()
        try:
            output = await agent.analyze(payload)
            return output, int((time.perf_counter() - started) * 1000), None
        except Exception as exc:  # noqa: BLE001 - isolate one agent's failure
            logger.exception("agent %s failed", agent.name)
            return None, int((time.perf_counter() - started) * 1000), str(exc)

    async def _fail(self, db: AsyncSession, task_id: str, exc: Exception) -> None:
        task = await db.get(AnalysisTask, task_id)
        if task is None:
            return
        task.status = TaskStatus.FAILED.value
        task.error_message = str(exc)[:1000]
        task.finished_at = utcnow()
        await db.commit()

    @staticmethod
    def _as_json(items: list[Any]) -> list[dict[str, Any]]:
        """Pydantic models -> plain JSON-serializable dicts for JSON columns."""
        return [json.loads(item.model_dump_json()) for item in items]

    @staticmethod
    def analysis_agent_names() -> list[str]:
        return list(DIMENSION_ORDER)
