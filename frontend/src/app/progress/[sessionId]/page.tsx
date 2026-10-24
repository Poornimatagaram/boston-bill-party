/**
 * Progress screen, keyed by session id.
 *
 * Stays a server component that awaits the route `params` and hands the
 * resolved `sessionId` to the `ProgressView` client component, which consumes
 * the backend SSE stream for live progress, the node-error state, and the 60s
 * interruption message (task 14.2; Req 9.1, 9.3, 9.4, 9.5).
 */

import AppShell from "../../components/AppShell";
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
    <AppShell
      title="Analyzing your bill"
      subtitle="Five specialized agents review your bill one step at a time."
    >
      <ProgressView sessionId={sessionId} />
    </AppShell>
  );
}
