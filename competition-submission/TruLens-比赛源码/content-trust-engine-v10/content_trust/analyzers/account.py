"""Account-history screening with a conservative minimum-data gate."""

from content_trust.schemas.agent import AccountAnalysisResult, AccountHistoryInput


class AccountHistoryAnalyzer:
    def __init__(self, provider=None, *, minimum_posts: int = 3) -> None:
        self._provider = provider
        self._minimum_posts = minimum_posts

    def analyze(
        self, history: AccountHistoryInput, *, locale: str = "zh-CN"
    ) -> AccountAnalysisResult:
        if len(history.posts) < self._minimum_posts:
            return AccountAnalysisResult(
                available=False,
                assessed_post_count=len(history.posts),
                error="insufficient_history",
                summary=(
                    f"账号历史样本不足：至少需要 {self._minimum_posts} 条可比较帖子；"
                    f"当前收到 {len(history.posts)} 条。"
                ),
            )
        if self._provider is None:
            return AccountAnalysisResult(
                available=False,
                assessed_post_count=len(history.posts),
                error="provider_not_configured",
                summary="账号历史分析模型未配置。",
            )
        analyze = getattr(self._provider, "analyze_account_history", None)
        if not callable(analyze):
            return AccountAnalysisResult(
                available=False,
                assessed_post_count=len(history.posts),
                error="provider_unsupported",
                summary="当前语义模型未提供账号历史分析接口。",
            )
        try:
            result = analyze(history, locale=locale)
        except Exception as exc:
            code = getattr(exc, "code", "provider_error")
            return AccountAnalysisResult(
                available=False,
                assessed_post_count=len(history.posts),
                error=code,
                summary="账号历史分析暂不可用；不据此推断账号异常。",
            )
        if result.assessed_post_count > len(history.posts):
            return AccountAnalysisResult(
                available=False,
                assessed_post_count=len(history.posts),
                error="invalid_provider_count",
                summary="账号历史分析返回了超出输入样本数的结果，已忽略。",
            )
        valid_ids = {post.id for post in history.posts}
        if any(
            post_id not in valid_ids
            for finding in result.findings
            for post_id in finding.post_ids
        ):
            return AccountAnalysisResult(
                available=False,
                assessed_post_count=len(history.posts),
                error="invalid_provider_reference",
                summary="账号分析结果引用了未提供的帖子，已忽略。",
            )
        return result
