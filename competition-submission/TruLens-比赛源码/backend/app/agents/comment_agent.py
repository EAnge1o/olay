"""Comment Analysis Agent — 评论区真实性核验.

Signals of a paid-comment (刷评) operation:
  * exact and near-duplicate comments (copy-paste armies)
  * generic one-liners ("好用"/"已下单") with no product specificity
  * burst activity: many comments clustered in a tiny time window
  * a small set of accounts contributing a large share of comments
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher

from app.agents.base import BaseAgent
from app.agents.knowledge import (
    BURST_MIN_COMMENTS,
    BURST_WINDOW_SECONDS,
    GENERIC_COMMENT_TERMS,
)
from app.core.enums import AgentName, RiskLevel
from app.core.utils import normalize_text
from app.schemas.agent import AgentInput, AgentOutput, Finding

NEAR_DUPLICATE_RATIO = 0.86
MAX_PAIR_COMPARISONS = 4000
MAX_GENERIC_LENGTH = 14


class CommentAgent(BaseAgent[AgentOutput]):
    name = AgentName.COMMENT.value
    display_name = "评论分析 Agent"
    description = "检测异常评论、重复评论与疑似刷评模式"

    async def analyze(self, payload: AgentInput) -> AgentOutput:
        comments = payload.comments or []
        if not comments:
            return AgentOutput(
                agent=self.name,
                risk_level=RiskLevel.LOW,
                score=0.0,
                summary="未提供评论数据，评论维度跳过。",
                findings=[],
                recommendations=["补充评论数据后可获得刷评风险分析。"],
                metrics={"total": 0},
                meta={"llm_augmented": False, "engine_status": "not_applicable"},
            )

        contents = [str(c.get("content", "")) for c in comments]
        norm = [normalize_text(c) for c in contents]

        dup_groups = self._duplicate_groups(norm)
        dup_comment_count = sum(len(g) for g in dup_groups)
        dup_rate = dup_comment_count / len(comments)

        generic_idx = self._generic_indices(contents)
        generic_rate = len(generic_idx) / len(comments)

        burst = self._burst_window(comments)
        author_counts = Counter(c.get("author") for c in comments if c.get("author"))
        dominant_author = author_counts.most_common(1)[0] if author_counts else None
        author_rate = (dominant_author[1] / len(comments)) if dominant_author else 0.0

        findings, recommendations = self._build_findings(
            comments, contents, dup_groups, dup_rate, generic_idx, generic_rate, burst, dominant_author, author_rate
        )
        score = self._risk_score(dup_rate, generic_rate, burst, author_rate, dominant_author, len(comments))
        risk_level = RiskLevel.from_score(score)

        meta = {"llm_augmented": False}
        if self.wants_llm(payload):
            merged = await self._augment_with_llm(contents)
            if merged:
                findings.extend(merged["findings"])
                recommendations = list(dict.fromkeys(recommendations + merged["recommendations"]))
                meta["llm_augmented"] = True

        return AgentOutput(
            agent=self.name,
            risk_level=risk_level,
            score=score,
            summary=self._summary(score, len(comments), dup_rate, generic_rate, burst),
            findings=findings,
            recommendations=list(dict.fromkeys(recommendations)),
            metrics={
                "total": len(comments),
                "unique_normalized": len({n for n in norm if n}),
                "duplicate_rate": round(dup_rate, 3),
                "generic_rate": round(generic_rate, 3),
                "burst_detected": bool(burst),
                "unique_authors": len(author_counts),
                "top_author_share": round(author_rate, 3),
            },
            meta=meta,
        )

    # -- Detection ----------------------------------------------------------

    @staticmethod
    def _duplicate_groups(norm: list[str]) -> list[list[int]]:
        """Exact + near-duplicate clustering over normalized comment text."""
        exact: dict[str, list[int]] = defaultdict(list)
        for idx, text in enumerate(norm):
            if text:
                exact[text].append(idx)

        groups = [g for g in exact.values() if len(g) > 1]

        # Near-duplicates across distinct normalized forms (copy with tweaks).
        uniques = [(i, t) for i, t in enumerate(norm) if t]
        compared = 0
        seen: set[tuple[int, int]] = set()
        for a in range(len(uniques)):
            for b in range(a + 1, len(uniques)):
                if compared >= MAX_PAIR_COMPARISONS:
                    break
                ia, ta = uniques[a]
                ib, tb = uniques[b]
                if ta == tb:
                    continue
                compared += 1
                if abs(len(ta) - len(tb)) > max(4, len(ta) * 0.3):
                    continue
                if SequenceMatcher(None, ta, tb).ratio() >= NEAR_DUPLICATE_RATIO:
                    key = (min(ia, ib), max(ia, ib))
                    if key not in seen:
                        seen.add(key)
                        groups.append([ia, ib])
            if compared >= MAX_PAIR_COMPARISONS:
                break
        return groups

    @staticmethod
    def _generic_indices(contents: list[str]) -> list[int]:
        indices: list[int] = []
        for idx, content in enumerate(contents):
            norm = normalize_text(content)
            if len(norm) <= MAX_GENERIC_LENGTH and any(t in content for t in GENERIC_COMMENT_TERMS):
                indices.append(idx)
        return indices

    @staticmethod
    def _burst_window(comments: list[dict]) -> dict | None:
        stamps: list[float] = []
        for c in comments:
            ts = CommentAgent._parse_time(c.get("created_at"))
            if ts is not None:
                stamps.append(ts)
        if len(stamps) < BURST_MIN_COMMENTS:
            return None
        stamps.sort()
        best: dict | None = None
        start = 0
        for end in range(len(stamps)):
            while stamps[end] - stamps[start] > BURST_WINDOW_SECONDS:
                start += 1
            count = end - start + 1
            if count >= BURST_MIN_COMMENTS and (best is None or count > best["count"]):
                best = {"count": count, "span_seconds": int(stamps[end] - stamps[start])}
        return best

    @staticmethod
    def _parse_time(value) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None

    # -- Scoring & narrative -------------------------------------------------

    @staticmethod
    def _risk_score(
        dup_rate: float,
        generic_rate: float,
        burst: dict | None,
        author_rate: float,
        dominant_author,
        total: int,
    ) -> float:
        score = 0.0
        score += min(45.0, dup_rate * 90.0)          # duplicates dominate
        score += min(30.0, generic_rate * 60.0)      # low-quality filler
        if burst:
            score += 15.0
        if dominant_author and total >= 5 and author_rate > 0.4:
            score += min(12.0, author_rate * 20.0)
        return round(min(100.0, score), 1)

    def _build_findings(
        self, comments, contents, dup_groups, dup_rate, generic_idx, generic_rate, burst, dominant_author, author_rate
    ) -> tuple[list[Finding], list[str]]:
        findings: list[Finding] = []
        recommendations: list[str] = []

        if dup_groups:
            severity = RiskLevel.HIGH if dup_rate >= 0.3 else RiskLevel.MEDIUM
            findings.append(
                Finding(
                    type="duplicate_comment",
                    evidence=self._group_evidence(comments, contents, dup_groups[0]),
                    confidence=round(min(0.95, 0.7 + dup_rate), 3),
                    severity=severity,
                    detail=f"{len(dup_groups)} 组重复/近似重复评论，重复占比 {dup_rate:.0%}。",
                )
            )
            recommendations.append("排查重复评论来源，疑似同一批账号批量复制粘贴。")

        if generic_idx:
            severity = RiskLevel.MEDIUM if generic_rate < 0.5 else RiskLevel.HIGH
            findings.append(
                Finding(
                    type="low_quality_comment",
                    evidence=f"『{contents[generic_idx[0]]}』等 {len(generic_idx)} 条泛化好评，无产品细节",
                    confidence=0.72,
                    severity=severity,
                    location=f"comment#{generic_idx[0]}",
                    detail=f"泛化好评占比 {generic_rate:.0%}，缺乏具体使用体验。",
                )
            )
            recommendations.append("引导真实用户补充使用细节，过滤无信息量的泛化好评。")

        if burst:
            severity = RiskLevel.HIGH if burst["count"] >= 10 else RiskLevel.MEDIUM
            findings.append(
                Finding(
                    type="burst_activity",
                    evidence=f"{burst['count']} 条评论集中在 {burst['span_seconds']} 秒内发布",
                    confidence=0.65,
                    severity=severity,
                    detail="短时间集中发布是刷评的典型时间特征。",
                )
            )
            recommendations.append("核查异常时间窗内的评论账号真实性（关注/互动历史）。")

        if dominant_author and author_rate > 0.4:
            findings.append(
                Finding(
                    type="suspicious_author",
                    evidence=f"账号『{dominant_author[0]}』贡献了 {dominant_author[1]} 条评论（{author_rate:.0%}）",
                    confidence=0.6,
                    severity=RiskLevel.MEDIUM,
                    detail="单一账号占评论比例过高。",
                )
            )
            recommendations.append("核查高频评论账号与内容作者是否存在关联。")

        return findings, recommendations

    @staticmethod
    def _group_evidence(comments, contents, group: list[int]) -> str:
        first = contents[group[0]][:60]
        others = len(group) - 1
        return f"『{first}』及其 {others} 条高度相似评论"

    @staticmethod
    def _summary(score: float, total: int, dup_rate: float, generic_rate: float, burst) -> str:
        # No trailing 。 on the first part: the parts are rejoined with "，", so a
        # leading 。 would render as "。，".
        parts = [f"共 {total} 条评论，刷评风险分 {score}/100"]
        if dup_rate > 0:
            parts.append(f"重复率 {dup_rate:.0%}")
        if generic_rate > 0:
            parts.append(f"泛化好评率 {generic_rate:.0%}")
        if burst:
            parts.append(f"存在 {burst['count']} 条/{burst['span_seconds']}s 集中发布")
        return "，".join(parts) + "。"

    # -- Optional model augmentation ----------------------------------------

    async def _augment_with_llm(self, contents: list[str]) -> dict | None:
        sample = "\n".join(f"{i}: {c}" for i, c in enumerate(contents[:60]))
        prompt = (
            "以下是美妆内容的评论列表。请判断是否存在刷评/水军模式，"
            '以 JSON 返回 {"findings":[{"type":"","evidence":"","confidence":0.0}],'
            '"recommendations":[""]}。\n' + sample
        )
        data = await self.call_llm_json(system="仅输出 JSON。", prompt=prompt)
        if not data:
            return None
        findings: list[Finding] = []
        for item in data.get("findings", [])[:5]:
            try:
                findings.append(
                    Finding(
                        type=str(item.get("type", "llm_flag")),
                        evidence=str(item.get("evidence", ""))[:280],
                        confidence=float(item.get("confidence", 0.5)),
                        severity=RiskLevel.MEDIUM,
                        detail="由模型复核发现。",
                    )
                )
            except (TypeError, ValueError):
                continue
        return {
            "findings": findings,
            "recommendations": [str(r) for r in data.get("recommendations", [])[:4] if r],
        }
