/**
 * Backend wake-up status, read with `useSyncExternalStore`.
 *
 * Free hosting tiers put the backend to sleep when idle, so the first request
 * can take up to a minute. On first subscribe (page load) we ping `GET /health`.
 * If it hasn't answered within {@link SLOW_AFTER_MS}, the status becomes
 * "waking" so the UI can explain the delay. A failed ping (for example the
 * host's 502/503 while it boots) is retried until {@link GIVE_UP_AFTER_MS}.
 */
import { checkHealth } from "./api";

export type BackendStatus = "checking" | "waking" | "ready";

/** Show the wake-up message once the ping takes longer than this. */
export const SLOW_AFTER_MS = 3_000;
/** Pause between failed pings. */
const RETRY_DELAY_MS = 3_000;
/** Stop pinging after this long; real requests will surface any error. */
const GIVE_UP_AFTER_MS = 120_000;
/** Bound a single ping so a hung connection is retried. */
const PING_TIMEOUT_MS = 70_000;

let status: BackendStatus = "checking";
let started = false;
const listeners = new Set<() => void>();

function setStatus(next: BackendStatus): void {
  if (next === status) return;
  status = next;
  for (const listener of listeners) listener();
}

async function pingUntilReady(): Promise<void> {
  const startedAt = Date.now();
  const slowTimer = setTimeout(() => {
    if (status === "checking") setStatus("waking");
  }, SLOW_AFTER_MS);

  while (Date.now() - startedAt < GIVE_UP_AFTER_MS) {
    try {
      await checkHealth({ signal: AbortSignal.timeout(PING_TIMEOUT_MS) });
      clearTimeout(slowTimer);
      setStatus("ready");
      return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
    }
  }
  clearTimeout(slowTimer);
}

export function subscribeBackendStatus(listener: () => void): () => void {
  listeners.add(listener);
  if (!started) {
    started = true;
    void pingUntilReady();
  }
  return () => {
    listeners.delete(listener);
  };
}

export function getBackendStatus(): BackendStatus {
  return status;
}

/** Server render: nothing is known yet. */
export function getServerBackendStatus(): BackendStatus {
  return "checking";
}
