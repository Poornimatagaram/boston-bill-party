/**
 * Results screen, keyed by session id.
 *
 * Stays a server component that awaits the route `params` and hands the
 * resolved `sessionId` to the `ResultsView` client component, which fetches the
 * analysis, renders the Summary, and drives the editable appeal letter plus the
 * explicit confirm gate (task 10.2). Voice Q&A (task 15.2) is layered on later
 * inside `ResultsView`.
 */

import ResultsView from "./ResultsView";

// Session-keyed route depends on a runtime id; render on request rather than
// prerendering (Cache Components / blocking route).
export const instant = false;

export default async function ResultsPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = await params;

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col gap-6 px-6 py-16">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">Analysis results</h1>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          Session <code className="font-mono">{sessionId}</code>
        </p>
      </header>

      <ResultsView sessionId={sessionId} />
    </main>
  );
}
