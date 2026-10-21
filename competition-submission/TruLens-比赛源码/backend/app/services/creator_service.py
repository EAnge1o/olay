"""Creator pre-publish issue extraction and rewrite suggestions."""

from __future__ import annotations

import re
from datetime import date

from app.schemas.creator import CreatorCheckRead, CreatorIssueRead
from app.schemas.report import ReportRead

RULES: list[tuple[str, str, str, str, str | None, bool]] = [
    (
        "absolute-comparison",
        "must",
        "绝对化比较",
        r"(最有效|最好用|第一名|全网第一)",
        "目前用下来我个人觉得更适合我的肤质",
        "避免把个人感受表达成适用于所有人的比较结论。",
        False,
    ),
    (
        "fast-effect",
        "must",
        "效果承诺",
        r"((?:\d+|[一二三四五六七])天.{0,12}(?:变白|美白|见效|改善)|立刻.{0,8}(?:白|见效|改善)|马上.{0,8}(?:白|见效|改善))",
        "我连续用了【X】天，个人感觉【你的真实感受】，效果因人而异",
        "短周期确定性效果需要真实使用条件和个人体验边界。",
        False,
    ),
    (
        "sensitive-safety",
        "must",
        "安全性绝对化",
        r"((?:敏感肌|孕妇|所有肤质).{0,14}(?:放心|可用|都能|都可以|闭眼冲))",
        "敏感肌或孕期人群建议先咨询医生或做局部测试；我自己的肤质是【你的肤质】",
        "特殊人群的适用性不宜作统一保证。",
        False,
    ),
    (
        "absolute-safe",
        "must",
        "绝对化",
        r"(100%|百分百|零风险|绝对安全|保证有效|无刺激)",
        "我用下来没有刺痛感，但每个人情况不同",
        "安全和刺激感受会受到肤质、用法与个体差异影响。",
        False,
    ),
    (
        "purchase-push",
        "suggest",
        "过度购买引导",
        r"(闭眼冲|无脑入|果断入手|赶紧买|直接拍)",
        "建议根据肤质和实际需求理性选择",
        "密集购买引导容易削弱内容分享属性。",
        False,
    ),
    (
        "dismiss-others",
        "suggest",
        "贬低同行",
        r"(智商税|别的都是|其他都是)",
        "建议删除",
        "减少对其他产品或消费者选择的否定性表达。",
        True,
    ),
]


def build_creator_check(
    *,
    draft: str,
    report: ReportRead,
    is_collaboration: bool,
) -> CreatorCheckRead:
    dimensions = {dimension.code: dimension for dimension in report.dimensions}
    text_dimension = dimensions["S"]
    image_dimension = dimensions["R"]
    issues = _extract_rule_issues(draft)
    issues.extend(_agent_issues(draft, text_dimension, {issue.id for issue in issues}))

    disclosure_text = _disclosure_text(draft, is_collaboration)
    if disclosure_text not in draft:
        issues.append(
            CreatorIssueRead(
                id="disclosure",
                severity="tip",
                type="披露",
                suggestion=disclosure_text,
                reason="清楚说明内容与品牌、产品之间的关系，更便于读者判断。",
            )
        )

    copy_score = (
        float(text_dimension.trust_score)
        if text_dimension.assessed and text_dimension.trust_score is not None
        else None
    )
    image_score = float(image_dimension.trust_score) if image_dimension.assessed else None
    has_must = any(issue.severity == "must" for issue in issues)
    publication_state = (
        "hold"
        if text_dimension.risk_level == "high"
        or (copy_score is not None and copy_score < 50)
        or (image_score is not None and image_score < 50)
        else "revise"
        if has_must
        else "ready"
    )
    publication_title = {
        "ready": "可以发布",
        "revise": "建议修改后发布",
        "hold": "建议暂缓发布",
    }[publication_state]

    return CreatorCheckRead(
        task_id=report.task_id,
        sample_id="",
        publication_state=publication_state,
        publication_title=publication_title,
        copy_score=copy_score,
        image_score=image_score,
        issues=issues,
        disclosures=_disclosures(draft, is_collaboration),
        good_points=_good_points(draft),
        check_date=date.today().isoformat(),
        report=report,
    )


def _extract_rule_issues(draft: str) -> list[CreatorIssueRead]:
    issues: list[CreatorIssueRead] = []
    seen: set[str] = set()
    for rule_id, severity, issue_type, pattern, suggestion, reason, remove in RULES:
        match = re.search(pattern, draft, flags=re.IGNORECASE)
        if match is None:
            continue
        original = match.group(1)
        if original in seen:
            continue
        seen.add(original)
        issues.append(
            CreatorIssueRead(
                id=f"{rule_id}-{len(issues)}",
                severity=severity,
                type=issue_type,
                original=original,
                suggestion=suggestion,
                reason=reason,
                remove=remove,
            )
        )
    return issues


def _agent_issues(draft: str, dimension, existing_ids: set[str]) -> list[CreatorIssueRead]:
    if not dimension.assessed or dimension.risk_level == "low":
        return []
    result: list[CreatorIssueRead] = []
    for index, evidence in enumerate(dimension.evidence[:3]):
        issue_id = f"agent-{evidence.type}-{index}"
        if issue_id in existing_ids:
            continue
        original = _find_original_claim(draft, evidence.evidence)
        result.append(
            CreatorIssueRead(
                id=issue_id,
                severity="must" if evidence.severity == "high" else "suggest",
                type=_issue_label(evidence.type),
                original=original,
                suggestion=_suggestion_for(evidence.type),
                reason=evidence.reason or evidence.evidence,
            )
        )
    return result


def _find_original_claim(draft: str, evidence_text: str) -> str | None:
    quoted = re.findall(r"[“\"]([^”\"]{2,30})[”\"]", evidence_text)
    for phrase in quoted:
        if phrase in draft:
            return phrase
    return None


def _issue_label(evidence_type: str) -> str:
    value = evidence_type.lower()
    if "exaggerated" in value or "absolute" in value:
        return "绝对化功效"
    if "unsupported" in value or "claim" in value:
        return "缺少依据"
    if "medical" in value:
        return "医疗化表述"
    return "文案风险"


def _suggestion_for(evidence_type: str) -> str:
    value = evidence_type.lower()
    if "unsupported" in value or "claim" in value:
        return "补充可核验依据，或改为主观体验并注明个体差异。"
    if "medical" in value:
        return "删除医疗化功效承诺，改为合规的日常使用感受。"
    return "弱化绝对化表述，保留个人体验边界和真实使用条件。"


def _disclosure_text(draft: str, is_collaboration: bool) -> str:
    if re.search(r"品牌合作|产品赠送|合作款|赠品", draft):
        return "本篇含品牌合作或产品赠送。"
    return "本篇含品牌合作或产品赠送。" if is_collaboration else "本产品为自费购买。"


def _disclosures(draft: str, is_collaboration: bool) -> dict[str, bool]:
    return {
        "合作或赠品披露": bool(
            re.search(r"品牌合作|产品赠送|合作款|赠品|自费购买", draft)
        )
        or is_collaboration
        and bool(re.search(r"品牌合作|产品赠送|合作款|赠品", draft)),
        "修图披露": bool(re.search(r"轻度磨皮|滤镜|修图", draft)),
        "使用周期说明": bool(re.search(r"连续用了|使用了.{0,6}天|使用周期", draft)),
        "个人体验边界声明": bool(
            re.search(r"个人觉得|个人感觉|因人而异|每个人情况不同|我用下来", draft)
        ),
        "数据来源说明": bool(re.search(r"数据|来源|检测|测试", draft)),
    }


def _good_points(draft: str) -> list[str]:
    points: list[str] = []
    if re.search(r"连续用了|使用了.{0,6}天|用了.{0,6}周", draft):
        points.append("包含使用周期信息")
    if re.search(r"个人觉得|个人感觉|因人而异|每个人情况不同", draft):
        points.append("保留了个人体验边界")
    if re.search(r"成分|浓度|配方", draft):
        points.append("提及成分或配方信息")
    if re.search(r"品牌合作|产品赠送|合作款|赠品|自费购买", draft):
        points.append("包含合作或购买关系说明")
    return points
