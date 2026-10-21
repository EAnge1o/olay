"""Image Analysis Agent — 图片真实性（R 维度）。

Public key is ``image`` (not ``vision``) so it matches both the engine's role
name and the frontend's ``R`` dimension without a translation table.

This agent is deliberately an *interface placeholder with light heuristics*.
The production detector (AI-image classifiers, frequency-domain forensics,
CLIP-based image-caption consistency) plugs into the same ``analyze`` contract;
when the Applied-AI engine is enabled this agent is bypassed entirely and the
engine's GLM/Qwen vision role produces the dimension instead.
"""

from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.enums import AgentName, DIMENSION_META, RiskLevel
from app.schemas.agent import AgentInput, AgentOutput, Finding

# Dimensions emitted by popular text-to-image models at their default sizes.
GENERATIVE_DIMENSIONS: frozenset[tuple[int, int]] = frozenset(
    {(512, 512), (768, 768), (1024, 1024), (1024, 1536), (1536, 1024), (1280, 720)}
)

_META = DIMENSION_META[AgentName.IMAGE.value]


class ImageAgent(BaseAgent[AgentOutput]):
    name = AgentName.IMAGE.value
    display_name = "图片分析 Agent"
    description = _META.label + "：AI 生成图片风险与图文一致性"

    async def analyze(self, payload: AgentInput) -> AgentOutput:
        if not payload.image_path:
            # No image supplied -> the dimension is not applicable. The API layer
            # turns engine_status != "assessed" into score=null so the frontend can
            # render its "暂不评分" state instead of a misleading 0.
            return AgentOutput(
                agent=self.name,
                risk_level=RiskLevel.LOW,
                score=0.0,
                summary="未提供图片，图片真实性维度不适用。",
                findings=[],
                recommendations=["上传商品/效果图后可进行图片真实性分析。"],
                metrics={"available": False},
                meta={"provider": "stub", "available": False, "engine_status": "not_applicable"},
            )

        meta = payload.image_meta or {}
        findings: list[Finding] = []
        score = 0.0

        width, height = meta.get("width"), meta.get("height")
        has_exif = meta.get("has_exif")

        if has_exif is False:
            score += 18.0
            findings.append(
                Finding(
                    type="metadata_stripped",
                    evidence=f"图片 {width}x{height} 无 EXIF 元数据",
                    confidence=0.5,
                    severity=RiskLevel.MEDIUM,
                    detail="元数据被清除，常见于截图、二次转发或 AI 生成图。",
                )
            )

        if has_exif is False and (width, height) in GENERATIVE_DIMENSIONS:
            score += 30.0
            findings.append(
                Finding(
                    type="possible_ai_generated",
                    evidence=f"尺寸 {width}x{height} 为生成模型默认输出，且无相机元数据",
                    confidence=0.45,
                    severity=RiskLevel.HIGH,
                    detail="尺寸与元数据特征与 AI 生成图高度吻合，建议人工复核。",
                )
            )

        score = round(min(100.0, score), 1)
        risk_level = RiskLevel.from_score(score)

        recommendations = ["图片维度为占位实现，接入检测模型后可给出更可靠的判定。"]
        if findings:
            recommendations.append("对疑似生成或元数据异常图片进行人工复核。")

        meta_out = {
            "provider": "stub",
            "available": True,
            "detector": None,
            "engine_status": "assessed",
            "probed": {"width": width, "height": height, "has_exif": has_exif},
        }
        if self.wants_llm(payload):
            # Hook point for a multimodal model; no-op until one is wired in.
            meta_out["llm_requested"] = True

        return AgentOutput(
            agent=self.name,
            risk_level=risk_level,
            score=score,
            summary=(
                f"图片元数据已解析（{width}x{height}），发现 {len(findings)} 处可疑特征。"
                if findings
                else "图片元数据正常，未发现明显异常（占位级检测）。"
            ),
            findings=findings,
            recommendations=recommendations,
            metrics={"available": True, "findings": len(findings)},
            meta=meta_out,
        )
