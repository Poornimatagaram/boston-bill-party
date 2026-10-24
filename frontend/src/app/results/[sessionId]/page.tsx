/**
 * Results screen, keyed by session id.
 *
 * Stays a server component that awaits the route `params` and hands the
 * resolved `sessionId` to the `ResultsView` client component, which fetches the
 * analysis, renders the Summary, and drives the editable appeal letter plus the
 * explicit confirm gate (task 10.2). Voice Q&A (task 15.2) is layered on later
 * inside `ResultsView`.
 */

import AppShell from "../../components/AppShell";
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
    <AppShell
      title="Your results"
      subtitle="Here’s what our agents found, plus a ready-to-send appeal letter."
    >
      <ResultsView sessionId={sessionId} />
    </AppShell>
  );
}
