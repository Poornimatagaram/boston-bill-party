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
  getResultStatus,
  type Finding,
  type FindingType,
  type ResultError,
  type ResultStatus,
} from "@/lib/api";
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

/** Human-readable labels for Finding types shown in the Summary context list. */
const FINDING_TYPE_LABELS: Record<FindingType, string> = {
  math_error: "Math error",
  duplicate_charge: "Duplicate charge",
  suspicious_code: "Suspicious code",
  rights_issue: "Patient-rights issue",
};

/** A result we can render: Summary (always) + optional letter + findings. */
interface LoadedResult {
  summaryEntries: string[];
  appealLetter: string | null;
  findings: Finding[];
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
}): LoadedResult {
  return {
    summaryEntries: status.summaryEntries ?? [],
    appealLetter: status.appealLetter ?? null,
    findings: status.findings ?? [],
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

  if (load.kind === "loading" || load.kind === "finishing") {
    return (
      <section
        aria-live="polite"
        aria-busy="true"
        className="flex items-center gap-3 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800"
      >
        <span
          aria-hidden="true"
          className="h-2 w-2 animate-pulse rounded-full bg-blue-500"
        />
        <p className="text-base text-zinc-800 dark:text-zinc-200">
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
        className="flex flex-col gap-2 rounded-lg border border-red-200 p-6 dark:border-red-900/60"
      >
        <p className="text-base font-medium text-red-600 dark:text-red-400">
          Results unavailable
        </p>
        <p className="text-sm text-zinc-700 dark:text-zinc-300">
          {load.message}
        </p>
      </section>
    );
  }

  // Both `loaded` and `error` have a Summary we can render. For `error` we also
  // show the failed-node message and a Retry action (Req 8.2).
  const result = load.kind === "loaded" ? load.result : load.partial;
  const isError = load.kind === "error";

  return (
    <div className="flex flex-col gap-8">
      {isError ? (
        <section
          role="alert"
          className="flex flex-col gap-3 rounded-lg border border-red-200 p-6 dark:border-red-900/60"
        >
          <div className="flex flex-col gap-1">
            <p className="text-base font-medium text-red-600 dark:text-red-400">
              Appeal letter couldn’t be generated
            </p>
            <p className="text-sm text-zinc-700 dark:text-zinc-300">
              A problem occurred while {nodeLabel(load.error.failedNode)}.
            </p>
            {load.error.message ? (
              <p className="text-sm text-zinc-500 dark:text-zinc-400">
                {load.error.message}
              </p>
            ) : null}
          </div>
          <div>
            <button
              type="button"
              onClick={handleRetry}
              className="inline-flex items-center justify-center rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
            >
              Retry
            </button>
          </div>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            Your summary below is kept while you retry.
          </p>
        </section>
      ) : null}

      {/* Summary — always shown when present (Req 8.1). */}
      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold tracking-tight">Summary</h2>
        {result.summaryEntries.length > 0 ? (
          <ul className="flex list-disc flex-col gap-2 pl-5 text-sm text-zinc-800 dark:text-zinc-200">
            {result.summaryEntries.map((entry, i) => (
              <li key={i}>{entry}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            No summary is available yet.
          </p>
        )}
      </section>

      {/* Findings context (optional, helps explain the summary). */}
      {result.findings.length > 0 ? (
        <section className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold tracking-tight">
            Detected issues
          </h2>
          <ul className="flex flex-col gap-3">
            {result.findings.map((f) => (
              <li
                key={f.id}
                className="rounded-md border border-zinc-200 p-4 dark:border-zinc-800"
              >
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
                    {FINDING_TYPE_LABELS[f.type] ?? f.type}
                  </span>
                  <span className="font-mono text-xs text-zinc-500 tabular-nums dark:text-zinc-400">
                    confidence {f.confidence}
                  </span>
                </div>
                <p className="mt-1 text-sm text-zinc-700 dark:text-zinc-300">
                  {f.description}
                </p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* Editable Appeal_Letter + explicit confirm gate (Req 8.3–8.7). When the
          Writer produced no letter (Req 7.4) we show nothing to edit/confirm. */}
      {letter !== null && result.appealLetter !== null ? (
        <section className="flex flex-col gap-3">
          <div className="flex items-baseline justify-between gap-3">
            <label
              htmlFor={letterFieldId}
              className="text-lg font-semibold tracking-tight"
            >
              Appeal letter
            </label>
            <span
              id={charCountId}
              className="font-mono text-xs text-zinc-500 tabular-nums dark:text-zinc-400"
            >
              {letter.length.toLocaleString()} /{" "}
              {MAX_LETTER_CHARS.toLocaleString()}
            </span>
          </div>
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
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
            className="w-full resize-y rounded-md border border-zinc-300 bg-white px-3 py-2 font-mono text-sm text-zinc-900 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
          />

          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={handleConfirm}
              disabled={confirm.kind === "confirming"}
              aria-busy={confirm.kind === "confirming"}
              className="inline-flex items-center justify-center rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
            >
              {confirm.kind === "confirming"
                ? "Confirming…"
                : confirm.kind === "confirmed"
                  ? "Confirmed"
                  : "Confirm appeal letter"}
            </button>
          </div>

          {/* Confirm / error status, announced to assistive tech. */}
          <div aria-live="polite" className="min-h-5">
            {confirm.kind === "confirmed" ? (
              <p className="text-sm text-green-700 dark:text-green-400">
                Your appeal letter has been confirmed and submitted.
              </p>
            ) : confirm.kind === "error" ? (
              <p role="alert" className="text-sm text-red-600 dark:text-red-400">
                {confirm.message}
              </p>
            ) : null}
          </div>
        </section>
      ) : !isError ? (
        <section className="rounded-lg border border-zinc-200 p-6 text-sm text-zinc-600 dark:border-zinc-800 dark:text-zinc-400">
          No credible billing errors or rights issues were detected, so there is
          no appeal letter to review.
        </section>
      ) : null}

      {/* Voice Q&A (task 15.2): feature-detected voice control with a text
          fallback, available once the Results screen has loaded (Req 10.1). */}
      <VoiceQA sessionId={sessionId} />
    </div>
  );
}
