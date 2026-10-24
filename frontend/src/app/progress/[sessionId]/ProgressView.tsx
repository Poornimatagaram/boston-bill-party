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
  Calculator,
  Check,
  FileSearch,
  LoaderCircle,
  PenLine,
  Scale,
  ShieldCheck,
  TriangleAlert,
  WifiOff,
  type LucideIcon,
} from "lucide-react";
import {
  getResultStatus,
  subscribeToEvents,
  TOTAL_NODES,
  type NodeErrorEvent,
  type ProgressEvent as PipelineProgressEvent,
  type ResultError,
  type ResultProgress,
} from "@/lib/api";
import type { Dictionary } from "@/i18n/dictionary";
import { useT } from "@/i18n/uiLanguage";

/**
 * If no SSE event arrives within this window while the pipeline is running,
 * surface an interruption message (Req 9.5). Kept as a module constant so a
 * faked clock in tests can advance past it deterministically.
 */
const INACTIVITY_TIMEOUT_MS = 60_000;

/**
 * The five pipeline agents in canonical order with their icons. Names,
 * descriptions, and activity labels come from the UI dictionary (`agents`).
 * Node ids mirror the backend's conceptual node names (Req 9.1).
 */
const AGENT_STEPS: ReadonlyArray<{ id: keyof Dictionary["agents"]; icon: LucideIcon }> = [
  { id: "extractor", icon: FileSearch },
  { id: "auditor", icon: Calculator },
  { id: "rights_checker", icon: Scale },
  { id: "skeptic", icon: ShieldCheck },
  { id: "writer", icon: PenLine },
];

/** Friendly activity label for a node id (for the active-node / error line). */
function nodeLabel(t: Dictionary, nodeId: string | null | undefined): string {
  if (!nodeId) return t.progress.startingUp;
  return t.agents[nodeId as keyof Dictionary["agents"]]?.activity ?? nodeId;
}

type ViewState =
  | { kind: "running"; progress: ResultProgress }
  | { kind: "error"; error: ResultError; lastProgress: ResultProgress | null }
  | {
      kind: "failed";
      // Dictionary key, resolved at render so it follows the UI language.
      messageKey: "lostLive" | "lostConnection";
      lastProgress: ResultProgress | null;
    }
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
  const t = useT();
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
          messageKey: "lostLive",
          lastProgress: status.progress,
        });
      } catch {
        if (!active) return;
        setView({
          kind: "failed",
          messageKey: "lostConnection",
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

  const pct = Math.round(
    (Math.min(progress.completedCount, total) / Math.max(total, 1)) * 100,
  );

  return (
    <section
      aria-live="polite"
      className="flex flex-col gap-6 rounded-3xl border border-white bg-white p-5 shadow-card-lg sm:p-8"
    >
      {/* Overall progress. */}
      <div className="flex flex-col gap-2">
        <div className="flex items-baseline justify-between gap-4">
          <span className="text-sm font-semibold text-navy-600">
            {t.progress.stepsCompleted}
          </span>
          <span
            className="font-mono text-lg font-bold text-navy-900 tabular-nums"
            aria-label={t.progress.stepsCompletedAria(progress.completedCount, total)}
          >
            {progress.completedCount}/{total}
          </span>
        </div>
        <div
          aria-hidden="true"
          className="h-2 overflow-hidden rounded-full bg-navy-100"
        >
          <div
            className="h-full rounded-full bg-linear-to-r from-teal-400 via-teal-500 to-coral-400 transition-[width] duration-500"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {/* Vertical stepper of the five agents (Req 9.1, 9.3). */}
      <ol className="flex flex-col">
        {AGENT_STEPS.map((step, i) => {
          const state = stepState(step.id);
          const isLast = i === AGENT_STEPS.length - 1;
          const AgentIcon = step.icon;
          return (
            <li key={step.id} className="flex gap-4">
              {/* Marker + connector rail. */}
              <div className="flex flex-col items-center">
                <span
                  aria-hidden="true"
                  className={[
                    "flex h-10 w-10 shrink-0 items-center justify-center rounded-full border-2 transition-colors",
                    state === "done"
                      ? "border-teal-500 bg-teal-500 text-white"
                      : state === "active"
                        ? "border-teal-400 bg-teal-50 text-teal-600"
                        : state === "failed"
                          ? "border-coral-400 bg-coral-50 text-coral-600"
                          : "border-navy-200 bg-white text-navy-300",
                  ].join(" ")}
                >
                  {state === "done" ? (
                    <Check className="h-5 w-5" strokeWidth={3} />
                  ) : state === "active" ? (
                    <LoaderCircle className="h-5 w-5 animate-spin" />
                  ) : state === "failed" ? (
                    <TriangleAlert className="h-5 w-5" />
                  ) : (
                    <AgentIcon className="h-4 w-4" />
                  )}
                </span>
                {!isLast ? (
                  <span
                    aria-hidden="true"
                    className={[
                      "my-1 w-0.5 flex-1 rounded-full",
                      state === "done" ? "bg-teal-400" : "bg-navy-100",
                    ].join(" ")}
                  />
                ) : null}
              </div>

              {/* Step text. */}
              <div className={isLast ? "pb-0" : "pb-6"}>
                <div className="flex flex-wrap items-center gap-2">
                  <p
                    className={[
                      "flex items-center gap-1.5 text-sm font-semibold",
                      state === "pending"
                        ? "text-navy-400"
                        : state === "failed"
                          ? "text-coral-700"
                          : "text-navy-900",
                    ].join(" ")}
                  >
                    {state !== "pending" ? (
                      <AgentIcon
                        className="h-4 w-4 text-teal-600"
                        aria-hidden="true"
                      />
                    ) : null}
                    {t.agents[step.id].name}
                  </p>
                  {state === "active" ? (
                    <span className="rounded-full bg-teal-100 px-2 py-0.5 text-[11px] font-semibold text-teal-700">
                      {t.progress.inProgress}
                    </span>
                  ) : state === "done" ? (
                    <span className="sr-only">{t.progress.doneSr}</span>
                  ) : state === "failed" ? (
                    <span className="rounded-full bg-coral-100 px-2 py-0.5 text-[11px] font-semibold text-coral-700">
                      {t.progress.failed}
                    </span>
                  ) : null}
                </div>
                <p className="mt-0.5 text-sm text-navy-500">
                  {t.agents[step.id].description}
                </p>
              </div>
            </li>
          );
        })}
      </ol>

      {/* Status line / error / interruption, below the stepper. */}
      {view.kind === "running" ? (
        <p className="flex items-center gap-2 rounded-xl bg-teal-50 px-4 py-3 text-sm font-medium text-teal-800">
          <LoaderCircle
            className="h-4 w-4 shrink-0 animate-spin"
            aria-hidden="true"
          />
          {nodeLabel(t, view.progress.activeNode)}…
        </p>
      ) : view.kind === "error" ? (
        <div
          role="alert"
          className="flex gap-3 rounded-xl border border-coral-200 bg-coral-50 p-4"
        >
          <TriangleAlert
            className="mt-0.5 h-5 w-5 shrink-0 text-coral-600"
            aria-hidden="true"
          />
          <div className="flex flex-col gap-1">
            <p className="text-sm font-semibold text-coral-800">
              {t.progress.stoppedTitle}
            </p>
            <p className="text-sm text-navy-700">
              {t.progress.problemDuring(nodeLabel(t, view.error.failedNode))}
            </p>
            {view.error.message ? (
              <p className="text-sm text-navy-500">{view.error.message}</p>
            ) : null}
          </div>
        </div>
      ) : view.kind === "interrupted" ? (
        <div
          role="alert"
          className="flex gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4"
        >
          <TriangleAlert
            className="mt-0.5 h-5 w-5 shrink-0 text-amber-600"
            aria-hidden="true"
          />
          <div className="flex flex-col gap-1">
            <p className="text-sm font-semibold text-amber-800">
              {t.progress.stalledTitle}
            </p>
            <p className="text-sm text-navy-700">{t.progress.stalledBody}</p>
          </div>
        </div>
      ) : (
        <div
          role="alert"
          className="flex gap-3 rounded-xl border border-coral-200 bg-coral-50 p-4"
        >
          <WifiOff
            className="mt-0.5 h-5 w-5 shrink-0 text-coral-600"
            aria-hidden="true"
          />
          <div className="flex flex-col gap-1">
            <p className="text-sm font-semibold text-coral-800">
              {t.progress.unavailableTitle}
            </p>
            <p className="text-sm text-navy-700">{t.progress[view.messageKey]}</p>
          </div>
        </div>
      )}
    </section>
  );
}
