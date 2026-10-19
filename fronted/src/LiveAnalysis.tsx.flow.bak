/**
 * Live verification — a standalone screen for the real backend pipeline.
 *
 * The rest of the app (home / how-it-works / report showcase) is a fixed story
 * deck. This screen exposes the full form (文案 / 评论 / 账号历史 / 图片) and runs
 * the shared `useLiveAnalysis` hook, so it can be deep-linked for demos:
 *
 *     /?live=true
 *
 * The same hook also drives the main app flow (see `App.tsx`), so there is
 * exactly one implementation of the backend integration.
 */

import { useRef, useState } from "react";

import { ArrowIcon, LiveProgressList, LiveReportView } from "./LiveReport";
import { DEMO_INPUT, useLiveAnalysis } from "./useLiveAnalysis";

const SAMPLE_HISTORY = DEMO_INPUT.accountHistory ?? "";

export default function LiveAnalysis({ onBack }: { onBack: () => void }) {
  const [link, setLink] = useState("");
  const [text, setText] = useState("");
  const [comments, setComments] = useState("");
  const [accountHistory, setAccountHistory] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const live = useLiveAnalysis();

  const hasInput = Boolean(
    link.trim() || text.trim() || comments.trim() || accountHistory.trim() || image,
  );
  const busy = live.phase === "running";

  const run = () => {
    void live.start({
      url: link.trim() || undefined,
      text,
      comments,
      accountHistory,
      image,
    });
  };

  const startOver = () => {
    live.reset();
  };

  if (live.phase === "done" && live.report) {
    return (
      <section className="result">
        <header className="result-header">
          <button aria-label="返回" className="back-button" onClick={onBack} title="返回">
            <ArrowIcon direction="left" size={22} />
          </button>
          <div className="result-actions">
            <button onClick={startOver}>新的核验</button>
          </div>
        </header>
        <LiveReportView report={live.report} sample={live.sample} onContinue={startOver} />
      </section>
    );
  }

  return (
    <section className="workspace">
      <div className="workspace-orb orb-left" />
      <div className="workspace-orb orb-right" />
      <header className="workspace-header">
        <div className="workspace-status-group">
          <button aria-label="返回" className="workspace-home" onClick={onBack} title="返回">
            <ArrowIcon direction="left" size={22} />
          </button>
        </div>
        <div className="agent-online">
          <i className="live-dot completed" />
          实时核验 · T / R / U / S 四维
        </div>
      </header>

      <div className="workspace-center">
        <div className="agent-symbol">
          <SparkIcon size={26} />
        </div>
        <p className="hello">TruLens · Live</p>
        <h2>粘贴链接，四维核验</h2>
        <p className="workspace-intro">
          直接粘贴小红书笔记链接（或分享文案）即可自动抓取正文、图片与评论；也可以手动填写文案、评论、账号历史或上传图片。
          未提供材料的维度会显示「暂不评分」。
        </p>

        <div className="composer live-form">
          <label className="live-field">
            <span>链接 · 小红书笔记（推荐）</span>
            <textarea
              className="live-short"
              disabled={busy}
              onChange={(event) => setLink(event.target.value)}
              placeholder="https://www.xiaohongshu.com/explore/… 或复制来的分享文案"
              value={link}
            />
          </label>

          <label className="live-field">
            <span>文案 S · 种草文案</span>
            <textarea
              disabled={busy}
              onChange={(event) => setText(event.target.value)}
              placeholder="粘贴帖子正文…"
              value={text}
            />
          </label>

          <label className="live-field">
            <span>评论 U · 评论区（每行一条，或 JSON 数组）</span>
            <textarea
              className="live-short"
              disabled={busy}
              onChange={(event) => setComments(event.target.value)}
              placeholder={"好用\n好用\n真的太好用了，果断入手"}
              value={comments}
            />
          </label>

          <label className="live-field">
            <span>账号 T · 账号历史 JSON（≥3 篇才会评分）</span>
            <textarea
              className="live-short"
              disabled={busy}
              onChange={(event) => setAccountHistory(event.target.value)}
              placeholder={'{"handle":"账号名","posts":[{"text":"…","published_at":"2026-09-01T09:00:00Z"}]}'}
              value={accountHistory}
            />
          </label>

          <div className="live-actions">
            <div>
              <input
                accept="image/jpeg,image/png,image/webp"
                hidden
                onChange={(event) => setImage(event.target.files?.[0] ?? null)}
                ref={fileInput}
                type="file"
              />
              <button
                className="tool-button"
                disabled={busy}
                onClick={() => fileInput.current?.click()}
                title="上传图片（R 维度）"
                type="button"
              >
                <PlusIcon size={18} />
              </button>
              <span className="live-file">
                {image ? (
                  <>
                    {image.name}
                    <button onClick={() => setImage(null)} title="移除图片" type="button">
                      ×
                    </button>
                  </>
                ) : (
                  "支持 JPG / PNG / WebP"
                )}
              </span>
            </div>
            <div>
              <button
                className="tool-button"
                disabled={busy}
                onClick={() => {
                  setLink("");
                  setText(DEMO_INPUT.text ?? "");
                  setComments(DEMO_INPUT.comments ?? "");
                  setAccountHistory(SAMPLE_HISTORY);
                }}
                title="填入示例数据"
                type="button"
              >
                示例
              </button>
              <button className="live-primary" disabled={busy || !hasInput} onClick={run} type="button">
                {busy ? "核验中…" : "开始四维核验"}
                <span className="live-arrow">
                  <ArrowIcon direction="right" size={18} />
                </span>
              </button>
            </div>
          </div>
        </div>

        {live.phase === "running" && (
          <div className="live-progress">
            <div className="live-progress-head">
              <strong>{live.status ? "正在四维核验…" : "已创建分析任务…"}</strong>
              <b>{live.status ? `${live.status.progress}%` : "0%"}</b>
            </div>
            <LiveProgressList agents={live.agents} />
          </div>
        )}
        {live.error && (
          <p className="live-error">
            {live.error}
            {live.taskId && (
              <button
                className="live-resume-button"
                onClick={() => void live.resume()}
                type="button"
              >
                重新获取结果
              </button>
            )}
          </p>
        )}
      </div>
    </section>
  );
}

function PlusIcon({ size = 18 }: { size?: number }) {
  return (
    <svg aria-hidden="true" fill="none" height={size} viewBox="0 0 24 24" width={size}>
      <path d="M12 4v16M4 12h16" stroke="currentColor" strokeLinecap="round" strokeWidth="1.6" />
    </svg>
  );
}

function SparkIcon({ size = 20 }: { size?: number }) {
  return (
    <svg aria-hidden="true" fill="none" height={size} viewBox="0 0 24 24" width={size}>
      <path
        d="M12 3l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4L12 3Z"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="1.4"
      />
    </svg>
  );
}
