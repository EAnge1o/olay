from content_trust.analyzers.comments import CommentAnalyzer
from content_trust.schemas import (
    CommentAnalysisResult,
    CommentInput,
)


class ClusterReviewProvider:
    def __init__(self):
        self.calls = []

    def analyze_comment_patterns(self, clusters, *, total_comment_count):
        self.calls.append((clusters, total_comment_count))
        return CommentAnalysisResult(
            available=True,
            total_comment_count=total_comment_count,
            llm_input_count=len(clusters),
            model_reviewed=True,
            pattern_clusters=[
                cluster.model_copy(
                    update={"verdict": "uncertain", "source": "llm"}
                )
                for cluster in clusters
            ],
        )


def test_comments_send_at_most_five_compressed_clusters_and_keep_coverage() -> None:
    comments = [
        CommentInput(id=f"c-{index}", text=f"第{index}条优惠信息，请进店看看")
        for index in range(10)
    ]
    provider = ClusterReviewProvider()

    result = CommentAnalyzer(provider).analyze(comments)

    assert len(provider.calls) == 1
    sent, full_count = provider.calls[0]
    assert len(sent) == 5
    assert full_count == 10
    assert all(not cluster.member_comment_ids for cluster in sent)
    assert result.total_comment_count == 10
    assert result.llm_input_count == 5
    assert result.model_reviewed is True
    assert result.suspicious_pattern_count == 0
    assert "不能证明" in result.summary


def test_repeated_comment_cluster_is_a_pattern_not_a_fake_review_verdict() -> None:
    comments = [
        CommentInput(id=f"c-{index}", text="这个精华真的很好用，日常保湿很舒服。")
        for index in range(3)
    ]

    result = CommentAnalyzer().analyze(comments)

    assert result.pattern_clusters[0].pattern_type == "repeated_template"
    assert result.pattern_clusters[0].verdict == "uncertain"
    assert result.pattern_clusters[0].count == 3
    assert "不证明" in result.pattern_clusters[0].reason
