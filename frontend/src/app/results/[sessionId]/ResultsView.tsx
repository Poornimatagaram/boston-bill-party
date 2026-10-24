"use client";

/**
 * Results view, keyed by session id (task 10.2).
 *
 * Renders the Writer output in three tabs to keep scrolling short:
 *  - Overview: issue count, per-type breakdown, and the plain-language Summary
 *    (in the session language).
 *  - Issues: compact, color-coded Finding cards (long text is collapsible).
 *  - Appeal Letter: the editable Appeal_Letter (always English) and the explicit
 *    confirm gate (Req 8.1–8.7), a read-only translation toggle for
 *    non-English sessions, and share actions once confirmed.
 *
 * Behavior:
 *  - On mount, fetch `GET /sessions/{id}/result`. If the pipeline is still
 *    `running`, poll briefly ("finishing up") until it reaches `completed` or
 *    `error`, then render immediately — no artificial delay (Req 8.1).
 *  - The letter <textarea> is seeded exactly once and capped at 20,000
 *    characters (Req 8.3); edits are RETAINED until the user confirms (Req 8.4).
 *    All tab panels stay mounted (only hidden), so switching tabs never loses
 *    edits.
 *  - A single explicit "Confirm" control (Req 8.5) is the ONLY transmit
 *    trigger; it POSTs the CURRENT edited text via confirmLetter (Req 8.6, 8.7).
 *  - No Appeal_Letter (no credible issues, Req 7.4) -> no editor or confirm.
 *  - On a Writer/node `error`, show the failed-node message plus a RETRY action
 *    that re-fetches WITHOUT discarding any Summary already produced (Req 8.2).
 *
 * Static UI text follows the header's UI language. Client-side fallback error
 * messages are stored as flags/null and resolved at render for the same reason;
 * backend error details are shown as returned.
 *
 * Voice Q&A (task 15.2) is the floating chat assistant ({@link VoiceQA}),
 * rendered once results are available (Req 10.1).
 *
 * The frontend never handles the LLM API key.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  ArrowRight,
  CircleCheck,
  FileText,
  Flag,
  LayoutDashboard,
  ListChecks,
  LoaderCircle,
  RotateCcw,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
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
import type { Dictionary } from "@/i18n/dictionary";
import { useT } from "@/i18n/uiLanguage";
import IssueCard, { findingMeta } from "./IssueCard";
import LetterActions from "./LetterActions";
import LetterTranslation from "./LetterTranslation";
import ReadAloudButton from "./ReadAloudButton";
import { ReadAloudProvider } from "./readAloud";
import VoiceQA from "./VoiceQA";

/** Maximum characters the editable Appeal_Letter accepts (Req 8.3). */
const MAX_LETTER_CHARS = 20_000;

/** How often we poll while the pipeline is still `running` (ms). */
const POLL_INTERVAL_MS = 1000;

/** Friendly activity label for a node id (for error display). */
function nodeLabel(t: Dictionary, nodeId: string | null | undefined): string {
  if (!nodeId) return t.progress.startingUp;
  return t.agents[nodeId as keyof Dictionary["agents"]]?.activity ?? nodeId;
}

type TabId = "overview" | "issues" | "letter";

const TAB_ICONS: ReadonlyArray<{ id: TabId; icon: LucideIcon }> = [
  { id: "overview", icon: LayoutDashboard },
  { id: "issues", icon: ListChecks },
  { id: "letter", icon: FileText },
];

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
  // `message` is a backend detail; null means "use the localized fallback".
  | { kind: "failed"; message: string | null; notFound: boolean };

type ConfirmState =
  | { kind: "idle" }
  | { kind: "confirming" }
  | { kind: "confirmed" }
  | { kind: "error"; message: string | null };

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

/** Count Findings per type, in first-seen order, for the Overview breakdown. */
function countByType(findings: Finding[]): Array<[FindingType, number]> {
  const counts = new Map<FindingType, number>();
  for (const f of findings) counts.set(f.type, (counts.get(f.type) ?? 0) + 1);
  return [...counts.entries()];
}

export default function ResultsView({ sessionId }: { sessionId: string }) {
  const t = useT();
  const tabsId = useId();
  const letterFieldId = useId();
  const charCountId = useId();

  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const [confirm, setConfirm] = useState<ConfirmState>({ kind: "idle" });
  const [tab, setTab] = useState<TabId>("overview");

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

    async function poll() {
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
          timer = setTimeout(() => poll(), POLL_INTERVAL_MS);
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

        stop();
        setLoad({
          kind: "failed",
          notFound: err instanceof ApiError && err.status === 404,
          message: err instanceof ApiError ? err.message : null,
        });
      }
    }

    poll();
    return stop;
  }, [sessionId, reloadToken]);

  // Retry after a writer/node error: re-fetch status WITHOUT discarding the
  // Summary we already display (Req 8.2). The letter is only seeded once, so
  // prior edits survive too.
  const handleRetry = useCallback(() => {
    setConfirm({ kind: "idle" });
    setLoad((prev) => (prev.kind === "error" ? { kind: "loading" } : prev));
    setReloadToken((token) => token + 1);
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
      setConfirm({
        kind: "error",
        message: err instanceof ApiError ? err.message : null,
      });
    }
  }, [sessionId, letter]);

  const tabDomId = (id: TabId) => `${tabsId}-tab-${id}`;
  const panelDomId = (id: TabId) => `${tabsId}-panel-${id}`;

  /** Arrow/Home/End keyboard navigation across the tablist. */
  const onTabKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    const last = TAB_ICONS.length - 1;
    let next: number;
    if (e.key === "ArrowRight") next = index === last ? 0 : index + 1;
    else if (e.key === "ArrowLeft") next = index === 0 ? last : index - 1;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = last;
    else return;
    e.preventDefault();
    const nextId = TAB_ICONS[next].id;
    setTab(nextId);
    document.getElementById(tabDomId(nextId))?.focus();
  };

  if (load.kind === "loading" || load.kind === "finishing") {
    return (
      <section
        aria-live="polite"
        aria-busy="true"
        className="flex items-center gap-3 rounded-3xl border border-white bg-white p-6 shadow-card-lg"
      >
        <LoaderCircle
          className="h-5 w-5 shrink-0 animate-spin text-teal-500"
          aria-hidden="true"
        />
        <p className="text-base text-navy-800">
          {load.kind === "finishing" ? t.results.finishing : t.results.loading}
        </p>
      </section>
    );
  }

  if (load.kind === "failed") {
    return (
      <section
        role="alert"
        className="flex gap-3 rounded-3xl border border-coral-200 bg-coral-50 p-6 shadow-card"
      >
        <TriangleAlert
          className="mt-0.5 h-5 w-5 shrink-0 text-coral-600"
          aria-hidden="true"
        />
        <div className="flex flex-col gap-1">
          <p className="text-base font-semibold text-coral-800">
            {t.results.unavailableTitle}
          </p>
          <p className="text-sm text-navy-700">
            {load.notFound
              ? t.results.notFound
              : (load.message ?? t.results.loadFailed)}
          </p>
        </div>
      </section>
    );
  }

  // Both `loaded` and `error` have a Summary we can render. For `error` we also
  // show the failed-node message and a Retry action (Req 8.2).
  const result = load.kind === "loaded" ? load.result : load.partial;
  const isError = load.kind === "error";
  const issueCount = result.findings.length;
  const breakdown = countByType(result.findings);
  const hasLetter = letter !== null && result.appealLetter !== null;

  const tabLabels: Record<TabId, { full: string; short: string }> = {
    overview: { full: t.results.tabOverview, short: t.results.tabOverview },
    issues: { full: t.results.tabIssues, short: t.results.tabIssues },
    letter: { full: t.results.tabLetter, short: t.results.tabLetterShort },
  };

  return (
    <ReadAloudProvider>
      <div className="flex flex-col gap-5">
        {isError ? (
          <section
            role="alert"
            className="flex flex-col gap-3 rounded-2xl border border-coral-200 bg-coral-50 p-5 shadow-card sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="flex gap-3">
              <TriangleAlert
                className="mt-0.5 h-5 w-5 shrink-0 text-coral-600"
                aria-hidden="true"
              />
              <div className="flex flex-col gap-1">
                <p className="text-sm font-semibold text-coral-800">
                  {t.results.letterFailedTitle}
                </p>
                <p className="text-sm text-navy-700">
                  {t.progress.problemDuring(nodeLabel(t, load.error.failedNode))}
                </p>
                {load.error.message ? (
                  <p className="text-sm text-navy-500">{load.error.message}</p>
                ) : null}
                <p className="text-xs text-navy-500">{t.results.keptWhileRetry}</p>
              </div>
            </div>
            <button
              type="button"
              onClick={handleRetry}
              className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-navy-700 px-4 py-2 text-sm font-semibold text-white shadow-card hover:bg-navy-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-300"
            >
              <RotateCcw className="h-4 w-4" aria-hidden="true" />
              {t.results.retry}
            </button>
          </section>
        ) : null}

        {/* Tabs. */}
        <div
          role="tablist"
          aria-label={t.results.tabsAria}
          className="grid grid-cols-3 gap-1 rounded-2xl border border-navy-100 bg-white p-1.5 shadow-card"
        >
          {TAB_ICONS.map(({ id, icon: TabIcon }, i) => {
            const selected = tab === id;
            return (
              <button
                key={id}
                id={tabDomId(id)}
                type="button"
                role="tab"
                aria-selected={selected}
                aria-controls={panelDomId(id)}
                tabIndex={selected ? 0 : -1}
                onClick={() => setTab(id)}
                onKeyDown={(e) => onTabKeyDown(e, i)}
                className={[
                  "inline-flex min-w-0 items-center justify-center gap-1.5 rounded-xl px-2 py-2.5 text-sm font-semibold transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400",
                  selected
                    ? "bg-linear-to-r from-navy-700 to-teal-600 text-white shadow-card"
                    : "text-navy-600 hover:bg-navy-50",
                ].join(" ")}
              >
                <TabIcon className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span className="truncate sm:hidden">{tabLabels[id].short}</span>
                <span className="hidden truncate sm:inline">{tabLabels[id].full}</span>
                {id === "issues" && issueCount > 0 ? (
                  <span
                    className={`rounded-full px-1.5 text-[11px] font-bold ${
                      selected ? "bg-white/25 text-white" : "bg-coral-100 text-coral-700"
                    }`}
                  >
                    {issueCount}
                  </span>
                ) : null}
                {id === "letter" && confirm.kind === "confirmed" ? (
                  <>
                    <CircleCheck
                      className={`h-4 w-4 shrink-0 ${selected ? "text-white" : "text-teal-600"}`}
                      aria-hidden="true"
                    />
                    <span className="sr-only">{t.results.confirmedSr}</span>
                  </>
                ) : null}
              </button>
            );
          })}
        </div>

        {/* ---------------------------------------------------- Overview */}
        <div
          role="tabpanel"
          id={panelDomId("overview")}
          aria-labelledby={tabDomId("overview")}
          hidden={tab !== "overview"}
          tabIndex={0}
          className="focus:outline-none"
        >
          <div className="grid gap-4 md:grid-cols-5">
            {/* Issue count. */}
            <section
              className={[
                "relative flex flex-col gap-4 overflow-hidden rounded-3xl p-6 text-white shadow-card-lg md:col-span-2",
                issueCount === 0
                  ? "bg-linear-to-br from-teal-500 to-teal-700"
                  : "bg-linear-to-br from-navy-800 via-navy-700 to-teal-600",
              ].join(" ")}
            >
              <div
                aria-hidden="true"
                className="pointer-events-none absolute -top-10 -right-10 h-40 w-40 rounded-full bg-coral-400/30 blur-2xl"
              />
              <span
                aria-hidden="true"
                className="flex h-11 w-11 items-center justify-center rounded-2xl bg-white/15 ring-1 ring-white/25"
              >
                {issueCount === 0 ? (
                  <CircleCheck className="h-6 w-6" />
                ) : (
                  <Flag className="h-6 w-6" />
                )}
              </span>
              <div>
                <p className="text-5xl font-extrabold tracking-tight">
                  {issueCount}
                </p>
                <p className="text-lg font-semibold">
                  {issueCount === 0
                    ? t.results.noIssuesTitle
                    : t.results.issuesFoundLabel(issueCount)}
                </p>
                <p className="mt-1 text-sm text-white/80">
                  {issueCount === 0 ? t.results.noIssuesBody : t.results.issuesBody}
                </p>
              </div>

              {breakdown.length > 0 ? (
                <ul className="flex flex-wrap gap-2">
                  {breakdown.map(([type, count]) => {
                    const meta = findingMeta(type, t);
                    const TypeIcon = meta.icon;
                    return (
                      <li
                        key={type}
                        className="inline-flex items-center gap-1.5 rounded-full bg-white/15 px-2.5 py-1 text-xs font-medium ring-1 ring-white/20"
                      >
                        <TypeIcon className="h-3.5 w-3.5" aria-hidden="true" />
                        {count} × {meta.label}
                      </li>
                    );
                  })}
                </ul>
              ) : null}

              <div className="mt-auto flex flex-wrap gap-2">
                {issueCount > 0 ? (
                  <button
                    type="button"
                    onClick={() => setTab("issues")}
                    className="inline-flex items-center gap-1.5 rounded-xl bg-white px-3.5 py-2 text-sm font-semibold text-navy-800 shadow-card hover:bg-teal-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-300"
                  >
                    {t.results.viewIssues}
                    <ArrowRight className="h-4 w-4" aria-hidden="true" />
                  </button>
                ) : null}
                {hasLetter ? (
                  <button
                    type="button"
                    onClick={() => setTab("letter")}
                    className="inline-flex items-center gap-1.5 rounded-xl bg-white/15 px-3.5 py-2 text-sm font-semibold text-white ring-1 ring-white/30 hover:bg-white/25 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-300"
                  >
                    {t.results.openLetter}
                    <ArrowRight className="h-4 w-4" aria-hidden="true" />
                  </button>
                ) : null}
              </div>
            </section>

            {/* Summary (Req 8.1), in the session language. */}
            <section className="flex flex-col gap-3 rounded-3xl border border-navy-100 bg-white p-5 shadow-card sm:p-6 md:col-span-3">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-lg font-bold tracking-tight text-navy-900">
                  {t.results.summary}
                </h2>
                {result.summaryEntries.length > 0 ? (
                  <ReadAloudButton
                    id="summary-all"
                    text={result.summaryEntries.join(". ")}
                    language={result.language}
                  />
                ) : null}
              </div>
              {result.summaryEntries.length > 0 ? (
                <ul lang={result.language} className="flex flex-col gap-1.5 text-sm text-navy-800">
                  {result.summaryEntries.map((entry, i) => (
                    <li
                      key={i}
                      className="flex items-start gap-2 rounded-xl px-1 py-1 hover:bg-navy-50/60"
                    >
                      <span
                        aria-hidden="true"
                        className="mt-2.5 h-1.5 w-1.5 shrink-0 rounded-full bg-teal-500"
                      />
                      <span className="flex-1 pt-1 leading-relaxed">{entry}</span>
                      <ReadAloudButton
                        id={`summary-entry-${i}`}
                        text={entry}
                        language={result.language}
                        label={t.readAloud.labels.summaryItem}
                        iconOnly
                      />
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-navy-500">{t.results.noSummary}</p>
              )}
            </section>
          </div>
        </div>

        {/* ------------------------------------------------------ Issues */}
        <div
          role="tabpanel"
          id={panelDomId("issues")}
          aria-labelledby={tabDomId("issues")}
          hidden={tab !== "issues"}
          tabIndex={0}
          className="focus:outline-none"
        >
          {issueCount > 0 ? (
            <ul className="grid gap-3 lg:grid-cols-2">
              {result.findings.map((f) => (
                <IssueCard key={f.id} finding={f} />
              ))}
            </ul>
          ) : (
            <EmptyState
              icon={CircleCheck}
              title={t.results.issuesEmptyTitle}
              body={t.results.issuesEmptyBody}
            />
          )}
        </div>

        {/* ---------------------------------------------- Appeal letter */}
        <div
          role="tabpanel"
          id={panelDomId("letter")}
          aria-labelledby={tabDomId("letter")}
          hidden={tab !== "letter"}
          tabIndex={0}
          className="focus:outline-none"
        >
          {hasLetter && letter !== null ? (
            <section className="flex flex-col gap-3 rounded-3xl border border-navy-100 bg-white p-5 shadow-card sm:p-6">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-1">
                  <label
                    htmlFor={letterFieldId}
                    className="text-lg font-bold tracking-tight text-navy-900"
                  >
                    {t.letter.title}
                  </label>
                  {/* The letter is always English, so it is read with an
                      English voice. */}
                  <ReadAloudButton
                    id="appeal-letter"
                    text={letter}
                    language="en"
                    label={t.readAloud.labels.letter}
                    iconOnly
                  />
                </div>
                <span
                  id={charCountId}
                  className="font-mono text-xs text-navy-500 tabular-nums"
                >
                  {letter.length.toLocaleString()} /{" "}
                  {MAX_LETTER_CHARS.toLocaleString()}
                </span>
              </div>
              <p className="text-sm text-navy-500">{t.letter.hint}</p>
              <textarea
                id={letterFieldId}
                lang="en"
                value={letter}
                maxLength={MAX_LETTER_CHARS}
                aria-describedby={charCountId}
                disabled={confirm.kind === "confirming"}
                onChange={(e) => {
                  setLetter(e.target.value);
                  // Any edit after a confirm/error returns the control to idle
                  // so the user can confirm the newly edited text.
                  setConfirm((prev) =>
                    prev.kind === "idle" || prev.kind === "confirming"
                      ? prev
                      : { kind: "idle" },
                  );
                }}
                rows={14}
                className="w-full resize-y rounded-2xl border border-navy-200 bg-navy-50/40 px-4 py-3 font-mono text-sm leading-relaxed text-navy-900 outline-none focus:border-teal-400 focus:bg-white focus:ring-2 focus:ring-teal-200 disabled:opacity-60"
              />

              {result.language !== "en" ? (
                <LetterTranslation
                  sessionId={sessionId}
                  letter={letter}
                  language={result.language}
                />
              ) : null}

              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={handleConfirm}
                  disabled={confirm.kind === "confirming"}
                  aria-busy={confirm.kind === "confirming"}
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-linear-to-r from-navy-700 to-teal-600 px-5 py-2.5 text-sm font-semibold text-white shadow-card transition-all hover:brightness-110 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-300 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {confirm.kind === "confirming" ? (
                    <>
                      <LoaderCircle
                        className="h-4 w-4 animate-spin"
                        aria-hidden="true"
                      />
                      {t.letter.confirming}
                    </>
                  ) : confirm.kind === "confirmed" ? (
                    <>
                      <CircleCheck className="h-4 w-4" aria-hidden="true" />
                      {t.letter.confirmed}
                    </>
                  ) : (
                    t.letter.confirm
                  )}
                </button>

                {/* Confirm / error status, announced to assistive tech. */}
                <div aria-live="polite" className="min-h-5">
                  {confirm.kind === "confirmed" ? (
                    <p className="text-sm font-medium text-teal-700">
                      {t.letter.confirmedMsg}
                    </p>
                  ) : confirm.kind === "error" ? (
                    <p role="alert" className="text-sm text-coral-700">
                      {confirm.message ?? t.letter.submitFailed}
                    </p>
                  ) : null}
                </div>
              </div>

              {confirm.kind === "confirmed" ? (
                <LetterActions letter={letter} />
              ) : null}
            </section>
          ) : isError ? (
            <EmptyState
              icon={TriangleAlert}
              title={t.letter.unavailableTitle}
              body={t.letter.unavailableBody}
            />
          ) : (
            <EmptyState
              icon={CircleCheck}
              title={t.letter.notNeededTitle}
              body={t.letter.notNeededBody}
            />
          )}
        </div>

        {/* Voice Q&A chat assistant (task 15.2, Req 10.1). */}
        <VoiceQA sessionId={sessionId} language={result.language} />
      </div>
    </ReadAloudProvider>
  );
}

function EmptyState({
  icon: Icon,
  title,
  body,
}: {
  icon: LucideIcon;
  title: string;
  body: string;
}) {
  return (
    <section className="flex flex-col items-center gap-3 rounded-3xl border border-navy-100 bg-white px-6 py-10 text-center shadow-card">
      <span
        aria-hidden="true"
        className="flex h-12 w-12 items-center justify-center rounded-2xl bg-teal-100 text-teal-700"
      >
        <Icon className="h-6 w-6" />
      </span>
      <p className="text-base font-semibold text-navy-900">{title}</p>
      <p className="max-w-md text-sm text-navy-500">{body}</p>
    </section>
  );
}
