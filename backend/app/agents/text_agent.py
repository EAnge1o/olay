"""Text Analysis Agent — 文案可信度核验.

Detects, in priority order:
  1. 医疗功效宣称 (medical claims) — the highest regulatory risk
  2. 绝对化用语 (absolute superlatives)
  3. 功效夸大 (efficacy exaggeration)
  4. 虚假专家背书 (unverifiable authority claims)
  5. AI 生成文案风险 (templated, experience-less copy)

Heuristic-first, model-augmented-second: the rule pass always runs and is
deterministic; when ``use_llm`` is on, an LLM second opinion is merged in.
"""

from __future__ import annotations

import re

from app.agents.base import BaseAgent
from app.agents.knowledge import (
    ABSOLUTE_TERMS,
    AD_DISCLOSURE_TERMS,
    AI_TEMPLATE_MARKERS,
    EXAGGERATION_TERMS,
    MEDICAL_TERMS,
    EXPERT_ENTITIES,
    PERSONAL_EXPERIENCE_MARKERS,
    TEXT_CATEGORY_RULES,
)
from app.core.enums import AgentName, RiskLevel
from app.schemas.agent import AgentInput, AgentOutput, Finding

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\u2700-\u27bf]+"
)
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?\n]+")

MIN_TEXT_LENGTH = 20


class TextAgent(BaseAgent[AgentOutput]):
    name = AgentName.TEXT.value
    display_name = "文案分析 Agent"
    description = "检测虚假宣传、AI生成文案风险、功效夸大与虚假专家背书"

    async def analyze(self, payload: AgentInput) -> AgentOutput:
        text = (payload.text or "").strip()
        if len(text) < MIN_TEXT_LENGTH:
            return AgentOutput(
                agent=self.name,
                risk_level=RiskLevel.LOW,
                score=0.0,
                summary="未提供足够文案（少于20字），无法进行有效分析。",
                findings=[],
                recommendations=["请补充完整种草文案后再分析。"],
                metrics={"length": len(text)},
                meta={"llm_augmented": False},
            )

        hits: dict[str, list[dict]] = {}
        for category, rule in TEXT_CATEGORY_RULES.items():
            matched = self._scan(text, rule["terms"])
            if matched:
                hits[category] = matched

        findings, recommendations = self._build_findings(text, hits)

        ai_score = self._ai_generated_score(text)
        if ai_score >= 35:
            confidence = round(min(0.9, 0.4 + ai_score / 200), 3)
            findings.append(
                Finding(
                    type="ai_generated_text",
                    evidence=self._excerpt(text, 0, 80),
                    confidence=confidence,
                    severity=RiskLevel.MEDIUM if ai_score < 70 else RiskLevel.HIGH,
                    detail=f"AI 生成特征分 {ai_score}/100：模板化结构、缺少个人体验细节。",
                )
            )
            recommendations.append(
                "增加真实使用细节（时间、肤质、前后对比），降低模板化与 AI 生成观感。"
            )

        if not any(term in text for term in AD_DISCLOSURE_TERMS) and any(
            c in hits for c in ("medical_claim", "exaggeration", "fake_expert")
        ):
            findings.append(
                Finding(
                    type="undisclosed_ad",
                    evidence="全文未见任何广告/合作披露标识",
                    confidence=0.6,
                    severity=RiskLevel.MEDIUM,
                    detail="存在强功效宣称但未标注广告或合作关系，涉嫌隐形广告。",
                )
            )
            recommendations.append("若内容涉及商业合作，请在显著位置标注广告/合作关系。")

        score = self._risk_score(hits, ai_score)
        risk_level = RiskLevel.from_score(score)

        meta = {"llm_augmented": False, "length": len(text), "ai_generated_score": ai_score}
        if self.wants_llm(payload):
            merged = await self._augment_with_llm(text, hits)
            if merged:
                findings.extend(merged["findings"])
                recommendations = list(dict.fromkeys(recommendations + merged["recommendations"]))
                meta["llm_augmented"] = True

        return AgentOutput(
            agent=self.name,
            risk_level=risk_level,
            score=score,
            summary=self._summary(score, findings),
            findings=findings,
            recommendations=list(dict.fromkeys(recommendations)),
            metrics={
                "length": len(text),
                "category_hits": {k: len(v) for k, v in hits.items()},
                "ai_generated_score": ai_score,
            },
            meta=meta,
        )

    # -- Heuristics ---------------------------------------------------------

    @staticmethod
    def _scan(text: str, terms: tuple[str, ...]) -> list[dict]:
        results: list[dict] = []
        lowered = text.lower()
        for term in terms:
            start = 0
            while True:
                idx = lowered.find(term.lower(), start)
                if idx == -1:
                    break
                results.append({"term": term, "index": idx})
                start = idx + len(term)
        return results

    @staticmethod
    def _excerpt(text: str, index: int, length: int) -> str:
        return text[max(0, index - 6) : index + length + 6].replace("\n", " ").strip()

    def _build_findings(self, text: str, hits: dict[str, list[dict]]) -> tuple[list[Finding], list[str]]:
        findings: list[Finding] = []
        recommendations: list[str] = []
        for category, matched in hits.items():
            rule = TEXT_CATEGORY_RULES[category]
            for item in matched[:3]:
                findings.append(
                    Finding(
                        type=rule["type"],
                        evidence=self._excerpt(text, item["index"], len(item["term"])),
                        confidence=0.88 if category in ("medical_claim", "absolute_claim") else 0.78,
                        severity=rule["severity"],
                        location=f"text[{item['index']}:{item['index'] + len(item['term'])}]",
                        detail=f"命中『{item['term']}』，属于{category}风险类别。",
                    )
                )
            recommendations.append(rule["recommendation"])
        return findings, recommendations

    def _ai_generated_score(self, text: str) -> int:
        """0-100 likelihood that the copy is machine-generated."""
        score = 0
        template_hits = sum(1 for term in AI_TEMPLATE_MARKERS if term in text)
        personal_hits = sum(1 for term in PERSONAL_EXPERIENCE_MARKERS if term in text)
        emoji_count = len(_EMOJI_RE.findall(text))
        sentences = [s for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
        avg_len = sum(len(s) for s in sentences) / max(1, len(sentences))

        score += min(40, template_hits * 8)              # templated connectives
        score += max(0, (5 - personal_hits)) * 6         # absence of lived detail
        score += min(30, emoji_count * 3)                # emoji padding
        if sentences and (avg_len < 12 or avg_len > 60):  # unnatural rhythm
            score += 10
        if len(set(sentences)) != len(sentences) and len(sentences) > 3:  # repetition
            score += 12
        return min(100, score)

    @staticmethod
    def _risk_score(hits: dict[str, list[dict]], ai_score: int) -> float:
        score = 0.0
        for category, matched in hits.items():
            weight = TEXT_CATEGORY_RULES[category]["weight"]
            score += weight * (1 + min(len(matched) - 1, 3) * 0.12)
        score += 26.0 * (ai_score / 100.0)
        return round(min(100.0, score), 1)

    @staticmethod
    def _summary(score: float, findings: list[Finding]) -> str:
        if not findings:
            return f"文案整体可信，风险分 {score}/100，未发现明显虚假宣传特征。"
        top = ", ".join(sorted({f.type for f in findings})[:4])
        return f"文案风险分 {score}/100，共 {len(findings)} 处风险点，主要问题：{top}。"

    # -- Optional model augmentation ----------------------------------------

    async def _augment_with_llm(self, text: str, hits: dict[str, list[dict]]) -> dict | None:
        prompt = (
            "你是美妆广告合规审核员。请判断下列种草文案是否存在虚假宣传、功效夸大、"
            "医疗宣称或虚假专家背书，并以 JSON 返回："
            '{"findings":[{"type":"","evidence":"","confidence":0.0}],"recommendations":[""]}\n'
            f"文案：{text[:2000]}"
        )
        data = await self.call_llm_json(
            system="仅输出 JSON，不要解释。confidence 取 0-1。",
            prompt=prompt,
        )
        if not data:
            return None
        findings: list[Finding] = []
        for item in data.get("findings", [])[:6]:
            try:
                findings.append(
                    Finding(
                        type=str(item.get("type", "llm_flag")),
                        evidence=str(item.get("evidence", ""))[:280],
                        confidence=float(item.get("confidence", 0.5)),
                        severity=RiskLevel.HIGH
                        if str(item.get("severity", "medium")) == "high"
                        else RiskLevel.MEDIUM,
                        detail="由模型复核发现。",
                    )
                )
            except (TypeError, ValueError):
                continue
        return {
            "findings": findings,
            "recommendations": [str(r) for r in data.get("recommendations", [])[:4] if r],
        }
