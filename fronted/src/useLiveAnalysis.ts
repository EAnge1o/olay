/**
 * Real analysis state, shared by every screen that talks to the backend.
 *
 * This is the single place that owns the live pipeline, so the app screens can
 * stay presentational and the whole integration can be rolled back by removing
 * the one `useLiveAnalysis()` call in `App.tsx`.
 *
 * Flow: POST /api/upload -> poll GET /api/status -> GET /api/report.
 */

import { useCallback, useState } from "react";

import {
  describeApiError,
  getSample,
  uploadContent,
  waitForReport,
  type AgentStatusItem,
  type ReportRead,
  type SampleRead,
  type TaskStatusResponse,
} from "./api";

export interface LiveRunInput {
  text?: string;
  /** Newline-separated comments, or a JSON array string. */
  comments?: string;
  /** JSON string for the T dimension. */
  accountHistory?: string;
  /** Xiaohongshu note link or share text; crawled in the background. */
  url?: string;
  /** Optional Xiaohongshu author homepage link. */
  accountUrl?: string;
  image?: File | null;
  source?: string;
  agents?: string[];
}

export type LivePhase = "idle" | "running" | "done" | "error";

export interface LiveRunState {
  phase: LivePhase;
  taskId: string | null;
  sampleId: string | null;
  /** Latest `/api/status` payload; drives progress + the per-dimension panel. */
  status: TaskStatusResponse | null;
  /** Convenience mirror of `status.agents`. */
  agents: AgentStatusItem[];
  report: ReportRead | null;
  sample: SampleRead | null;
  error: string | null;
}

export interface LiveRun extends LiveRunState {
  start: (input: LiveRunInput) => Promise<void>;
  /** Resume polling an existing task after a frontend timeout or network blip. */
  resume: () => Promise<void>;
  reset: () => void;
}

const INITIAL: LiveRunState = {
  phase: "idle",
  taskId: null,
  sampleId: null,
  status: null,
  agents: [],
  report: null,
  sample: null,
  error: null,
};

/**
 * Demo content used by the one-click "示例" affordances. Kept next to the hook
 * so the landing hero and the live form submit exactly the same payload — the
 * backend then produces a rich, reproducible four-dimension report.
 */
export const DEMO_INPUT: LiveRunInput = {
  text:
    "姐妹们！这支寡肽精华真的绝了！我是敏感肌，用了7天痘印就淡了，毛孔也消失了，" +
    "皮肤科医生推荐，100%修复敏感肌，无副作用零风险，一次见效，7天白一个度，立竿见影。",
  comments: [
    "好用",
    "好用",
    "好用",
    "真的太好用了，果断入手",
    "真的太好用了，果断入手！",
    "我用了两周，两颊泛红缓解了，但痘印淡得不明显，要配合防晒。",
  ].join("\n"),
  accountHistory: JSON.stringify(
    {
      handle: "cutie_beauty88",
      profile_text: "每天一支好物分享 / 合作请私信",
      follower_count: 8200,
      total_post_count: 460,
      posts: [
        {
          text: "今天也是这瓶精华，闭眼入，链接在橱窗，领券下单更划算。",
          published_at: "2026-09-28T09:00:00Z",
          like_count: 21,
        },
        {
          text: "又回购了，姐妹们冲，商品卡直接拍，品牌方合作款。",
          published_at: "2026-09-28T09:12:00Z",
          like_count: 18,
        },
        {
          text: "无脑入就对了，优惠券领取后更便宜，旗舰店同款。",
          published_at: "2026-09-28T09:26:00Z",
          like_count: 15,
        },
        {
          text: "用了两周，脸颊泛红有缓解，但痘印变化不明显，需要配合防晒。",
          published_at: "2026-09-30T21:05:00Z",
          like_count: 140,
        },
      ],
    },
    null,
    2,
  ),
};

export function useLiveAnalysis(): LiveRun {
  const [state, setState] = useState<LiveRunState>(INITIAL);

  const reset = useCallback(() => setState(INITIAL), []);

  const start = useCallback(async (input: LiveRunInput) => {
    setState({ ...INITIAL, phase: "running" });
    try {
      const upload = await uploadContent({ ...input, source: input.source ?? "frontend" });

      // Ignore late updates from a superseded run.
      const isCurrent = (taskId: string) => taskId === upload.task_id;
      setState((prev) => ({
        ...prev,
        taskId: upload.task_id,
        sampleId: upload.sample_id,
      }));

      // The sample view is a nicety, never a blocker.
      getSample(upload.sample_id)
        .then((sample) => setState((prev) => (isCurrent(upload.task_id) ? { ...prev, sample } : prev)))
        .catch(() => undefined);

      const report = await waitForReport(upload.task_id, {
        // Link ingestion spends up to four minutes in the browser crawler before
        // the multimodal engine starts; image/text uploads use a shorter window.
        timeoutMs: input.url ? 480_000 : 300_000,
        onProgress: (status) =>
          setState((prev) =>
            isCurrent(upload.task_id) ? { ...prev, status, agents: status.agents } : prev,
          ),
      });

      setState((prev) =>
        isCurrent(upload.task_id) ? { ...prev, phase: "done", report } : prev,
      );
    } catch (err) {
      setState((prev) => ({ ...prev, phase: "error", error: describeApiError(err) }));
    }
  }, []);

  const resume = useCallback(async () => {
    if (!state.taskId) return;
    const taskId = state.taskId;
    setState((prev) => ({ ...prev, phase: "running", error: null }));
    try {
      const report = await waitForReport(taskId, {
        timeoutMs: 480_000,
        onProgress: (status) =>
          setState((prev) =>
            prev.taskId === taskId ? { ...prev, status, agents: status.agents } : prev,
          ),
      });
      setState((prev) =>
        prev.taskId === taskId ? { ...prev, phase: "done", report } : prev,
      );
    } catch (err) {
      setState((prev) =>
        prev.taskId === taskId
          ? { ...prev, phase: "error", error: describeApiError(err) }
          : prev,
      );
    }
  }, [state.taskId]);

  return { ...state, start, resume, reset };
}
