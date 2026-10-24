/**
 * Progress screen, keyed by session id.
 *
 * Stays a server component that awaits the route `params` and hands the
 * resolved `sessionId` to the `ProgressView` client component, which consumes
 * the backend SSE stream for live progress, the node-error state, and the 60s
 * interruption message (task 14.2; Req 9.1, 9.3, 9.4, 9.5).
 */

import ProgressView from "./ProgressView";

// Session-keyed route depends on a runtime id; render on request rather than
// prerendering (Cache Components / blocking route).
export const instant = false;

export default async function ProgressPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = await params;

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col gap-6 px-6 py-16">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">Analyzing your bill</h1>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          Session <code className="font-mono">{sessionId}</code>
        </p>
      </header>

      <ProgressView sessionId={sessionId} />
    </main>
  );
}
