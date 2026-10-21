/**
 * The real four-dimension report view, shared by the standalone live page and
 * by the app's `Result` screen.
 *
 * Renders the backend payload with the design's own `.unified-report-*` classes,
 * including the honest "暂不评分" state for a dimension that had nothing to
 * judge, and the per-dimension evidence + limitations.
 */

import { useEffect, useState } from "react";

import {
  getSampleTrace,
  sampleImageUrl,
  type DimensionCode,
  type DimensionRead,
  type ReportRead,
  type SampleRead,
  type SampleTraceRead,
} from "./api";
import {
  CrossCheckSection,
  DecisionSupport,
  MethodBadge,
  type CrossCheckItem,
} from "./TeammateUI";

export const ORDERED_CODES: DimensionCode[] = ["T", "R", "U", "S"];

export const CODE_FALLBACK_LABEL: Record<DimensionCode, string> = {
  T: "账号可信度",
  R: "图像原生性",
  U: "评论自然度",
  S: "文案真实性",
};

export function LiveReportView({
  report,
  sample,
  onContinue,
  continueLabel = "开始新的核验",
  technicalView = false,
}: {
  report: ReportRead;
  sample?: SampleRead | null;
  onContinue: () => void;
  continueLabel?: string;
  technicalView?: boolean;
}) {
  const byCode = new Map<DimensionCode, DimensionRead>(
    report.dimensions.map((dimension) => [dimension.code, dimension]),
  );
  const assessedCount = report.dimensions.filter((dimension) => dimension.assessed).length;
  const activeCodes = report.dimensions
    .filter((dimension) => dimension.assessed)
    .map((dimension) => dimension.code);
  const crossCheckItems: CrossCheckItem[] = report.cross_checks?.length
    ? report.cross_checks.map((item) => ({
        id: item.id,
        from: item.from_code,
        to: item.to_code,
        left: item.left,
        right: item.right,
        conclusion: item.conclusion,
        state: item.state,
        dimension: item.dimension,
        detail: item.detail,
        confidence: item.confidence ?? undefined,
        method: item.method,
        label: item.label ?? undefined,
      }))
    : getLiveCrossCheckItems(report);
  const [confidenceOpen, setConfidenceOpen] = useState(false);
  const [activeReportSection, setActiveReportSection] = useState("forensics-section");
  const [evidenceCode, setEvidenceCode] = useState<DimensionCode | null>(null);
  const [trace, setTrace] = useState<SampleTraceRead | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const nothingAssessed = report.assessed_dimensions.length === 0;
  const score = Math.round(report.trust_score);
  const tone = nothingAssessed ? "warning" : score >= 80 ? "trusted" : score >= 50 ? "warning" : "danger";
  const confidenceLevel = report.confidence?.level ?? (assessedCount === 4 ? "high" : assessedCount >= 2 ? "medium" : "low");
  const confidenceBars = report.confidence?.bars ?? (confidenceLevel === "high" ? 3 : confidenceLevel === "medium" ? 2 : 1);
  const missingLabels = report.confidence?.missing_materials ?? ORDERED_CODES
    .filter((code) => !byCode.get(code)?.assessed)
    .map((code) => crossDimensionName(code));
  const crossCheckCount = report.confidence?.cross_check_count ?? crossCheckItems.filter((item) => item.state !== "unverified").length;

  useEffect(() => {
    if (!sample?.id || !sample.image?.sha256) {
      setTrace(null);
      return;
    }
    let cancelled = false;
    setTraceLoading(true);
    void getSampleTrace(sample.id)
      .then((value) => {
        if (!cancelled) setTrace(value);
      })
      .catch(() => {
        if (!cancelled) setTrace(null);
      })
      .finally(() => {
        if (!cancelled) setTraceLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sample?.id, sample?.image?.sha256]);

  useEffect(() => {
    const sectionIds = ["forensics-section", "cross-check-section", "decision-section"];
    const observer = new IntersectionObserver((entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((left, right) => right.intersectionRatio - left.intersectionRatio)[0];
      if (visible) setActiveReportSection(visible.target.id);
    }, { rootMargin: "-24% 0px -58% 0px", threshold: [0.05, 0.25, 0.5] });
    sectionIds.forEach((id) => {
      const element = document.getElementById(id);
      if (element) observer.observe(element);
    });
    return () => observer.disconnect();
  }, [report.task_id]);

  const radar = ORDERED_CODES.map((code, index) => {
    const dimension = byCode.get(code);
    const radius =
      dimension?.assessed && dimension.trust_score != null ? dimension.trust_score * 0.9 : 0;
    if (index === 0) return [120, 110 - radius];
    if (index === 1) return [120 + radius, 110];
    if (index === 2) return [120, 110 + radius];
    return [120 - radius, 110];
  });

  return (
    <div className="unified-report-page">
      <article className={`unified-report-card ${tone}`}>
        <header>
          <div>
            <strong>TruLens 核验报告</strong>
            <small>
              实时核验结果 · 已评估 {assessedCount} / 4 个维度 · 任务 {report.task_id.slice(0, 8)}
              {technicalView ? " · 技术视图" : ""}
            </small>
          </div>
          <span>AI 生成报告</span>
        </header>

        <nav aria-label="报告章节导航" className="report-anchor-nav">
          {[
            ["forensics-section", "① 鉴伪结果"],
            ["cross-check-section", "② 一致性校验"],
            ["decision-section", "③ 决策与建议"],
          ].map(([id, label]) => (
            <button
              className={activeReportSection === id ? "active" : ""}
              key={id}
              onClick={() => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" })}
            >
              {label}
            </button>
          ))}
        </nav>

        <div className="unified-score-row">
          <div className="unified-score-cluster">
            <div className="unified-score">
              <span>Trust Score</span>
              <strong>
                {nothingAssessed ? (
                  "暂不评分"
                ) : (
                  <>
                    {score}
                    <small>/ 100</small>
                  </>
                )}
              </strong>
              <em>基于 {assessedCount}/4 个维度</em>
              {assessedCount < 2 && <p>材料有限，结论仅供参考</p>}
            </div>
            <div className="report-confidence-wrap">
              <button
                className={`report-confidence confidence-${confidenceLevel}`}
                onClick={() => setConfidenceOpen((open) => !open)}
              >
                <span>置信度</span>
                <strong>{report.confidence?.label ?? (confidenceLevel === "high" ? "高" : confidenceLevel === "medium" ? "中" : "低")}</strong>
                <i>{[1, 2, 3].map((bar) => <b className={bar <= confidenceBars ? "filled" : ""} key={bar} />)}</i>
              </button>
              {confidenceOpen && (
                <div className="report-confidence-popover">
                  <strong>置信度依据</strong>
                  <span>启用维度 <b>{assessedCount}/4</b></span>
                  <span>证据条数 <b>{report.evidence.length}</b></span>
                  <span>交叉验证项 <b>{crossCheckCount}</b></span>
                  <span>缺失材料 <b>{missingLabels.length ? missingLabels.join("、") : "无"}</b></span>
                </div>
              )}
            </div>
          </div>
          <div className="unified-verdict-group">
            <span className="unified-verdict">{nothingAssessed ? "数据不足" : report.trust_label}</span>
          </div>
        </div>

        {nothingAssessed && (
          <em className="live-overall-note">输入不足以核验任何维度，当前分数为中性占位。</em>
        )}

        <div className="unified-risk-band">
          <div>
            <i style={{ left: `${nothingAssessed ? 50 : score}%` }} />
          </div>
          <span>高风险</span>
          <span>可信</span>
        </div>

        <section className="report-section-block" id="forensics-section">
          <header className="report-section-heading"><span>Multimodal Forensics</span><h2>① 四维结果</h2></header>
        <div className="unified-dimensions">
          <div className="unified-radar">
            <svg aria-label="T R U S 四维核验分布" viewBox="0 0 240 220">
              {[0.25, 0.5, 0.75, 1].map((scale) => {
                const radius = 90 * scale;
                return (
                  <polygon
                    className="radar-grid"
                    key={scale}
                    points={`120,${110 - radius} ${120 + radius},110 120,${110 + radius} ${120 - radius},110`}
                  />
                );
              })}
              <line x1="120" x2="120" y1="20" y2="200" />
              <line x1="30" x2="210" y1="110" y2="110" />
              <polygon
                className="unified-radar-data"
                points={radar.map(([x, y]) => `${x},${y}`).join(" ")}
              />
              {[
                ["T", 120, 20, 120, 10],
                ["R", 210, 110, 226, 114],
                ["U", 120, 200, 120, 217],
                ["S", 30, 110, 14, 114],
              ].map(([code, cx, cy, tx, ty]) => (
                <g className={byCode.get(code as DimensionCode)?.assessed ? "" : "missing"} key={code}>
                  <circle cx={cx} cy={cy} r="10" />
                  <text textAnchor="middle" x={tx} y={ty}>
                    {code}
                  </text>
                </g>
              ))}
              {radar.map(
                ([x, y], index) =>
                  byCode.get(ORDERED_CODES[index])?.assessed && (
                    <circle className="unified-radar-point" cx={x} cy={y} key={ORDERED_CODES[index]} r="4" />
                  ),
              )}
            </svg>
            <small>四维核验分布</small>
          </div>

          <div className="unified-dimension-list">
            {ORDERED_CODES.map((code) => {
              const dimension = byCode.get(code);
              const assessed = Boolean(dimension?.assessed && dimension.trust_score != null);
              return (
                <button
                  type="button"
                  className={`unified-dimension-row ${assessed ? "" : "missing"}`}
                  disabled={!assessed}
                  key={code}
                  onClick={() => setEvidenceCode(code)}
                  title={dimension?.question}
                >
                  <span className={`report-dimension-badge badge-${code.toLowerCase()}`}>{code}</span>
                  <div>
                    <span>
                      <strong>{CODE_FALLBACK_LABEL[code]}</strong>
                      <b>{assessed ? <>{Math.round(dimension!.trust_score!)}<small> / 100</small></> : "暂不评分"}</b>
                    </span>
                    <div>
                      <i style={{ width: assessed ? `${dimension!.trust_score}%` : "0%" }} />
                    </div>
                    <small>
                      {assessed && dimension?.trust_label
                        ? roundScoresInText(`${dimension.trust_label} · ${dimension.summary}`)
                        : dimension?.summary ? roundScoresInText(dimension.summary) : "尚未提供该维度所需材料，未参与评分。"}
                    </small>
                    {technicalView && assessed && (
                      <MethodBadge method={(dimension?.evidence.length ?? 0) > 0 ? "llm" : "rule"} />
                    )}
                    {dimension?.limitations?.length ? (
                      <small className="live-dim-limit">局限：{dimension.limitations.join(" ")}</small>
                    ) : null}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
        </section>

        {report.summary && (
          <section className="live-report-summary">
            <span>Report Summary</span>
            <p>{roundScoresInText(report.summary)}</p>
          </section>
        )}

        {sample?.account && <LiveAccountEvidence account={sample.account} />}

        {sample?.image?.sha256 && (
          <LiveMaterialTrace loading={traceLoading} trace={trace} />
        )}

        {crossCheckItems.length > 0 && (
          <CrossCheckSection
            activeDimensions={activeCodes}
            items={crossCheckItems}
            live
            technicalView={technicalView}
          />
        )}

        <DecisionSupport
          activeDimensions={activeCodes}
          confidenceLevel={confidenceLevel}
          continueLabel={continueLabel}
          liveAdvice={report.decision?.advice}
          onContinue={onContinue}
          recommendations={report.recommendations}
          score={score}
          sourceLabel="实时报告"
          subtitle={roundScoresInText(report.decision?.headline ?? headline(report, byCode))}
        />
      </article>

      {sample && (
        <div className="unified-dimensions" style={{ borderTop: "none" }}>
          {sample.image?.url && (
            <div className="live-thumb">
              <img alt={sample.image.filename ?? "上传的素材"} src={sampleImageUrl(sample.id)} />
            </div>
          )}
          <div>
            <h3 style={{ margin: "0 0 10px", font: "500 18px var(--serif)", color: "#302a39" }}>本次输入</h3>
            <div className="live-input-summary" style={{ justifyContent: "flex-start", margin: 0 }}>
              <span>文案 {sample.text_length} 字</span>
              <span>评论 {sample.comment_count} 条</span>
              <span>账号历史 {sample.account?.post_count ?? 0} 篇</span>
              {sample.account?.handle && <span>账号 @{sample.account.handle}</span>}
              {sample.image && (
                <span>
                  {sample.image.width}×{sample.image.height} 图片
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      <section className="live-evidence">
        <h3>证据链（{report.evidence.length} 条）</h3>
        <ul>
          {report.evidence.map((item, index) => (
            <li key={`${item.source}-${index}`}>
              <span className={`live-sev ${item.severity}`}>{item.severity.toUpperCase()}</span>
              <div>
                <p>{item.evidence}</p>
                <small>
                  {item.source} · {item.type} · 置信度 {item.confidence}
                  {item.reason ? ` · ${item.reason}` : ""}
                  {technicalView && <MethodBadge method={item.source.includes("rule") ? "rule" : "llm"} />}
                </small>
              </div>
            </li>
          ))}
          {report.evidence.length === 0 && (
            <li>
              <p>本次未提取到证据。</p>
            </li>
          )}
        </ul>
      </section>

      {report.decision?.limitations?.length ? (
        <section className="live-evidence">
          <h3>报告边界</h3>
          <ul>
            {report.decision.limitations.map((item, index) => (
              <li key={`limit-${index}`}>
                <span className="live-sev medium">LIMIT</span>
                <p>{item}</p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {technicalView && (
        <section className="technical-explanation">
          <div>
            <section>
              <strong>输入与评分</strong>
              <span>已评估 {assessedCount} / 4 维 · 信任分 {nothingAssessed ? "暂不评分" : `${score}/100`}</span>
              <span>维度分数由后端按证据强度与风险规则计算，未提供的模态不参与评分。</span>
            </section>
            <section>
              <strong>交叉验证</strong>
              <span>比较已评估维度的可信分差、风险等级和证据数量。</span>
              <span>关系图只表达信号方向，不将统计差异表述为事实矛盾。</span>
            </section>
          </div>
          <small>
            {report.evidence.length} 条证据 · {report.dimensions.reduce((total, dimension) => total + dimension.evidence.length, 0)} 条维度内证据 · 任务 {report.task_id}
          </small>
        </section>
      )}

      <p className="method-note">
        本报告由 TruLens 根据当前可用信号生成；未提供的模态不参与判断，AI 结论不构成医疗或购买建议。
      </p>

      {evidenceCode && byCode.get(evidenceCode) ? (
        <div
          aria-label={`${CODE_FALLBACK_LABEL[evidenceCode]}证据链`}
          aria-modal="true"
          className={`report-evidence-backdrop evidence-${evidenceCode.toLowerCase()}`}
          role="dialog"
        >
          <article className="report-evidence-modal">
            <header className="report-evidence-header">
              <div>
                <span className={`report-dimension-badge badge-${evidenceCode.toLowerCase()}`}>
                  {evidenceCode}
                </span>
                <div>
                  <small>LIVE EVIDENCE · 可解释评分依据</small>
                  <strong>{CODE_FALLBACK_LABEL[evidenceCode]}证据链</strong>
                </div>
              </div>
              <div className="evidence-modal-score">
                <span>维度评分</span>
                <strong>
                  {Math.round(byCode.get(evidenceCode)!.trust_score ?? 0)}
                  <small>/ 100</small>
                </strong>
              </div>
              <button
                aria-label="关闭证据链"
                className="evidence-modal-close"
                onClick={() => setEvidenceCode(null)}
                type="button"
              >
                ×
              </button>
            </header>

            <div className="evidence-chain-summary">
              <span>结论</span>
              <p>{byCode.get(evidenceCode)!.summary}</p>
              <div>
                {byCode.get(evidenceCode)!.evidence.map((item) => (
                  <span className="evidence-summary-item" key={`${item.source}-${item.evidence}`}>
                    <small>{item.evidence}</small>
                  </span>
                ))}
              </div>
            </div>

            <section className="live-evidence" style={{ margin: "22px 28px 0" }}>
              <h3>本次输入证据</h3>
              {byCode.get(evidenceCode)!.evidence.map((item) => (
                <article className="live-evidence-item" key={`${item.source}-${item.type}-${item.evidence}`}>
                  <span className={`live-sev ${item.severity}`}>{item.severity.toUpperCase()}</span>
                  <div>
                    <p>{item.evidence}</p>
                    <small>
                      {item.source} · {item.type} · 置信度 {item.confidence.toFixed(2)}
                      {item.reason ? ` · ${item.reason}` : ""}
                    </small>
                  </div>
                </article>
              ))}
              {byCode.get(evidenceCode)!.limitations.length > 0 ? (
                <div className="evidence-chain-summary" style={{ margin: "12px 0 0" }}>
                  <span>局限</span>
                  <p>{byCode.get(evidenceCode)!.limitations.join(" ")}</p>
                </div>
              ) : null}
            </section>

            <footer className="evidence-modal-footer">
              <p><span /> 原始信号 <i /> 模型检测 <em /> 评分结论</p>
              <button onClick={() => setEvidenceCode(null)} type="button">返回报告</button>
            </footer>
          </article>
        </div>
      ) : null}
    </div>
  );
}

function LiveAccountEvidence({
  account,
}: {
  account: NonNullable<SampleRead["account"]>;
}) {
  const posts = account.posts ?? [];
  const datedPosts = posts.filter((post) => typeof post.published_at === "string");
  const coverage = Math.min(100, Math.round((posts.length / 30) * 100));
  const profileCompleteness = Math.min(
    100,
    25 +
      (account.handle ? 25 : 0) +
      (account.profile_text ? 25 : 0) +
      (account.follower_count != null ? 15 : 0) +
      (account.total_post_count != null ? 10 : 0),
  );
  const signals = [
    {
      label: "历史样本覆盖",
      value: coverage,
      detail: `当前提供 ${posts.length} 篇历史内容，账号维度至少需要 3 篇才会评分。`,
    },
    {
      label: "主页信息完整度",
      value: profileCompleteness,
      detail: [
        account.handle ? `账号 ${account.handle}` : "未提供账号名",
        account.profile_text ? "含主页简介" : "未提供简介",
      ].join(" · "),
    },
    {
      label: "时间记录覆盖",
      value: Math.min(100, Math.round((datedPosts.length / Math.max(1, posts.length)) * 100)),
      detail: `${datedPosts.length}/${posts.length} 篇历史内容带发布时间。`,
    },
  ];
  return (
    <section className="account-profile-evidence" aria-label="真实账号材料">
      <header>
        <span className="account-profile-avatar">T</span>
        <div>
          <strong>{account.handle || "未命名账号"}</strong>
          <small>账号快照 · 来自本次真实输入</small>
        </div>
        <em>{account.post_count} 篇</em>
      </header>
      <div className="account-profile-signals">
        {signals.map((signal) => (
          <div className="account-profile-signal" key={signal.label}>
            <div><strong>{signal.label}</strong><span>{signal.value}%</span></div>
            <div className="account-profile-meter"><i style={{ width: `${signal.value}%` }} /></div>
            <p>{signal.detail}</p>
          </div>
        ))}
      </div>
      <div className="account-profile-summary">
        <span>公开主页摘要</span>
        <strong>{account.profile_text || "本次未获取到主页简介。"}</strong>
      </div>
      <footer>
        账号信息来自上传的账号历史或小红书主页抓取；样本不足时不会推断完整账号画像。
      </footer>
    </section>
  );
}

function LiveMaterialTrace({
  loading,
  trace,
}: {
  loading: boolean;
  trace: SampleTraceRead | null;
}) {
  return (
    <section className="material-trace-evidence" aria-label="真实图片素材溯源">
      <header>
        <div>
          <span>素材溯源</span>
          <small>比对范围：TruLens 本地已核验内容库。</small>
        </div>
        <div><b>真实检测</b></div>
      </header>
      {loading ? (
        <p>正在比对图片 SHA-256…</p>
      ) : trace?.matches.length ? (
        <>
          <p>{trace.note}</p>
          <div className="material-trace-timeline">
            {trace.matches.slice(0, 3).map((match, index) => (
              <article key={match.sample_id}>
                <div className="material-trace-thumbnail">
                  <img alt={match.title} src={match.image_url} />
                </div>
                <i />
                <span>{index === 0 ? "最早匹配" : `匹配 ${index + 1}`}</span>
                <em>{match.risk_level ? `风险 ${match.risk_level}` : "未完成分析"}</em>
                <strong>{match.title}</strong>
                <time>{formatTraceDate(match.created_at)}</time>
              </article>
            ))}
          </div>
        </>
      ) : (
        <div className="material-trace-empty">
          <span><ArrowIcon direction="left" size={15} /></span>
          <div>
            <strong>未发现完全相同的图片哈希</strong>
            <small>{trace?.note || "文件哈希已记录，后续样本可继续参与比对。"}</small>
          </div>
        </div>
      )}
    </section>
  );
}

function formatTraceDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function getLiveCrossCheckItems(report: ReportRead): CrossCheckItem[] {
  const byCode = new Map<DimensionCode, DimensionRead>(
    report.dimensions.map((dimension) => [dimension.code, dimension]),
  );
  const assessed = ORDERED_CODES.filter((code) => byCode.get(code)?.assessed);

  if (assessed.length === 0) return [];

  if (assessed.length === 1) {
    const code = assessed[0];
    const dimension = byCode.get(code)!;
    const score = dimension.trust_score ?? 0;
    return [
      {
        id: `live-intra-${code.toLowerCase()}`,
        from: code,
        to: code,
        left: `${CODE_FALLBACK_LABEL[code]}：${Math.round(score)} / 100`,
        right: `${dimension.evidence.length} 条证据 · ${dimension.trust_label ?? "已评估"}`,
        conclusion: score >= 70 ? `${CODE_FALLBACK_LABEL[code]}当前未呈现明显高风险` : `${CODE_FALLBACK_LABEL[code]}存在需要留意的风险信号`,
        state: score >= 70 ? "consistent" : "doubtful",
        confidence: liveConfidence(dimension),
        dimension: `${crossDimensionName(code)}维度内`,
        detail: `${roundScoresInText(dimension.summary)} 本次仅提供${crossDimensionName(code)}材料，因此先展示维度内结论，不推断其他模态。`,
        method: dimension.evidence.length > 0 ? "llm" : "rule",
        label: "本次报告",
      },
    ];
  }

  return livePairs.map(([from, to]) => {
    const left = byCode.get(from);
    const right = byCode.get(to);
    const available = Boolean(left?.assessed && right?.assessed);
    if (!available) {
      const missing = [from, to]
        .filter((code) => !byCode.get(code)?.assessed)
        .map(crossDimensionName)
        .join("、");
      return {
        id: `live-${from}-${to}`.toLowerCase(),
        from,
        to,
        left: `${crossDimensionName(from)}材料`,
        right: `${crossDimensionName(to)}材料`,
        conclusion: `缺少${missing}，当前无法比对`,
        state: "unverified",
        dimension: `${crossDimensionName(from)} × ${crossDimensionName(to)}`,
        detail: `本次报告未完成${missing}维度评估，因此不生成该组合的方向性结论。`,
        label: "等待材料",
      };
    }

    const leftScore = left?.trust_score ?? 0;
    const rightScore = right?.trust_score ?? 0;
    const gap = Math.abs(leftScore - rightScore);
    const bothTrusted = leftScore >= 70 && rightScore >= 70;
    const bothRisky = leftScore < 50 && rightScore < 50;
    const directionConflict = (leftScore >= 70 && rightScore < 50) || (rightScore >= 70 && leftScore < 50);
    const state: CrossCheckItem["state"] = directionConflict
      ? "conflict"
      : bothTrusted || bothRisky
        ? "corroborated"
        : gap <= 18
          ? "consistent"
          : "doubtful";
    const conclusion = state === "conflict"
      ? "两个维度的可信方向不一致"
      : state === "corroborated"
        ? bothRisky
          ? "两个维度同时出现风险信号"
          : "两个维度均呈现较高可信度"
        : state === "doubtful"
          ? "两个维度存在明显分差，建议复核"
          : "两个维度暂未呈现明显冲突";
    const evidenceCount = (left?.evidence.length ?? 0) + (right?.evidence.length ?? 0);

    return {
      id: `live-${from}-${to}`.toLowerCase(),
      from,
      to,
      left: `${CODE_FALLBACK_LABEL[from]}：${Math.round(leftScore)} / 100`,
      right: `${CODE_FALLBACK_LABEL[to]}：${Math.round(rightScore)} / 100`,
      conclusion,
      state,
      confidence: gap <= 18 ? liveConfidence(left!, right!) : evidenceCount >= 4 ? "中" : "低",
      dimension: `${crossDimensionName(from)} × ${crossDimensionName(to)}`,
      detail: `本次报告显示${crossDimensionName(from)}可信度 ${Math.round(leftScore)}，${crossDimensionName(to)}可信度 ${Math.round(rightScore)}，分差 ${Math.round(gap)}。该关系基于分数、风险等级和 ${evidenceCount} 条证据计算，不替代进一步的人工事实核验。`,
      method: evidenceCount > 0 ? "llm" : "rule",
      label: "实时比对",
    };
  });
}

const livePairs: Array<[DimensionCode, DimensionCode]> = [
  ["T", "R"],
  ["T", "U"],
  ["T", "S"],
  ["R", "U"],
  ["R", "S"],
  ["U", "S"],
];

/**
 * Backend summaries embed scores as decimals ("风险分 10.0/100", "文案 10.0分"). Display only:
 * round any number that is directly followed by "/100" or "分" so the page stays on integers.
 */
export function roundScoresInText(text: string) {
  return text.replace(/(\d+(?:\.\d+)?)(\s*(?:\/\s*100|分))/g, (_, value: string, unit: string) => `${Math.round(Number(value))}${unit}`);
}

/** Short dimension names for sentences (账号 / 图像 / 评论 / 文案). */
const CODE_SHORT_LABEL: Record<DimensionCode, string> = { T: "账号", R: "图像", U: "评论", S: "文案" };

function crossDimensionName(code: DimensionCode) {
  return CODE_SHORT_LABEL[code];
}

function liveConfidence(...dimensions: DimensionRead[]): "高" | "中" | "低" {
  const available = dimensions.filter((dimension) => dimension.assessed);
  if (available.length === 0) return "低";
  const minimumEvidence = Math.min(...available.map((dimension) => dimension.evidence.length));
  return minimumEvidence >= 3 ? "高" : minimumEvidence >= 1 ? "中" : "低";
}

/** Per-dimension live status list, driven by `/api/status`. */
export function LiveProgressList({ agents }: { agents: AgentStatusItemLike[] }) {
  return (
    <div className="live-progress">
      <ol>
        {ORDERED_CODES.map((code) => {
          const item = agents.find((agent) => agent.code === code);
          const state = item?.status ?? "pending";
          const verdict = item?.assessed
            ? `可信度 ${item.trust_score != null ? Math.round(item.trust_score) : "暂不评分"}`
            : state === "completed"
              ? "暂不评分"
              : "";
          return (
            <li key={code}>
              <i className={`live-dot ${state}`} />
              <span>
                {code} · {CODE_FALLBACK_LABEL[code]}
              </span>
              <b>{verdict}</b>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/** Structural subset of `AgentStatusItem` (keeps this module import-light). */
export interface AgentStatusItemLike {
  code: DimensionCode;
  label: string;
  status: "pending" | "running" | "completed" | "failed";
  assessed: boolean;
  trust_score: number | null;
}

function headline(
  report: ReportRead,
  byCode: Map<DimensionCode, DimensionRead>,
): string {
  const assessed = report.dimensions.filter(
    (dimension) => dimension.assessed && dimension.risk_score != null,
  );
  if (assessed.length === 0) return "输入不足以完成四维交叉验证。";

  if (assessed.length === 1) {
    const only = assessed[0];
    const label = CODE_FALLBACK_LABEL[only.code];
    return `当前仅完成 ${only.code} · ${label} 单维核验。`;
  }

  const top = [...assessed]
    .sort((a, b) => (b.risk_score ?? 0) - (a.risk_score ?? 0))[0];
  if ((top.risk_score ?? 0) < 30) {
    return "已评估维度暂未发现明显高风险信号。";
  }
  const label = CODE_FALLBACK_LABEL[top.code];
  return `最需要留意的维度：${top.code} · ${label}。`;
}

export function ArrowIcon({ direction, size = 20 }: { direction: "left" | "right"; size?: number }) {
  const path = direction === "left" ? "M19 12H5m6-6-6 6 6 6" : "M5 12h14m-6-6 6 6-6 6";
  return (
    <svg aria-hidden="true" fill="none" height={size} viewBox="0 0 24 24" width={size}>
      <path d={path} stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.6" />
    </svg>
  );
}
