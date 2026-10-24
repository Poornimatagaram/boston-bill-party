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
 * The five pipeline agents in canonical order, each with a demo-friendly name
 * and a one-line description. Drives the vertical stepper and the active-node
 * label (Req 9.1). Node ids mirror the backend's conceptual node names.
 */
const AGENT_STEPS: ReadonlyArray<{
  id: string;
  name: string;
  description: string;
}> = [
  {
    id: "extractor",
    name: "Extractor",
    description: "Reads your bill and pulls out the line items.",
  },
  {
    id: "auditor",
    name: "Auditor",
    description: "Checks the math and looks for duplicate charges.",
  },
  {
    id: "rights_checker",
    name: "Rights Checker",
    description: "Reviews the bill against patient-rights rules.",
  },
  {
    id: "skeptic",
    name: "Skeptic",
    description: "Double-checks each finding and drops weak ones.",
  },
  {
    id: "writer",
    name: "Writer",
    description: "Writes your plain-language summary and appeal letter.",
  },
];

/** Friendly labels for the five node ids (for the active-node / error line). */
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

  const completedSet = new Set(progress.completedNodes);
  const activeId = view.kind === "running" ? progress.activeNode : null;
  const failedId = view.kind === "error" ? view.error.failedNode : null;

  type StepState = "done" | "active" | "failed" | "pending";
  const stepState = (id: string): StepState => {
    if (completedSet.has(id)) return "done";
    if (id === failedId) return "failed";
    if (id === activeId) return "active";
    return "pending";
  };

  return (
    <section
      aria-live="polite"
      className="flex flex-col gap-6 rounded-2xl border border-border-subtle bg-surface p-5 shadow-card sm:p-7"
    >
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-sm font-medium text-navy-500 dark:text-navy-200">
          Steps completed
        </span>
        <span
          className="font-mono text-lg font-semibold tabular-nums text-navy-800 dark:text-white"
          aria-label={`${progress.completedCount} of ${total} steps completed`}
        >
          {progress.completedCount}/{total}
        </span>
      </div>

      {/* Vertical stepper of the five agents (Req 9.1, 9.3). */}
      <ol className="flex flex-col">
        {AGENT_STEPS.map((step, i) => {
          const state = stepState(step.id);
          const isLast = i === AGENT_STEPS.length - 1;
          return (
            <li key={step.id} className="flex gap-4">
              {/* Marker + connector rail. */}
              <div className="flex flex-col items-center">
                <span
                  aria-hidden="true"
                  className={[
                    "flex h-9 w-9 shrink-0 items-center justify-center rounded-full border-2 text-sm font-semibold transition-colors",
                    state === "done"
                      ? "border-teal-500 bg-teal-500 text-white"
                      : state === "active"
                        ? "border-teal-400 bg-teal-50 text-teal-700 dark:bg-teal-900/30 dark:text-teal-200"
                        : state === "failed"
                          ? "border-red-400 bg-red-50 text-red-600 dark:bg-red-950/40 dark:text-red-300"
                          : "border-navy-200 bg-surface text-navy-400 dark:border-navy-700 dark:text-navy-400",
                  ].join(" ")}
                >
                  {state === "done" ? (
                    "✓"
                  ) : state === "active" ? (
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-teal-500 border-t-transparent" />
                  ) : state === "failed" ? (
                    "!"
                  ) : (
                    i + 1
                  )}
                </span>
                {!isLast ? (
                  <span
                    aria-hidden="true"
                    className={[
                      "my-1 w-0.5 flex-1",
                      state === "done"
                        ? "bg-teal-400"
                        : "bg-navy-100 dark:bg-navy-700",
                    ].join(" ")}
                  />
                ) : null}
              </div>

              {/* Step text. */}
              <div className={isLast ? "pb-0" : "pb-6"}>
                <div className="flex items-center gap-2">
                  <p
                    className={[
                      "text-sm font-semibold",
                      state === "pending"
                        ? "text-navy-400 dark:text-navy-400"
                        : state === "failed"
                          ? "text-red-600 dark:text-red-300"
                          : "text-navy-900 dark:text-white",
                    ].join(" ")}
                  >
                    {step.name}
                  </p>
                  {state === "active" ? (
                    <span className="rounded-full bg-teal-100 px-2 py-0.5 text-[11px] font-medium text-teal-700 dark:bg-teal-900/40 dark:text-teal-200">
                      In progress
                    </span>
                  ) : null}
                </div>
                <p className="mt-0.5 text-sm text-navy-500 dark:text-navy-300">
                  {step.description}
                </p>
              </div>
            </li>
          );
        })}
      </ol>

      {/* Status line / error / interruption, below the stepper. */}
      {view.kind === "running" ? (
        <p className="text-sm text-navy-600 dark:text-navy-200">
          {nodeLabel(view.progress.activeNode)}…
        </p>
      ) : view.kind === "error" ? (
        <div
          role="alert"
          className="flex flex-col gap-1 rounded-xl border border-red-200 bg-red-50 p-4 dark:border-red-900/60 dark:bg-red-950/40"
        >
          <p className="text-sm font-semibold text-red-700 dark:text-red-300">
            Analysis stopped
          </p>
          <p className="text-sm text-navy-700 dark:text-navy-200">
            A problem occurred during “{nodeLabel(view.error.failedNode)}”.
          </p>
          {view.error.message ? (
            <p className="text-sm text-navy-500 dark:text-navy-300">
              {view.error.message}
            </p>
          ) : null}
        </div>
      ) : view.kind === "interrupted" ? (
        <div
          role="alert"
          className="flex flex-col gap-1 rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-900/60 dark:bg-amber-950/30"
        >
          <p className="text-sm font-semibold text-amber-700 dark:text-amber-300">
            The analysis seems to have stalled
          </p>
          <p className="text-sm text-navy-700 dark:text-navy-200">
            We haven’t received a progress update in a while. The analysis may
            still be running — your progress so far is shown above.
          </p>
        </div>
      ) : (
        <div
          role="alert"
          className="flex flex-col gap-1 rounded-xl border border-red-200 bg-red-50 p-4 dark:border-red-900/60 dark:bg-red-950/40"
        >
          <p className="text-sm font-semibold text-red-700 dark:text-red-300">
            Analysis unavailable
          </p>
          <p className="text-sm text-navy-700 dark:text-navy-200">
            {view.message}
          </p>
        </div>
      )}
    </section>
  );
}
