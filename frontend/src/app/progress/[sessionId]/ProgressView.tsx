"use client";

/**
 * Live progress view (Server-Sent Events), keyed by session id.
 *
 * Task 14.2: subscribe to the backend SSE stream and render the pipeline's live
 * state (Req 9.1, 9.3, 9.4, 9.5). SSE is the target transport; the interim
 * polling path (task 10.1) is replaced here. A one-shot `getResultStatus`
 * fetch is kept only as a fallback to resolve the final state if the transport
 * errors.
 *
 * Behavior:
 *  - On mount, subscribe via `subscribeToEvents` (Req 9.1). Each `progress`
 *    event arrives promptly, so the friendly active-node label (Req 9.1) and
 *    the `completed/5` count (Req 9.3) update well within 2s.
 *  - On a `node_error`, show the failed node and an "analysis stopped" message
 *    while RETAINING the last progress we displayed (Req 9.4) — the completed
 *    count/nodes already shown are not cleared.
 *  - On `done`, the pipeline finished: unsubscribe and navigate to the Results
 *    screen (consistent with the prior polling version's completed -> navigate).
 *  - A 60-second inactivity timer is started on mount and RESET on every
 *    received event (progress / node_error / done). If 60s elapse with no
 *    event, show an interruption message WITHOUT discarding current progress
 *    (Req 9.5). The timer uses setTimeout so a faked clock can drive it in tests.
 *  - On a transport/connection `onError`, surface a message and attempt a
 *    one-shot `getResultStatus` fallback to detect completion/error so a late
 *    drop still resolves to Results or an error state.
 *  - All subscriptions and timers are cleaned up on unmount.
 *
 * Accessibility: the status is rendered in an `aria-live="polite"` region so
 * assistive tech announces active-node / completed-count / interruption updates.
 *
 * The frontend never handles the LLM API key.
 */

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  getResultStatus,
  subscribeToEvents,
  TOTAL_NODES,
  type NodeErrorEvent,
  type ProgressEvent as PipelineProgressEvent,
  type ResultError,
  type ResultProgress,
} from "@/lib/api";

/**
 * If no SSE event arrives within this window while the pipeline is running,
 * surface an interruption message (Req 9.5). Kept as a module constant so a
 * faked clock in tests can advance past it deterministically.
 */
const INACTIVITY_TIMEOUT_MS = 60_000;

/**
 * Friendly labels for the five conceptual pipeline node ids (Req 9.1). All five
 * can appear in progress payloads, so each is mapped.
 */
const NODE_LABELS: Record<string, string> = {
  extractor: "Reading your bill",
  auditor: "Checking the math and charges",
  rights_checker: "Reviewing patient rights",
  skeptic: "Double-checking the findings",
  writer: "Writing your summary and appeal letter",
};

function nodeLabel(nodeId: string | null | undefined): string {
  if (!nodeId) return "Starting up";
  return NODE_LABELS[nodeId] ?? nodeId;
}

type ViewState =
  | { kind: "running"; progress: ResultProgress }
  | { kind: "error"; error: ResultError; lastProgress: ResultProgress | null }
  | { kind: "failed"; message: string; lastProgress: ResultProgress | null }
  | { kind: "interrupted"; lastProgress: ResultProgress | null };

const INITIAL_PROGRESS: ResultProgress = {
  activeNode: null,
  completedNodes: [],
  completedCount: 0,
  total: TOTAL_NODES,
};

/** Normalize an SSE `progress` payload into the shared {@link ResultProgress}. */
function toResultProgress(event: PipelineProgressEvent): ResultProgress {
  return {
    activeNode: event.activeNode,
    completedNodes: event.completedNodes,
    completedCount: event.completedCount,
    total: event.total || TOTAL_NODES,
  };
}

export default function ProgressView({ sessionId }: { sessionId: string }) {
  const router = useRouter();
  const [view, setView] = useState<ViewState>({
    kind: "running",
    progress: INITIAL_PROGRESS,
  });

  // Keep the last progress we saw so node-error / interruption / transport-drop
  // states can retain it (Req 9.4, 9.5).
  const lastProgressRef = useRef<ResultProgress | null>(null);

  useEffect(() => {
    let active = true;
    let unsubscribe: (() => void) | null = null;
    let inactivityTimer: ReturnType<typeof setTimeout> | null = null;
    const controller = new AbortController();

    const clearInactivityTimer = () => {
      if (inactivityTimer) {
        clearTimeout(inactivityTimer);
        inactivityTimer = null;
      }
    };

    // (Re)arm the 60s no-event watchdog (Req 9.5). Called once on mount and on
    // every received event so an active stream never trips it.
    const resetInactivityTimer = () => {
      clearInactivityTimer();
      inactivityTimer = setTimeout(() => {
        if (!active) return;
        // Preserve the current progress picture — only add the interruption
        // notice (Req 9.5).
        setView((prev) => {
          if (prev.kind === "running") {
            return { kind: "interrupted", lastProgress: prev.progress };
          }
          return prev;
        });
      }, INACTIVITY_TIMEOUT_MS);
    };

    const stop = () => {
      active = false;
      clearInactivityTimer();
      if (unsubscribe) unsubscribe();
      unsubscribe = null;
      controller.abort();
    };

    const handleProgress = (event: PipelineProgressEvent) => {
      if (!active) return;
      resetInactivityTimer();
      const progress = toResultProgress(event);
      lastProgressRef.current = progress;
      setView({ kind: "running", progress });
    };

    const handleNodeError = (event: NodeErrorEvent) => {
      if (!active) return;
      resetInactivityTimer();
      // Retain the last progress we displayed (Req 9.4); do not clear it.
      setView({
        kind: "error",
        error: { failedNode: event.failedNode, message: event.message },
        lastProgress: lastProgressRef.current,
      });
    };

    const handleDone = () => {
      if (!active) return;
      resetInactivityTimer();
      stop();
      router.push(`/results/${encodeURIComponent(sessionId)}`);
    };

    // Transport/connection error. Try a one-shot status fetch as a fallback to
    // resolve a late completion/error; otherwise surface a connection message.
    const handleTransportError = async () => {
      if (!active) return;
      clearInactivityTimer();
      try {
        const status = await getResultStatus(sessionId, {
          signal: controller.signal,
        });
        if (!active) return;

        if (status.status === "completed") {
          stop();
          router.push(`/results/${encodeURIComponent(sessionId)}`);
          return;
        }

        if (status.status === "error") {
          stop();
          setView({
            kind: "error",
            error: status.error,
            lastProgress: lastProgressRef.current,
          });
          return;
        }

        // Still running but the stream dropped: keep showing progress and
        // surface a connection notice without discarding what we have.
        lastProgressRef.current = status.progress;
        setView({
          kind: "failed",
          message:
            "We lost the live connection to the analysis. It may still be running.",
          lastProgress: status.progress,
        });
      } catch {
        if (!active) return;
        setView({
          kind: "failed",
          message:
            "We lost connection to the analysis. Please refresh to try again.",
          lastProgress: lastProgressRef.current,
        });
      }
    };

    // Arm the watchdog before the first event can arrive, then subscribe.
    resetInactivityTimer();
    unsubscribe = subscribeToEvents(sessionId, {
      onProgress: handleProgress,
      onNodeError: handleNodeError,
      onDone: handleDone,
      onError: () => {
        void handleTransportError();
      },
    });

    return stop;
  }, [sessionId, router]);

  const progress =
    view.kind === "running"
      ? view.progress
      : (view.lastProgress ?? INITIAL_PROGRESS);
  const total = progress.total || TOTAL_NODES;

  return (
    <section
      aria-live="polite"
      className="flex flex-col gap-6 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800"
    >
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-sm font-medium text-zinc-500 dark:text-zinc-400">
          Steps completed
        </span>
        <span
          className="font-mono text-lg font-semibold tabular-nums"
          aria-label={`${progress.completedCount} of ${total} steps completed`}
        >
          {progress.completedCount}/{total}
        </span>
      </div>

      {view.kind === "running" ? (
        <div className="flex items-center gap-3">
          <span
            aria-hidden="true"
            className="h-2 w-2 animate-pulse rounded-full bg-blue-500"
          />
          <p className="text-base text-zinc-800 dark:text-zinc-200">
            {nodeLabel(view.progress.activeNode)}…
          </p>
        </div>
      ) : view.kind === "error" ? (
        <div role="alert" className="flex flex-col gap-1">
          <p className="text-base font-medium text-red-600 dark:text-red-400">
            Analysis stopped
          </p>
          <p className="text-sm text-zinc-700 dark:text-zinc-300">
            A problem occurred during “{nodeLabel(view.error.failedNode)}”.
          </p>
          {view.error.message ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              {view.error.message}
            </p>
          ) : null}
        </div>
      ) : view.kind === "interrupted" ? (
        <div role="alert" className="flex flex-col gap-1">
          <p className="text-base font-medium text-amber-600 dark:text-amber-400">
            The analysis seems to have stalled
          </p>
          <p className="text-sm text-zinc-700 dark:text-zinc-300">
            We haven’t received a progress update in a while. The analysis may
            still be running — your progress so far is shown above.
          </p>
        </div>
      ) : (
        <div role="alert" className="flex flex-col gap-1">
          <p className="text-base font-medium text-red-600 dark:text-red-400">
            Analysis unavailable
          </p>
          <p className="text-sm text-zinc-700 dark:text-zinc-300">
            {view.message}
          </p>
        </div>
      )}
    </section>
  );
}
