/**
 * Typed client for the TruLens backend (T/R/U/S four-dimension contract).
 *
 * Everything the live vertical slice needs: upload -> poll status -> read the
 * report. The backend returns *credibility* scores at this boundary
 * (`score` / `trust_score`, higher = better), while `risk_score` preserves the
 * internal risk orientation.
 *
 * Base URL defaults to `/api`, which the Vite dev server proxies to the
 * uvicorn backend (see `vite.config.ts`), so no CORS or absolute URL is needed
 * in development. Override with `VITE_API_BASE` for a deployed setup.
 */

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

export type DimensionCode = "T" | "R" | "U" | "S";
export type RiskLevel = "low" | "medium" | "high";
export type TrustLevel = "low" | "medium" | "high";
export type TaskStatus = "pending" | "running" | "completed" | "failed";

export interface EvidenceItem {
  source: string;
  type: string;
  evidence: string;
  confidence: number;
  severity: RiskLevel;
  reason: string | null;
}

export interface DimensionRead {
  key: string;
  code: DimensionCode;
  label: string;
  question: string;
  input_label: string;
  assessed: boolean;
  /** Credibility 0-100, or null when the dimension had nothing to judge. */
  trust_score: number | null;
  risk_score: number | null;
  risk_level: RiskLevel | null;
  trust_level: TrustLevel | null;
  trust_label: string | null;
  summary: string;
  evidence: EvidenceItem[];
  limitations: string[];
}

export interface CrossCheckRead {
  id: string;
  from_code: DimensionCode;
  to_code: DimensionCode;
  left: string;
  right: string;
  conclusion: string;
  state: "conflict" | "consistent" | "corroborated" | "doubtful" | "unverified";
  dimension: string;
  detail: string;
  confidence: "高" | "中" | "低" | null;
  method: "llm" | "rule";
  label: string | null;
}

export type DecisionPerspective = "consumer" | "creator" | "platform" | "brand";

export interface DecisionAdviceRead {
  perspective: DecisionPerspective;
  priority: "重点" | "建议";
  title: string;
  basis: string;
  action: string | null;
}

export interface DecisionRead {
  tier: "pass" | "verify" | "review";
  label: string;
  headline: string;
  advice: DecisionAdviceRead[];
  limitations: string[];
}

export interface ReportConfidenceRead {
  level: "high" | "medium" | "low";
  label: "高" | "中" | "低";
  bars: number;
  assessed_count: number;
  evidence_count: number;
  cross_check_count: number;
  missing_materials: string[];
}

export interface ReportRead {
  id: string;
  task_id: string;
  /** Risk 0-100, higher = riskier. */
  overall_score: number;
  /** Credibility 0-100, higher = better — the "Trust Score / 100" the UI shows. */
  trust_score: number;
  risk_level: RiskLevel;
  trust_level: TrustLevel;
  trust_label: string;
  summary: string;
  evidence: EvidenceItem[];
  recommendations: string[];
  dimension_scores: Record<string, number>;
  dimensions: DimensionRead[];
  cross_checks: CrossCheckRead[];
  decision: DecisionRead | null;
  confidence: ReportConfidenceRead | null;
  assessed_dimensions: string[];
  generated_at: string | null;
  created_at: string;
}

export interface AgentStatusItem {
  agent: string;
  code: DimensionCode;
  label: string;
  status: TaskStatus;
  assessed: boolean;
  risk_level: RiskLevel | null;
  risk_score: number | null;
  trust_level: TrustLevel | null;
  trust_score: number | null;
  trust_label: string | null;
  duration_ms: number | null;
  error_message: string | null;
}

export interface TaskStatusResponse {
  task_id: string;
  sample_id: string;
  status: TaskStatus;
  progress: number;
  requested_agents: string[];
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  report_ready: boolean;
  agents: AgentStatusItem[];
}

export interface AccountSnapshotInfo {
  handle: string | null;
  profile_text: string | null;
  follower_count: number | null;
  total_post_count: number | null;
  post_count: number;
  posts: Array<Record<string, unknown>>;
}

export interface ImageInfo {
  filename: string | null;
  content_type: string | null;
  size: number | null;
  width: number | null;
  height: number | null;
  has_exif: boolean | null;
  sha256: string | null;
  url: string | null;
}

export interface SampleRead {
  id: string;
  text_content: string | null;
  text_length: number;
  comment_count: number;
  comments: Array<Record<string, unknown>>;
  account: AccountSnapshotInfo | null;
  image: ImageInfo | null;
  source: string;
  created_at: string;
}

export interface TraceMatchRead {
  sample_id: string;
  task_id: string | null;
  title: string;
  source: string;
  created_at: string;
  trust_score: number | null;
  risk_level: RiskLevel | null;
  image_url: string;
}

export interface SampleTraceRead {
  sample_id: string;
  sha256: string | null;
  match_count: number;
  matches: TraceMatchRead[];
  note: string;
}

export interface TeamCheckRead {
  claim: string;
  evidence: string;
}

export interface TeamQueueItemRead {
  id: string;
  task_id: string;
  sample_id: string;
  title: string;
  author: string;
  score: number;
  risk_level: RiskLevel;
  confidence: TrustLevel;
  dimensions: DimensionCode[];
  contradiction: string;
  issue_type: string;
  actions: Record<"platform" | "brand", string>;
  status: "pending" | "watching" | "done";
  checks: TeamCheckRead[];
  created_at: string;
}

export interface TeamSummaryRead {
  total: number;
  high_risk: number;
  pending: number;
  trusted: number;
  medium_risk: number;
  confidence_high: number;
  confidence_medium: number;
  confidence_low: number;
}

export interface TeamGroupMemberRead {
  id: string;
  task_id: string;
  title: string;
  author: string;
  created_at: string;
}

export interface TeamRelatedGroupRead {
  id: string;
  signals: string[];
  members: TeamGroupMemberRead[];
}

export interface TeamQueueRead {
  items: TeamQueueItemRead[];
  summary: TeamSummaryRead;
  groups: TeamRelatedGroupRead[];
  generated_at: string;
}

export interface CreatorIssueRead {
  id: string;
  severity: "must" | "suggest" | "tip";
  type: string;
  original: string | null;
  suggestion: string;
  reason: string;
  remove: boolean;
}

export interface CreatorCheckRead {
  task_id: string;
  sample_id: string;
  publication_state: "ready" | "revise" | "hold";
  publication_title: string;
  copy_score: number | null;
  image_score: number | null;
  issues: CreatorIssueRead[];
  disclosures: Record<string, boolean>;
  good_points: string[];
  check_date: string;
  report: ReportRead;
}

export interface UploadResponse {
  task_id: string;
  sample_id: string;
  status: TaskStatus;
  comment_count: number;
  text_length: number;
  has_image: boolean;
  account_post_count: number;
  /** Canonical URL echoed back when a Xiaohongshu link was submitted. */
  source_url: string | null;
}

export interface UploadInput {
  text?: string;
  /** Raw newline-separated text or a JSON array string; the backend normalizes it. */
  comments?: string;
  /** JSON string for the T dimension: {handle, posts:[{text, published_at, ...}]}. */
  accountHistory?: string;
  /**
   * Xiaohongshu note link *or* the whole share text. The backend resolves it to
   * the note id and crawls the page in a background browser.
   */
  url?: string;
  /** Optional Xiaohongshu author profile URL; omitted means auto-discover. */
  accountUrl?: string;
  image?: File | null;
  source?: string;
  autoAnalyze?: boolean;
  useLlm?: boolean;
  agents?: string[];
}

/**
 * Does this input look like a Xiaohongshu link / share text?
 *
 * Deliberately loose: real shares look like
 * "9.9 复制打开小红书，看看【标题】 https://xhslink.com/abc123" or a bare note URL,
 * and the backend's `normalize()` is the authority on what is parseable.
 */
export function looksLikeXhsLink(text: string | null | undefined): boolean {
  if (!text) return false;
  return /xiaohongshu\.com|xhslink\.(com|cn)|\b[0-9a-f]{24}\b/i.test(text);
}

/** Error thrown for any non-2xx response, carrying the backend's error envelope. */
export class ApiError extends Error {
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly status: number;

  constructor(status: number, code: string, message: string, details: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

/** Human-readable one-liner for any thrown value (used by every screen). */
export function describeApiError(err: unknown): string {
  if (err instanceof ApiError) {
    const detail = Object.keys(err.details).length ? `（${JSON.stringify(err.details)}）` : "";
    return `${err.message}${detail}`;
  }
  return err instanceof Error ? err.message : String(err);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, init);
  } catch (cause) {
    throw new ApiError(0, "network_error", "无法连接后端服务，请确认 API 已启动。", {
      cause: String(cause),
    });
  }

  const text = await response.text();
  const payload = text ? safeJson(text) : null;

  if (!response.ok) {
    const envelope = (payload as { error?: { code?: string; message?: string; details?: unknown } } | null)?.error;
    throw new ApiError(
      response.status,
      envelope?.code ?? "http_error",
      envelope?.message ?? `请求失败 (${response.status})`,
      (envelope?.details as Record<string, unknown>) ?? {},
    );
  }
  return payload as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/** `POST /api/upload` — ingest content and open an analysis task. */
export async function uploadContent(input: UploadInput): Promise<UploadResponse> {
  const form = new FormData();
  if (input.text) form.append("text", input.text);
  if (input.comments) form.append("comments", input.comments);
  if (input.accountHistory) form.append("account_history", input.accountHistory);
  if (input.url) form.append("url", input.url);
  if (input.accountUrl) form.append("account_url", input.accountUrl);
  if (input.image) form.append("image", input.image);
  form.append("source", input.source ?? "frontend");
  form.append("auto_analyze", String(input.autoAnalyze ?? true));
  form.append("use_llm", String(input.useLlm ?? false));
  if (input.agents?.length) form.append("agents", JSON.stringify(input.agents));

  return request<UploadResponse>("/upload", { method: "POST", body: form });
}

/** `POST /api/analyze/{task_id}` — (re)start the pipeline for a task. */
export async function analyzeTask(taskId: string, agents?: string[]): Promise<void> {
  await request(`/analyze/${encodeURIComponent(taskId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agents: agents ?? null, use_llm: false }),
  });
}

/** `GET /api/status/{task_id}` — polling view. */
export function getStatus(taskId: string): Promise<TaskStatusResponse> {
  return request<TaskStatusResponse>(`/status/${encodeURIComponent(taskId)}`);
}

/** `GET /api/report/{task_id}` — the final four-dimension report. */
export function getReport(taskId: string): Promise<ReportRead> {
  return request<ReportRead>(`/report/${encodeURIComponent(taskId)}`);
}

/** `GET /api/samples/{sample_id}` — what was uploaded (including the image URL). */
export function getSample(sampleId: string): Promise<SampleRead> {
  return request<SampleRead>(`/samples/${encodeURIComponent(sampleId)}`);
}

/** `GET /api/samples/{sample_id}/trace` — exact image hash reuse in the local library. */
export function getSampleTrace(sampleId: string): Promise<SampleTraceRead> {
  return request<SampleTraceRead>(`/samples/${encodeURIComponent(sampleId)}/trace`);
}

/** `GET /api/team/queue` — completed reports from the real database. */
export function getTeamQueue(): Promise<TeamQueueRead> {
  return request<TeamQueueRead>("/team/queue");
}

/** `PATCH /api/team/queue/{task_id}` — persist a team review status. */
export function updateTeamStatus(
  taskId: string,
  status: "pending" | "watching" | "done",
): Promise<{ task_id: string; status: string }> {
  return request(`/team/queue/${encodeURIComponent(taskId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
}

export interface CreatorCheckInput {
  draft: string;
  isCollaboration: boolean;
  category: string;
  image?: File | null;
}

/** `POST /api/creator/check` — real text/image analysis plus rewrite suggestions. */
export async function checkCreator(input: CreatorCheckInput): Promise<CreatorCheckRead> {
  const form = new FormData();
  form.append("draft", input.draft);
  form.append("is_collaboration", String(input.isCollaboration));
  form.append("category", input.category);
  if (input.image) form.append("image", input.image);
  return request<CreatorCheckRead>("/creator/check", {
    method: "POST",
    body: form,
  });
}

/** `GET /api/engine-trace/{task_id}` — the engine's execution trace, when it ran. */
export function getEngineTrace(taskId: string): Promise<unknown> {
  return request<unknown>(`/engine-trace/${encodeURIComponent(taskId)}`);
}

/** Absolute URL for the uploaded image, usable directly in `<img src>`. */
export function sampleImageUrl(sampleId: string): string {
  return `${API_BASE}/samples/${encodeURIComponent(sampleId)}/image`;
}

export interface WaitOptions {
  /** Give up after this long. Defaults to 5 minutes for multimodal analysis. */
  timeoutMs?: number;
  intervalMs?: number;
  onProgress?: (status: TaskStatusResponse) => void;
}

/**
 * Poll `/api/status` until the pipeline reaches a terminal state, then return
 * the report. Throws if the task fails or the timeout elapses.
 */
export async function waitForReport(taskId: string, options: WaitOptions = {}): Promise<ReportRead> {
  const timeoutMs = options.timeoutMs ?? 300_000;
  const intervalMs = options.intervalMs ?? 900;
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    const status = await getStatus(taskId);
    options.onProgress?.(status);

    if (status.status === "failed") {
      throw new ApiError(500, "task_failed", status.error_message ?? "分析任务失败。", { taskId });
    }
    if (status.report_ready) {
      return getReport(taskId);
    }
    await sleep(intervalMs);
  }
  throw new ApiError(504, "timeout", "分析超时，请稍后在报告库中查看结果。", { taskId });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
