"""Local comment normalization and clustering, followed by one compact LLM review."""

import re
from difflib import SequenceMatcher

from content_trust.providers.base import CommentAnalysisProvider
from content_trust.schemas import CommentAnalysisResult, CommentInput, CommentPatternCluster

_NORMALIZE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
_LINK_OR_CONTACT = re.compile(
    r"https?://|www\.|\b(?:微信|加v|加微|私信|vx|v信|扫码|群聊)\b|\d{7,}",
    re.IGNORECASE,
)
_PROMO = re.compile(r"领券|优惠|下单|购买|进店|点链接|私聊|代购|折扣|返现|抽奖")
_MAX_LLM_CLUSTERS = 5
_MAX_FUZZY_COMMENTS = 300


class CommentAnalyzer:
    def __init__(self, provider: CommentAnalysisProvider | None = None) -> None:
        self._provider = provider

    def analyze(self, comments: list[CommentInput]) -> CommentAnalysisResult:
        if not comments:
            return CommentAnalysisResult(
                available=False,
                error="no_comments",
                summary="No comments were supplied.",
            )

        clusters = _local_clusters(comments)
        if self._provider is None:
            return _local_result(comments, clusters)

        reviews = _compress_for_review(clusters)
        try:
            analyze_patterns = getattr(self._provider, "analyze_comment_patterns", None)
            if callable(analyze_patterns):
                reviewed = analyze_patterns(
                    reviews,
                    total_comment_count=len(comments),
                )
                expected_ids = {item.cluster_id for item in reviews}
                returned_ids = [item.cluster_id for item in reviewed.pattern_clusters]
                if set(returned_ids) != expected_ids or len(returned_ids) != len(expected_ids):
                    raise ValueError("comment_cluster_review_mismatch")
                clusters = _merge_reviews(clusters, reviewed.pattern_clusters)
            else:
                # Compatibility path for injected providers using the original contract.
                compact_comments = [
                    CommentInput(
                        id=cluster.cluster_id,
                        text=_cluster_prompt_text(cluster),
                    )
                    for cluster in reviews
                ]
                reviewed = self._provider.analyze_comments(compact_comments)
                clusters = _merge_legacy_reviews(clusters, reviewed.assessments)
        except Exception as exc:
            code = getattr(exc, "code", None) or (
                str(exc)
                if isinstance(exc, ValueError) and str(exc)
                else "provider_error"
            )
            local = _local_result(comments, clusters)
            return local.model_copy(update={"error": code})

        summary = _summarize_clusters(comments, clusters, model_reviewed=True)
        return CommentAnalysisResult(
            available=True,
            assessed_count=len(comments),
            total_comment_count=len(comments),
            llm_input_count=len(reviews),
            suspicious_pattern_count=sum(
                cluster.verdict == "suspicious" for cluster in clusters
            ),
            model_reviewed=True,
            assessments=[],
            pattern_clusters=clusters,
            cross_comment_patterns=[
                f"{cluster.pattern_type}: {cluster.count} comments"
                for cluster in clusters
                if cluster.count > 1 or cluster.pattern_type != "sampled_unique_comments"
            ],
            summary=summary,
        )


def _local_clusters(comments: list[CommentInput]) -> list[CommentPatternCluster]:
    normalized = [(_NORMALIZE.sub("", item.text.casefold()), item) for item in comments]
    exact: dict[str, list[CommentInput]] = {}
    for text, comment in normalized:
        if len(text) >= 8:
            exact.setdefault(text, []).append(comment)

    clusters: list[CommentPatternCluster] = []
    covered: set[str] = set()
    for members in exact.values():
        if len(members) < 2:
            continue
        cluster_id = f"comment-pattern-{len(clusters) + 1}"
        clusters.append(
            CommentPatternCluster(
                cluster_id=cluster_id,
                pattern_type="repeated_template",
                member_comment_ids=[item.id for item in members],
                count=len(members),
                representative_text=members[0].text,
                examples=list(dict.fromkeys(item.text for item in members[:3])),
                reason="本地规则发现多条完全相同的规范化文本；这只是重复模式，不证明评论虚假。",
            )
        )
        covered.update(item.id for item in members)

    for comment in comments:
        if comment.id in covered:
            continue
        pattern_type = None
        if _LINK_OR_CONTACT.search(comment.text):
            pattern_type = "contact_or_link"
        elif _PROMO.search(comment.text):
            pattern_type = "irrelevant_promotion"
        if pattern_type:
            clusters.append(
                CommentPatternCluster(
                    cluster_id=f"comment-pattern-{len(clusters) + 1}",
                    pattern_type=pattern_type,
                    member_comment_ids=[comment.id],
                    count=1,
                    representative_text=comment.text,
                    examples=[comment.text],
                    reason="本地规则发现导流或促销文本线索；需要结合上下文人工复核。",
                )
            )
            covered.add(comment.id)

    if len(comments) <= _MAX_FUZZY_COMMENTS:
        candidates = [
            (text, comment)
            for text, comment in normalized
            if comment.id not in covered and len(text) >= 12
        ]
        fuzzy_groups: list[list[CommentInput]] = []
        for text, comment in candidates:
            for group in fuzzy_groups:
                representative = _NORMALIZE.sub("", group[0].text.casefold())
                if SequenceMatcher(None, text, representative).ratio() >= 0.90:
                    group.append(comment)
                    break
            else:
                fuzzy_groups.append([comment])
        for members in fuzzy_groups:
            if len(members) < 2:
                continue
            clusters.append(
                CommentPatternCluster(
                    cluster_id=f"comment-pattern-{len(clusters) + 1}",
                    pattern_type="near_duplicate",
                    member_comment_ids=[item.id for item in members],
                    count=len(members),
                    representative_text=members[0].text,
                    examples=list(dict.fromkeys(item.text for item in members[:3])),
                    reason="本地字符相似度筛查发现相似表达；这不能证明协同行为或评论虚假。",
                )
            )
            covered.update(item.id for item in members)

    remaining = [item for item in comments if item.id not in covered]
    if remaining:
        samples = _sample_comments(remaining, limit=5)
        clusters.append(
            CommentPatternCluster(
                cluster_id=f"comment-pattern-{len(clusters) + 1}",
                pattern_type="sampled_unique_comments",
                member_comment_ids=[item.id for item in remaining],
                count=len(remaining),
                representative_text="\n".join(item.text for item in samples),
                examples=[item.text for item in samples],
                reason=(
                    "这些评论没有进入本地重复/导流模式簇；只抽取有限样例供模型总结，"
                    "不代表所有成员都已逐条核验。"
                ),
            )
        )
    return clusters


def _compress_for_review(
    clusters: list[CommentPatternCluster],
) -> list[CommentPatternCluster]:
    ordered = sorted(
        clusters,
        key=lambda item: (
            item.pattern_type == "sampled_unique_comments",
            -item.count,
            item.cluster_id,
        ),
    )
    if len(ordered) <= _MAX_LLM_CLUSTERS:
        selected = ordered
    else:
        selected = ordered[:_MAX_LLM_CLUSTERS]
    return [
        item.model_copy(
            update={
                "member_comment_ids": [],
                "representative_text": item.representative_text[:1200],
                "examples": [example[:500] for example in item.examples[:3]],
            }
        )
        for item in selected
    ]


def _merge_reviews(
    local: list[CommentPatternCluster], reviewed: list[CommentPatternCluster]
) -> list[CommentPatternCluster]:
    reviews = {item.cluster_id: item for item in reviewed}
    merged = []
    for cluster in local:
        review = reviews.get(cluster.cluster_id)
        if review is None:
            merged.append(cluster)
            continue
        merged.append(
            cluster.model_copy(
                update={
                    "source": "llm",
                    "verdict": review.verdict,
                    "confidence": review.confidence,
                    "reason": review.reason or cluster.reason,
                    "recommended_action": review.recommended_action,
                }
            )
        )
    return merged


def _merge_legacy_reviews(local, assessments):
    reviews = {item.comment_id: item for item in assessments}
    merged = []
    for cluster in local:
        review = reviews.get(cluster.cluster_id)
        if review is None:
            merged.append(cluster)
            continue
        merged.append(
            cluster.model_copy(
                update={
                    "source": "llm",
                    "verdict": review.verdict,
                    "confidence": review.confidence,
                    "reason": review.reason,
                    "recommended_action": review.recommended_action,
                }
            )
        )
    return merged


def _local_result(comments, clusters):
    return CommentAnalysisResult(
        available=True,
        assessed_count=len(comments),
        total_comment_count=len(comments),
        llm_input_count=0,
        suspicious_pattern_count=0,
        model_reviewed=False,
        pattern_clusters=clusters,
        cross_comment_patterns=[
            f"{cluster.pattern_type}: {cluster.count} comments"
            for cluster in clusters
            if cluster.count > 1 or cluster.pattern_type != "sampled_unique_comments"
        ],
        summary=_summarize_clusters(comments, clusters, model_reviewed=False),
        error=None,
    )


def _summarize_clusters(comments, clusters, *, model_reviewed: bool) -> str:
    patterns = [item for item in clusters if item.pattern_type != "sampled_unique_comments"]
    if not patterns:
        base = "本地筛查未发现明显重复模板或导流模式。"
    else:
        detail = "；".join(
            f"{item.pattern_type} 涉及 {item.count} 条"
            for item in patterns
        )
        base = f"发现可复核的评论文本模式：{detail}。"
    if model_reviewed:
        base += "DeepSeek 只复核压缩后的模式簇和少量代表样例。"
    else:
        base += "当前只运行本地规则，未运行 DeepSeek 模式复核。"
    base += "文本相似或促销本身不能证明评论虚假、账号协同或付费关系。"
    return base


def _cluster_prompt_text(cluster: CommentPatternCluster) -> str:
    examples = "\n".join(f"- {item}" for item in cluster.examples)
    return (
        f"模式类型={cluster.pattern_type}; 涉及条数={cluster.count}; "
        f"成员ID={','.join(cluster.member_comment_ids)}\n代表文本："
        f"{cluster.representative_text}\n样例：\n{examples}"
    )


def _sample_comments(comments: list[CommentInput], limit: int) -> list[CommentInput]:
    if len(comments) <= limit:
        return comments
    indices = [round(index * (len(comments) - 1) / (limit - 1)) for index in range(limit)]
    return [comments[index] for index in dict.fromkeys(indices)]
