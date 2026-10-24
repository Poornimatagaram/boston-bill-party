"use client";

/**
 * Results view, keyed by session id (task 10.2).
 *
 * Renders the Writer output: the plain-language Summary plus the editable
 * Appeal_Letter, and wires the explicit confirm gate (Req 8.1–8.7).
 *
 * Behavior:
 *  - On mount, fetch `GET /sessions/{id}/result`. If the pipeline is still
 *    `running`, poll briefly ("finishing up") until it reaches `completed` or
 *    `error`, then render immediately — no artificial delay, so the Summary and
 *    letter appear well within 2s of the data being available (Req 8.1).
 *  - Render the Summary (one line per entry) and, for context, the retained
 *    Findings list (type, description, confidence).
 *  - Present the Appeal_Letter in an editable <textarea> seeded from the result
 *    and capped at 20,000 characters (Req 8.3), with a live character count.
 *  - RETAIN edits in local state across polls/re-renders until the user
 *    confirms (Req 8.4) — the textarea is seeded exactly once.
 *  - Expose a single explicit "Confirm" control (Req 8.5) that is the ONLY
 *    trigger for transmit; on click it POSTs the CURRENT edited text via
 *    confirmLetter (Req 8.6, 8.7). No auto-send on blur/navigation.
 *  - When there is no Appeal_Letter (no credible issues, Req 7.4), show the
 *    Summary with no editor and no confirm control.
 *  - On a Writer/node `error`, show the failed-node/message plus a RETRY action
 *    that re-fetches the result WITHOUT discarding any Summary already produced
 *    (Req 8.2). Retry re-fetches status; a future writer-retry endpoint can
 *    replace the re-fetch here.
 *
 * Accessibility: the textarea and confirm button are labelled, and confirm /
 * error status is announced via an aria-live region. An unknown session id
 * (404) is handled gracefully.
 *
 * Voice Q&A (task 15.2) is rendered via the {@link VoiceQA} component once the
 * results are available, so its feature-detected control appears within 1s of
 * the Results screen being ready (Req 10.1).
 *
 * The frontend never handles the LLM API key.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  ApiError,
  confirmLetter,
  DEFAULT_LANGUAGE,
  getResultStatus,
  type Finding,
  type FindingType,
  type ResultError,
  type ResultStatus,
  type SupportedLanguage,
} from "@/lib/api";
import ReadAloudButton from "./ReadAloudButton";
import { ReadAloudProvider } from "./readAloud";
import VoiceQA from "./VoiceQA";

/** Maximum characters the editable Appeal_Letter accepts (Req 8.3). */
const MAX_LETTER_CHARS = 20_000;

/** How often we poll while the pipeline is still `running` (ms). */
const POLL_INTERVAL_MS = 1000;

/** Friendly labels for the conceptual pipeline node ids (for error display). */
const NODE_LABELS: Record<string, string> = {
  extractor: "reading your bill",
  auditor: "checking the math and charges",
  rights_checker: "reviewing patient rights",
  skeptic: "double-checking the findings",
  writer: "writing your summary and appeal letter",
};

function nodeLabel(nodeId: string | null | undefined): string {
  if (!nodeId) return "the analysis";
  return NODE_LABELS[nodeId] ?? nodeId;
}

/**
 * Per-type presentation for the Detected-issue cards: a human label, an icon,
 * and a color scheme (card tint + accent used for the icon chip and the
 * confidence bar). Color-coding makes the issue types scannable in the demo.
 */
const FINDING_TYPE_META: Record<
  FindingType,
  { label: string; icon: string; card: string; chip: string; bar: string }
> = {
  math_error: {
    label: "Math error",
    icon: "🧮",
    card: "border-amber-200 bg-amber-50/70 dark:border-amber-900/50 dark:bg-amber-950/20",
    chip: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-200",
    bar: "bg-amber-500",
  },
  duplicate_charge: {
    label: "Duplicate charge",
    icon: "📑",
    card: "border-rose-200 bg-rose-50/70 dark:border-rose-900/50 dark:bg-rose-950/20",
    chip: "bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-200",
    bar: "bg-rose-500",
  },
  suspicious_code: {
    label: "Suspicious code",
    icon: "🔍",
    card: "border-violet-200 bg-violet-50/70 dark:border-violet-900/50 dark:bg-violet-950/20",
    chip: "bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-200",
    bar: "bg-violet-500",
  },
  rights_issue: {
    label: "Patient-rights issue",
    icon: "⚖️",
    card: "border-teal-200 bg-teal-50/70 dark:border-teal-800/60 dark:bg-teal-950/20",
    chip: "bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-200",
    bar: "bg-teal-500",
  },
};

/** Parse a confidence string in [0,1] into a clamped percentage (0–100). */
function confidencePct(confidence: string): number {
  const n = Number.parseFloat(confidence);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, Math.round(n * 100)));
}

/** A result we can render: Summary (always) + optional letter + findings. */
interface LoadedResult {
  summaryEntries: string[];
  appealLetter: string | null;
  findings: Finding[];
  language: SupportedLanguage;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "finishing" }
  | { kind: "loaded"; result: LoadedResult }
  | {
      // Node-fatal error. Retain any Summary already produced (Req 8.2).
      kind: "error";
      error: ResultError;
      partial: LoadedResult;
    }
  | { kind: "failed"; message: string };

type ConfirmState =
  | { kind: "idle" }
  | { kind: "confirming" }
  | { kind: "confirmed" }
  | { kind: "error"; message: string };

function toLoadedResult(
  status: Extract<ResultStatus, { status: "completed" }>,
): LoadedResult;
function toLoadedResult(
  status: Extract<ResultStatus, { status: "error" }>,
): LoadedResult;
function toLoadedResult(status: {
  summaryEntries?: string[];
  appealLetter?: string | null;
  findings?: Finding[];
  language?: SupportedLanguage;
}): LoadedResult {
  return {
    summaryEntries: status.summaryEntries ?? [],
    appealLetter: status.appealLetter ?? null,
    findings: status.findings ?? [],
    language: status.language ?? DEFAULT_LANGUAGE,
  };
}

export default function ResultsView({ sessionId }: { sessionId: string }) {
  const letterFieldId = useId();
  const charCountId = useId();

  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const [confirm, setConfirm] = useState<ConfirmState>({ kind: "idle" });

  // The editable letter text. `null` means "not seeded yet" so we seed it from
  // the loaded result exactly once and then RETAIN user edits (Req 8.4) across
  // re-renders/polls and across a retry that re-produces the same letter.
  const [letter, setLetter] = useState<string | null>(null);
  const letterSeededRef = useRef(false);

  // Bumped by the Retry action to re-run the load effect without discarding the
  // Summary we already show (the effect seeds state but never clears `letter`).
  const [reloadToken, setReloadToken] = useState(0);

  // Transient "Copied!" acknowledgement for the post-confirm Copy action.
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const controller = new AbortController();

    const stop = () => {
      active = false;
      if (timer) clearTimeout(timer);
      controller.abort();
    };

    const applyLoaded = (result: LoadedResult) => {
      // Seed the editable letter exactly once; never overwrite user edits.
      if (!letterSeededRef.current) {
        letterSeededRef.current = true;
        setLetter(result.appealLetter ?? "");
      }
      setLoad({ kind: "loaded", result });
    };

    async function poll(initial: boolean) {
      if (!active) return;
      try {
        const status = await getResultStatus(sessionId, {
          signal: controller.signal,
        });
        if (!active) return;

        if (status.status === "running") {
          // Still working. Show a brief "finishing up" and keep polling; render
          // happens immediately once data is present (no artificial delay).
          setLoad((prev) =>
            prev.kind === "loaded" || prev.kind === "error"
              ? prev
              : { kind: "finishing" },
          );
          timer = setTimeout(() => poll(false), POLL_INTERVAL_MS);
          return;
        }

        if (status.status === "completed") {
          stop();
          applyLoaded(toLoadedResult(status));
          return;
        }

        // status === "error": node-fatal. Keep any Summary in the payload so the
        // retry path does not lose it (Req 8.2).
        stop();
        setLoad({
          kind: "error",
          error: status.error,
          partial: toLoadedResult(status),
        });
      } catch (err) {
        if (!active) return;
        if (err instanceof DOMException && err.name === "AbortError") return;

        const message =
          err instanceof ApiError
            ? err.status === 404
              ? "We couldn't find this analysis session. It may have expired."
              : err.message
            : err instanceof Error
              ? err.message
              : "We couldn't load the results. Please try again.";
        stop();
        setLoad({ kind: "failed", message });
      }
      // `initial` is unused beyond readability; both paths behave the same.
      void initial;
    }

    poll(true);
    return stop;
  }, [sessionId, reloadToken]);

  // Retry after a writer/node error: re-fetch status WITHOUT discarding the
  // Summary we already display (Req 8.2). The load effect re-seeds state but the
  // letter is only seeded once, so prior edits survive too.
  const handleRetry = useCallback(() => {
    setConfirm({ kind: "idle" });
    setLoad((prev) =>
      prev.kind === "error" ? { kind: "loading" } : prev,
    );
    setReloadToken((t) => t + 1);
  }, []);

  // The ONLY transmit trigger (Req 8.5): send the CURRENT edited text (Req 8.6,
  // 8.7). Never fired on blur/navigation.
  const handleConfirm = useCallback(async () => {
    if (letter === null) return;
    setConfirm({ kind: "confirming" });
    try {
      await confirmLetter(sessionId, letter);
      setConfirm({ kind: "confirmed" });
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "We couldn't submit the letter. Please try again.";
      setConfirm({ kind: "error", message });
    }
  }, [sessionId, letter]);

  // Post-confirm convenience actions. None of these transmit anything to the
  // backend — they only act on the local letter text the user already
  // confirmed. The mailto link intentionally leaves the recipient blank.
  const handleCopy = useCallback(async () => {
    if (!letter) return;
    try {
      await navigator.clipboard.writeText(letter);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard may be unavailable (e.g. insecure context); ignore silently.
    }
  }, [letter]);

  const handleDownload = useCallback(() => {
    if (!letter) return;
    const blob = new Blob([letter], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "appeal-letter.txt";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }, [letter]);

  // mailto: with the letter in the body and NO recipient filled in.
  const mailtoHref =
    letter !== null
      ? `mailto:?subject=${encodeURIComponent(
          "Appeal regarding my medical bill",
        )}&body=${encodeURIComponent(letter)}`
      : "";

  if (load.kind === "loading" || load.kind === "finishing") {
    return (
      <section
        aria-live="polite"
        aria-busy="true"
        className="flex items-center gap-3 rounded-2xl border border-border-subtle bg-surface p-6 shadow-card"
      >
        <span className="h-5 w-5 shrink-0 animate-spin rounded-full border-2 border-teal-500 border-t-transparent" />
        <p className="text-base text-navy-800 dark:text-navy-100">
          {load.kind === "finishing"
            ? "Finishing up your analysis…"
            : "Loading your results…"}
        </p>
      </section>
    );
  }

  if (load.kind === "failed") {
    return (
      <section
        role="alert"
        className="flex flex-col gap-2 rounded-2xl border border-red-200 bg-red-50 p-6 shadow-card dark:border-red-900/60 dark:bg-red-950/30"
      >
        <p className="text-base font-semibold text-red-700 dark:text-red-300">
          Results unavailable
        </p>
        <p className="text-sm text-navy-700 dark:text-navy-200">
          {load.message}
        </p>
      </section>
    );
  }

  // Both `loaded` and `error` have a Summary we can render. For `error` we also
  // show the failed-node message and a Retry action (Req 8.2).
  const result = load.kind === "loaded" ? load.result : load.partial;
  const isError = load.kind === "error";
  const issueCount = result.findings.length;
  const issueBannerText =
    issueCount === 0
      ? "No credible issues found"
      : `${issueCount} issue${issueCount === 1 ? "" : "s"} found`;

  return (
    <ReadAloudProvider>
    <div className="flex flex-col gap-6">
      {/* Top banner: issue count (Req 8.1 presentation). */}
      <section
        className={[
          "flex items-center gap-4 rounded-2xl border p-5 shadow-card",
          issueCount === 0
            ? "border-teal-200 bg-teal-50 dark:border-teal-800 dark:bg-teal-950/20"
            : "border-navy-200 bg-navy-700 text-white dark:border-navy-700",
        ].join(" ")}
      >
        <span
          aria-hidden="true"
          className={[
            "flex h-12 w-12 shrink-0 items-center justify-center rounded-full text-2xl",
            issueCount === 0
              ? "bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-200"
              : "bg-teal-400 text-navy-900",
          ].join(" ")}
        >
          {issueCount === 0 ? "✅" : "🚩"}
        </span>
        <div className="flex flex-col">
          <p
            className={[
              "text-lg font-semibold tracking-tight sm:text-xl",
              issueCount === 0
                ? "text-teal-900 dark:text-teal-100"
                : "text-white",
            ].join(" ")}
          >
            {issueBannerText}
          </p>
          <p
            className={
              issueCount === 0
                ? "text-sm text-teal-700 dark:text-teal-200"
                : "text-sm text-teal-200"
            }
          >
            {issueCount === 0
              ? "We didn’t find credible billing errors or rights issues."
              : "Review the details below and send the appeal letter."}
          </p>
        </div>
      </section>

      {isError ? (
        <section
          role="alert"
          className="flex flex-col gap-3 rounded-2xl border border-red-200 bg-red-50 p-5 shadow-card dark:border-red-900/60 dark:bg-red-950/30"
        >
          <div className="flex flex-col gap-1">
            <p className="text-base font-semibold text-red-700 dark:text-red-300">
              Appeal letter couldn’t be generated
            </p>
            <p className="text-sm text-navy-700 dark:text-navy-200">
              A problem occurred while {nodeLabel(load.error.failedNode)}.
            </p>
            {load.error.message ? (
              <p className="text-sm text-navy-500 dark:text-navy-300">
                {load.error.message}
              </p>
            ) : null}
          </div>
          <div>
            <button
              type="button"
              onClick={handleRetry}
              className="inline-flex items-center justify-center rounded-xl bg-navy-700 px-4 py-2 text-sm font-semibold text-white shadow-card hover:bg-navy-600"
            >
              Retry
            </button>
          </div>
          <p className="text-xs text-navy-500 dark:text-navy-300">
            Your summary below is kept while you retry.
          </p>
        </section>
      ) : null}

      {/* Summary — always shown when present (Req 8.1). */}
      <section className="flex flex-col gap-3 rounded-2xl border border-border-subtle bg-surface p-5 shadow-card sm:p-6">
        <h2 className="text-lg font-semibold tracking-tight text-navy-900 dark:text-white">
          Summary
        </h2>
        {result.summaryEntries.length > 0 ? (
          <>
            <ul className="flex flex-col gap-2 text-sm text-navy-800 dark:text-navy-100">
              {result.summaryEntries.map((entry, i) => (
                <li key={i} className="flex items-start gap-2">
                  <ReadAloudButton
                    id={`summary-entry-${i}`}
                    text={entry}
                    language={result.language}
                    label="this summary item"
                    iconOnly
                  />
                  <span className="mt-0.5">{entry}</span>
                </li>
              ))}
            </ul>
            <ReadAloudButton
              id="summary-all"
              text={result.summaryEntries.join(". ")}
              language={result.language}
            />
          </>
        ) : (
          <p className="text-sm text-navy-500 dark:text-navy-300">
            No summary is available yet.
          </p>
        )}
      </section>

      {/* Findings context — color-coded cards with icons + confidence bars. */}
      {result.findings.length > 0 ? (
        <section className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold tracking-tight text-navy-900 dark:text-white">
            Detected issues
          </h2>
          <ul className="flex flex-col gap-3">
            {result.findings.map((f) => {
              const meta =
                FINDING_TYPE_META[f.type] ??
                ({
                  label: f.type,
                  icon: "•",
                  card: "border-border-subtle bg-surface",
                  chip: "bg-navy-100 text-navy-700",
                  bar: "bg-navy-500",
                } as const);
              const pct = confidencePct(f.confidence);
              return (
                <li
                  key={f.id}
                  className={`rounded-2xl border p-4 shadow-card sm:p-5 ${meta.card}`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <ReadAloudButton
                        id={`finding-${f.id}`}
                        text={`${meta.label}. ${f.description}`}
                        language={result.language}
                        label="this issue"
                        iconOnly
                      />
                      <span
                        aria-hidden="true"
                        className={`flex h-7 w-7 items-center justify-center rounded-full text-sm ${meta.chip}`}
                      >
                        {meta.icon}
                      </span>
                      <span className="text-sm font-semibold text-navy-900 dark:text-white">
                        {meta.label}
                      </span>
                    </div>
                  </div>
                  <p className="mt-2 text-sm text-navy-700 dark:text-navy-100">
                    {f.description}
                  </p>
                  {/* Confidence bar. */}
                  <div className="mt-3 flex items-center gap-2">
                    <span className="text-[11px] font-medium text-navy-500 dark:text-navy-300">
                      Confidence
                    </span>
                    <div
                      className="h-2 flex-1 overflow-hidden rounded-full bg-navy-100 dark:bg-navy-800"
                      role="progressbar"
                      aria-valuenow={pct}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-label={`Confidence ${pct}%`}
                    >
                      <div
                        className={`h-full rounded-full ${meta.bar}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span className="w-9 text-right font-mono text-xs tabular-nums text-navy-600 dark:text-navy-200">
                      {pct}%
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {/* Editable Appeal_Letter + explicit confirm gate (Req 8.3–8.7). When the
          Writer produced no letter (Req 7.4) we show nothing to edit/confirm. */}
      {letter !== null && result.appealLetter !== null ? (
        <section className="flex flex-col gap-3 rounded-2xl border border-border-subtle bg-surface p-5 shadow-card sm:p-6">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <div className="flex items-center gap-2">
              <label
                htmlFor={letterFieldId}
                className="text-lg font-semibold tracking-tight text-navy-900 dark:text-white"
              >
                Appeal letter
              </label>
              <ReadAloudButton
                id="appeal-letter"
                text={letter}
                language={result.language}
                label="the appeal letter"
                iconOnly
              />
            </div>
            <span
              id={charCountId}
              className="font-mono text-xs text-navy-500 tabular-nums dark:text-navy-300"
            >
              {letter.length.toLocaleString()} /{" "}
              {MAX_LETTER_CHARS.toLocaleString()}
            </span>
          </div>
          <p className="text-sm text-navy-500 dark:text-navy-300">
            Review and edit this draft. Nothing is sent until you select Confirm.
          </p>
          <textarea
            id={letterFieldId}
            value={letter}
            maxLength={MAX_LETTER_CHARS}
            aria-describedby={charCountId}
            disabled={confirm.kind === "confirming"}
            onChange={(e) => {
              setLetter(e.target.value);
              // Any edit after a confirm/error returns the control to idle so
              // the user can confirm the newly edited text.
              setConfirm((prev) =>
                prev.kind === "idle" || prev.kind === "confirming"
                  ? prev
                  : { kind: "idle" },
              );
            }}
            rows={16}
            className="w-full resize-y rounded-xl border border-border-subtle bg-canvas px-3 py-2 font-mono text-sm text-navy-900 outline-none focus:border-teal-400 focus:ring-2 focus:ring-teal-200 disabled:opacity-60 dark:bg-navy-950 dark:text-navy-50 dark:focus:ring-teal-900"
          />

          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={handleConfirm}
              disabled={confirm.kind === "confirming"}
              aria-busy={confirm.kind === "confirming"}
              className="inline-flex items-center justify-center rounded-xl bg-navy-700 px-5 py-2.5 text-sm font-semibold text-white shadow-card transition-colors hover:bg-navy-600 focus:outline-none focus:ring-2 focus:ring-teal-300 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {confirm.kind === "confirming"
                ? "Confirming…"
                : confirm.kind === "confirmed"
                  ? "Confirmed ✓"
                  : "Confirm appeal letter"}
            </button>
          </div>

          {/* Confirm / error status, announced to assistive tech. */}
          <div aria-live="polite" className="min-h-5">
            {confirm.kind === "confirmed" ? (
              <p className="text-sm font-medium text-teal-700 dark:text-teal-300">
                Your appeal letter has been confirmed and submitted.
              </p>
            ) : confirm.kind === "error" ? (
              <p role="alert" className="text-sm text-red-600 dark:text-red-400">
                {confirm.message}
              </p>
            ) : null}
          </div>

          {/* After Confirm: share actions (all act locally — no transmission). */}
          {confirm.kind === "confirmed" ? (
            <div className="flex flex-col gap-3 rounded-xl border border-teal-200 bg-teal-50 p-4 dark:border-teal-800 dark:bg-teal-950/20">
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={handleCopy}
                  className="inline-flex items-center gap-2 rounded-xl border border-navy-200 bg-surface px-4 py-2 text-sm font-medium text-navy-800 shadow-card transition-colors hover:bg-navy-50 dark:border-navy-700 dark:text-navy-100 dark:hover:bg-navy-900"
                >
                  {copied ? "✓ Copied!" : "📋 Copy letter"}
                </button>
                <button
                  type="button"
                  onClick={handleDownload}
                  className="inline-flex items-center gap-2 rounded-xl border border-navy-200 bg-surface px-4 py-2 text-sm font-medium text-navy-800 shadow-card transition-colors hover:bg-navy-50 dark:border-navy-700 dark:text-navy-100 dark:hover:bg-navy-900"
                >
                  ⬇️ Download as .txt
                </button>
                <a
                  href={mailtoHref}
                  className="inline-flex items-center gap-2 rounded-xl border border-navy-200 bg-surface px-4 py-2 text-sm font-medium text-navy-800 shadow-card transition-colors hover:bg-navy-50 dark:border-navy-700 dark:text-navy-100 dark:hover:bg-navy-900"
                >
                  ✉️ Email draft
                </a>
              </div>
              <p className="text-sm text-teal-800 dark:text-teal-200">
                Send this to your hospital billing office or insurer.
              </p>
            </div>
          ) : null}
        </section>
      ) : !isError ? (
        <section className="rounded-2xl border border-border-subtle bg-surface p-6 text-sm text-navy-600 shadow-card dark:text-navy-300">
          No credible billing errors or rights issues were detected, so there is
          no appeal letter to review.
        </section>
      ) : null}

      {/* Voice Q&A (task 15.2): feature-detected voice control with a text
          fallback, available once the Results screen has loaded (Req 10.1). */}
      <VoiceQA sessionId={sessionId} language={result.language} />
    </div>
    </ReadAloudProvider>
  );
}
