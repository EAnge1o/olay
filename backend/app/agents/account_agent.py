"""Account Analysis Agent — 账号可信度（T 维度）。

Fills the fourth dimension the frontend shows as ``T / 账号可信度``: whether an
account looks like a stable publisher or a commercial posting machine.

Scope guard (mirrors the engine's own rule): this only screens *patterns* in the
history the caller supplied. It never infers account identity, motivation, or
whether the account is a "bot" — and with fewer than three comparable posts it
reports ``insufficient_data`` rather than guessing.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher

from app.agents.base import BaseAgent
from app.core.enums import AgentName, DIMENSION_META, RiskLevel
from app.core.utils import normalize_text
from app.schemas.agent import AgentInput, AgentOutput, Finding

_META = DIMENSION_META[AgentName.ACCOUNT.value]

MIN_POSTS_FOR_PATTERN_SCREENING = 3
NEAR_DUPLICATE_RATIO = 0.85

# 商业导流标记 — a high density of these is the main "带货号" signal.
COMMERCIAL_MARKERS: tuple[str, ...] = (
    "链接", "购买", "下单", "领券", "优惠", "旗舰店", "橱窗", "小黄车", "商品卡",
    "合作", "推广", "赞助", "品牌方", "佣金", "带货", "同款", "http", "www.",
)
_COMMERCIAL_RE = re.compile("|".join(re.escape(m) for m in COMMERCIAL_MARKERS), re.IGNORECASE)


class AccountAgent(BaseAgent[AgentOutput]):
    name = AgentName.ACCOUNT.value
    display_name = "账号分析 Agent"
    description = _META.label + "：发布节奏、带货占比与历史内容一致性"

    async def analyze(self, payload: AgentInput) -> AgentOutput:
        posts = [p for p in (payload.account_posts or []) if isinstance(p, dict)]
        if len(posts) < MIN_POSTS_FOR_PATTERN_SCREENING:
            return self._insufficient(len(posts))

        texts = [str(p.get("text") or "") for p in posts]
        non_empty = [t for t in texts if t.strip()]

        commercial_ratio = self._commercial_ratio(non_empty)
        burst = self._burst(posts)
        repetition = self._repetition(non_empty)

        findings: list[Finding] = []
        recommendations: list[str] = []
        score = 0.0

        if commercial_ratio >= 0.5:
            severity = RiskLevel.HIGH if commercial_ratio >= 0.75 else RiskLevel.MEDIUM
            score += 35.0 if commercial_ratio >= 0.75 else 22.0
            findings.append(
                Finding(
                    type="commercial_density",
                    evidence=f"{len(non_empty)} 篇历史内容中 {commercial_ratio:.0%} 含商品/导流标记",
                    confidence=round(min(0.9, 0.5 + commercial_ratio / 2), 3),
                    severity=severity,
                    detail="带货占比偏高，历史内容的独立体验价值有限。",
                )
            )
            recommendations.append("核实历史内容的商业合作披露比例，避免纯导流内容被当作体验分享。")

        if burst is not None:
            score += 20.0
            findings.append(
                Finding(
                    type="posting_burst",
                    evidence=f"{burst['count']} 篇发布集中在 {burst['span_hours']} 小时内",
                    confidence=0.6,
                    severity=RiskLevel.MEDIUM,
                    detail="短时间集中发布，与日常分享节奏不符。",
                )
            )
            recommendations.append("核对集中发布时段的投放记录，判断是否为批量投放。")

        if repetition >= NEAR_DUPLICATE_RATIO:
            score += 25.0
            findings.append(
                Finding(
                    type="repetitive_posts",
                    evidence=f"历史内容高度同质（相似度 {repetition:.0%}）",
                    confidence=round(min(0.9, repetition), 3),
                    severity=RiskLevel.MEDIUM,
                    detail="多篇内容表述接近，存在模板化批量生产的迹象。",
                )
            )
            recommendations.append("抽查同质内容的原创程度，确认是否存在模板化批量发布。")

        score = round(min(100.0, score), 1)
        risk_level = RiskLevel.from_score(score)

        handle = payload.account_meta.get("handle")
        prefix = f"账号『{handle}』" if handle else "该账号"
        if not findings:
            recommendations.append("账号历史未发现明显异常模式；仍建议结合真实互动情况人工确认。")
            summary = f"{prefix}历史内容（{len(posts)} 篇）未发现明显异常模式。"
        else:
            summary = f"{prefix}历史内容（{len(posts)} 篇）发现 {len(findings)} 处可疑模式。"

        return AgentOutput(
            agent=self.name,
            risk_level=risk_level,
            score=score,
            summary=summary,
            findings=findings,
            recommendations=recommendations,
            metrics={
                "assessed_post_count": len(posts),
                "commercial_ratio": round(commercial_ratio, 3),
                "repetition_ratio": round(repetition, 3),
                "burst_detected": burst is not None,
            },
            meta={
                "provider": "local_heuristic",
                "engine_status": "assessed",
                # The engine states this caveat for the account role; we keep it
                # identical so both backends read the same way.
                "limitations": ["账号分析只覆盖调用方提供的历史样本，不代表完整账号画像。"],
            },
        )

    # -- heuristics ----------------------------------------------------------

    def _insufficient(self, provided: int) -> AgentOutput:
        return AgentOutput(
            agent=self.name,
            risk_level=RiskLevel.LOW,
            score=0.0,
            summary=f"账号历史仅 {provided} 篇，不足 {MIN_POSTS_FOR_PATTERN_SCREENING} 篇，无法进行模式筛查。",
            findings=[],
            recommendations=["提供至少 3 篇历史内容后再评估账号可信度。"],
            metrics={"assessed_post_count": provided},
            meta={"provider": "local_heuristic", "engine_status": "insufficient_data"},
        )

    @staticmethod
    def _commercial_ratio(texts: list[str]) -> float:
        if not texts:
            return 0.0
        hits = sum(1 for text in texts if _COMMERCIAL_RE.search(text))
        return hits / len(texts)

    @staticmethod
    def _burst(posts: list[dict]) -> dict | None:
        stamps: list[float] = []
        for post in posts:
            parsed = AccountAgent._parse_time(post.get("published_at"))
            if parsed is not None:
                stamps.append(parsed)
        if len(stamps) < MIN_POSTS_FOR_PATTERN_SCREENING:
            return None
        stamps.sort()
        best: dict | None = None
        start = 0
        for end in range(len(stamps)):
            while stamps[end] - stamps[start] > 24 * 3600:
                start += 1
            count = end - start + 1
            if count >= MIN_POSTS_FOR_PATTERN_SCREENING and (best is None or count > best["count"]):
                best = {
                    "count": count,
                    "span_hours": round((stamps[end] - stamps[start]) / 3600, 1),
                }
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
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    @staticmethod
    def _repetition(texts: list[str]) -> float:
        """Highest near-duplicate ratio across the history's normalized texts."""
        normalized = [normalize_text(t) for t in texts if t]
        if len(normalized) < 2:
            return 0.0
        counts = Counter(normalized)
        most_common = counts.most_common(1)[0]
        if most_common[1] > 1:
            return most_common[1] / len(normalized)

        best = 0.0
        for i in range(len(normalized)):
            for j in range(i + 1, len(normalized)):
                a, b = normalized[i], normalized[j]
                if not a or not b:
                    continue
                if abs(len(a) - len(b)) > max(4, len(a) * 0.3):
                    continue
                best = max(best, SequenceMatcher(None, a, b).ratio())
                if best >= 0.99:
                    return best
        return best
