/**
 * Typed fetch client for the Boston Bill Party backend.
 *
 * Types mirror `backend/models.py` and the HTTP/SSE contract in the design doc.
 * The frontend NEVER handles the backend LLM API key — only the public API
 * base URL is configurable here.
 *
 * Endpoints covered:
 *   POST /sessions                 -> createSession
 *   GET  /sessions/{id}/events     -> subscribeToEvents (SSE)
 *   GET  /sessions/{id}/result     -> getResult
 *   POST /sessions/{id}/confirm    -> confirmLetter
 *   POST /sessions/{id}/ask        -> ask
 */

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

/** Base URL of the backend API. Configure via NEXT_PUBLIC_API_BASE_URL. */
export const API_BASE_URL: string =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/+$/, "") ??
  "http://localhost:8000";

// ---------------------------------------------------------------------------
// Domain types (mirror backend/models.py)
// ---------------------------------------------------------------------------

/** Languages the system can produce output in (SupportedLanguage). */
export type SupportedLanguage = "en" | "es" | "pt" | "ht";

/** Human-readable labels for the language selector; English is the default. */
export const SUPPORTED_LANGUAGES: ReadonlyArray<{
  value: SupportedLanguage;
  label: string;
}> = [
  { value: "en", label: "English" },
  { value: "es", label: "Español" },
  { value: "pt", label: "Português" },
  { value: "ht", label: "Kreyòl Ayisyen" },
];

export const DEFAULT_LANGUAGE: SupportedLanguage = "en";

/** Categories of issues produced by the Auditor and Rights_Checker. */
export type FindingType =
  | "math_error"
  | "duplicate_charge"
  | "suspicious_code"
  | "rights_issue";

/**
 * A single detected issue with evidence and a confidence score.
 * Monetary fields serialize as two-decimal strings on the backend, so they
 * arrive as strings here (or null when absent).
 */
export interface Finding {
  id: string;
  type: FindingType;
  description: string;
  /** Decimal in [0, 1], serialized as a string. */
  confidence: string;
  line_item_ids: string[];
  passage_ids: string[];
  /** Two-decimal string for math-error findings, else null. */
  computed_total: string | null;
  stated_total: string | null;
}

// ---------------------------------------------------------------------------
// Request / response shapes
// ---------------------------------------------------------------------------

/** POST /sessions response: a new session id, returned within 5s. */
export interface CreateSessionResponse {
  sessionId: string;
}

/**
 * GET /sessions/{id}/result response.
 *
 * The backend result shape is still being finalized across tasks (the design
 * doc shows `{ summary, appealLetter, findings }` while the model exposes
 * `summary_entries` / `retained_findings`). We accept either field name and
 * normalize via {@link normalizeResult}.
 */
export interface ResultResponse {
  /** Preferred: list of summary entries (one per retained Finding). */
  summaryEntries?: string[];
  /** Alternate naming used in some backend revisions. */
  summary?: string[];
  /** The editable appeal letter; null when there are no findings (Req 7.4). */
  appealLetter: string | null;
  /** Preferred: retained findings after the Skeptic filter. */
  retainedFindings?: Finding[];
  /** Alternate naming used in some backend revisions. */
  findings?: Finding[];
}

/** Normalized result with stable field names for UI consumption. */
export interface AnalysisResult {
  summaryEntries: string[];
  appealLetter: string | null;
  findings: Finding[];
}

/** POST /sessions/{id}/ask response: an answer returned within 30s. */
export interface AskResponse {
  answer: string;
}

// ---------------------------------------------------------------------------
// Result/status polling (GET /sessions/{id}/result discriminated shape)
// ---------------------------------------------------------------------------

/**
 * Live progress picture returned in the `running` result shape and by the SSE
 * `progress` event. `activeNode` is null between node transitions (no node is
 * currently RUNNING); `completedNodes` lists completed node ids in canonical
 * order; `completedCount` / `total` drive the `completed/5` display (Req 9.3).
 */
export interface ResultProgress {
  activeNode: string | null;
  completedNodes: string[];
  completedCount: number;
  total: number;
}

/** Node-fatal error recorded on the session (`error` result shape, Req 9.4). */
export interface ResultError {
  failedNode: string;
  message: string;
}

/**
 * Discriminated union mirroring the backend `GET /sessions/{id}/result` body
 * for a known session (an unknown id is a 404 -> {@link ApiError}).
 *
 *  - `running`   — pipeline in flight; carries the live {@link ResultProgress}.
 *  - `completed` — Writer produced output; carries the normalized result fields.
 *  - `error`     — a node-fatal error; carries {@link ResultError} plus any
 *                  partial result already produced (writer-retry path, Req 8.2).
 */
export type ResultStatus =
  | { status: "running"; progress: ResultProgress }
  | ({ status: "completed" } & AnalysisResult & {
        language: SupportedLanguage;
      })
  | ({ status: "error"; error: ResultError } & Partial<AnalysisResult> & {
        language?: SupportedLanguage;
      });

/** Raw backend body for the result/status endpoint before normalization. */
interface ResultStatusResponse extends ResultResponse {
  status: "running" | "completed" | "error";
  progress?: ResultProgress;
  error?: ResultError;
  language?: SupportedLanguage;
}

// ---------------------------------------------------------------------------
// SSE event shapes (GET /sessions/{id}/events)
// ---------------------------------------------------------------------------

/** Total number of pipeline nodes reported by progress events. */
export const TOTAL_NODES = 5;

/** `progress` event payload. */
export interface ProgressEvent {
  activeNode: string;
  completedNodes: string[];
  completedCount: number;
  total: number;
}

/** `node_error` event payload — the frontend keeps prior completed state. */
export interface NodeErrorEvent {
  failedNode: string;
  message: string;
}

/** Discriminated union of events delivered to a stream subscriber. */
export type PipelineEvent =
  | { type: "progress"; data: ProgressEvent }
  | { type: "node_error"; data: NodeErrorEvent }
  | { type: "done" };

/** Callbacks for {@link subscribeToEvents}. */
export interface EventHandlers {
  onProgress?: (event: ProgressEvent) => void;
  onNodeError?: (event: NodeErrorEvent) => void;
  onDone?: () => void;
  /** Transport-level error (connection dropped, parse failure, etc.). */
  onError?: (error: Error) => void;
}

// ---------------------------------------------------------------------------
// Error handling
// ---------------------------------------------------------------------------

/** Error thrown when the backend responds with a non-2xx status. */
export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

async function parseJsonOrThrow<T>(res: Response): Promise<T> {
  const text = await res.text();
  let body: unknown = undefined;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) {
    const detail =
      body &&
      typeof body === "object" &&
      "detail" in body &&
      typeof (body as { detail: unknown }).detail === "string"
        ? (body as { detail: string }).detail
        : undefined;
    const message = detail ?? `Request failed with status ${res.status}`;
    throw new ApiError(res.status, message, body);
  }
  return body as T;
}

function url(path: string): string {
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

// ---------------------------------------------------------------------------
// Endpoint functions
// ---------------------------------------------------------------------------

/**
 * POST /sessions — upload a PDF plus the selected language and start the
 * pipeline. Returns the new session id (within 5s per Req 1.7).
 *
 * The system operates EXCLUSIVELY on synthetic data (Req 12.1), and the backend
 * rejects any upload not explicitly marked synthetic (Req 12.2). This client is
 * therefore always marking the upload synthetic via the documented
 * `synthetic=true` form field — the request-shape contract the backend expects
 * (``backend/main.py``). Without this the backend returns 400 and the pipeline
 * never starts.
 */
export async function createSession(
  file: File,
  language: SupportedLanguage,
  init?: { signal?: AbortSignal },
): Promise<CreateSessionResponse> {
  const form = new FormData();
  form.append("pdf", file);
  form.append("language", language);
  // Synthetic-data marker (Req 12.1/12.2): this app only ever handles synthetic
  // bills, so every upload is marked synthetic per the backend contract.
  form.append("synthetic", "true");

  const res = await fetch(url("/sessions"), {
    method: "POST",
    body: form,
    signal: init?.signal,
  });
  return parseJsonOrThrow<CreateSessionResponse>(res);
}

/** GET /health — cheap liveness check (used to detect a cold-starting backend). */
export async function checkHealth(init?: { signal?: AbortSignal }): Promise<void> {
  const res = await fetch(url("/health"), {
    method: "GET",
    cache: "no-store",
    signal: init?.signal,
  });
  await parseJsonOrThrow<{ status: string }>(res);
}

/** GET /sessions/{id}/result — fetch the final analysis and normalize it. */
export async function getResult(
  sessionId: string,
  init?: { signal?: AbortSignal },
): Promise<AnalysisResult> {
  const res = await fetch(url(`/sessions/${encodeURIComponent(sessionId)}/result`), {
    method: "GET",
    signal: init?.signal,
  });
  const body = await parseJsonOrThrow<ResultResponse>(res);
  return normalizeResult(body);
}

/** Collapse the two possible backend field spellings into {@link AnalysisResult}. */
export function normalizeResult(body: ResultResponse): AnalysisResult {
  return {
    summaryEntries: body.summaryEntries ?? body.summary ?? [],
    appealLetter: body.appealLetter ?? null,
    findings: body.retainedFindings ?? body.findings ?? [],
  };
}

/**
 * GET /sessions/{id}/result — poll the discriminated result/status shape.
 *
 * Unlike {@link getResult} (which assumes a completed run), this surfaces the
 * backend's `status` so a caller can poll while the pipeline is `running`,
 * react to a node-fatal `error`, and read the final result on `completed`
 * (Req 9.1, 9.3, 9.4). An unknown session id raises {@link ApiError} (404).
 */
export async function getResultStatus(
  sessionId: string,
  init?: { signal?: AbortSignal },
): Promise<ResultStatus> {
  const res = await fetch(url(`/sessions/${encodeURIComponent(sessionId)}/result`), {
    method: "GET",
    signal: init?.signal,
  });
  const body = await parseJsonOrThrow<ResultStatusResponse>(res);

  if (body.status === "running") {
    return {
      status: "running",
      progress: body.progress ?? {
        activeNode: null,
        completedNodes: [],
        completedCount: 0,
        total: TOTAL_NODES,
      },
    };
  }

  if (body.status === "error") {
    return {
      status: "error",
      error: body.error ?? {
        failedNode: "unknown",
        message: "The analysis stopped due to an error.",
      },
      ...normalizeResult(body),
      language: body.language,
    };
  }

  // status === "completed"
  return {
    status: "completed",
    ...normalizeResult(body),
    language: body.language ?? DEFAULT_LANGUAGE,
  };
}

/**
 * POST /sessions/{id}/confirm — transmit the current edited letter text only
 * after the user's explicit confirm action (Req 8.5–8.7).
 */
export async function confirmLetter(
  sessionId: string,
  letterText: string,
  init?: { signal?: AbortSignal },
): Promise<void> {
  const res = await fetch(url(`/sessions/${encodeURIComponent(sessionId)}/confirm`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ letter: letterText }),
    signal: init?.signal,
  });
  await parseJsonOrThrow<unknown>(res);
}

/**
 * POST /sessions/{id}/ask — ask a question about the analyzed bill. Returns an
 * answer within 30s or a timeout error (Req 10.6, 10.7).
 */
export async function ask(
  sessionId: string,
  question: string,
  init?: { signal?: AbortSignal },
): Promise<AskResponse> {
  const res = await fetch(url(`/sessions/${encodeURIComponent(sessionId)}/ask`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal: init?.signal,
  });
  return parseJsonOrThrow<AskResponse>(res);
}

/** POST /sessions/{id}/translate-letter response. */
export interface TranslateLetterResponse {
  translation: string;
  language: SupportedLanguage;
}

/**
 * POST /sessions/{id}/translate-letter — read-only translation of the English
 * appeal letter into the session language, to help the patient understand it.
 * The English letter is still what gets sent.
 */
export async function translateLetter(
  sessionId: string,
  letterText: string,
  init?: { signal?: AbortSignal },
): Promise<TranslateLetterResponse> {
  const res = await fetch(
    url(`/sessions/${encodeURIComponent(sessionId)}/translate-letter`),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ letter: letterText }),
      signal: init?.signal,
    },
  );
  return parseJsonOrThrow<TranslateLetterResponse>(res);
}

/**
 * GET /sessions/{id}/events — subscribe to the SSE progress stream.
 *
 * Returns an unsubscribe function that closes the underlying EventSource.
 * The named events (`progress`, `node_error`, `done`) match the design's SSE
 * event shapes. Only usable in the browser (EventSource is a DOM API).
 */
export function subscribeToEvents(
  sessionId: string,
  handlers: EventHandlers,
): () => void {
  if (typeof window === "undefined" || typeof EventSource === "undefined") {
    handlers.onError?.(
      new Error("subscribeToEvents requires a browser environment (EventSource)"),
    );
    return () => {};
  }

  const source = new EventSource(
    url(`/sessions/${encodeURIComponent(sessionId)}/events`),
  );

  const parse = <T>(raw: string): T | null => {
    try {
      return JSON.parse(raw) as T;
    } catch (err) {
      handlers.onError?.(
        err instanceof Error ? err : new Error("Failed to parse SSE payload"),
      );
      return null;
    }
  };

  source.addEventListener("progress", (ev) => {
    const data = parse<ProgressEvent>((ev as MessageEvent).data);
    if (data) handlers.onProgress?.(data);
  });

  source.addEventListener("node_error", (ev) => {
    const data = parse<NodeErrorEvent>((ev as MessageEvent).data);
    if (data) handlers.onNodeError?.(data);
  });

  source.addEventListener("done", () => {
    handlers.onDone?.();
    source.close();
  });

  source.onerror = () => {
    handlers.onError?.(new Error("SSE connection error"));
  };

  return () => source.close();
}
