"use client";

/**
 * Cold-start notice. Pings the backend on page load and, if it takes more than
 * 3 seconds, explains that the AI agents are waking up. The live region stays
 * mounted so screen readers hear the message when it appears.
 */

import { useSyncExternalStore } from "react";
import { LoaderCircle } from "lucide-react";
import { useT } from "@/i18n/uiLanguage";
import {
  getBackendStatus,
  getServerBackendStatus,
  subscribeBackendStatus,
} from "@/lib/backendStatus";

export default function WakeBanner() {
  const t = useT();
  const status = useSyncExternalStore(
    subscribeBackendStatus,
    getBackendStatus,
    getServerBackendStatus,
  );

  return (
    <div role="status" aria-live="polite">
      {status === "waking" ? (
        <div className="border-b border-teal-200 bg-teal-50">
          <p className="mx-auto flex max-w-6xl items-center gap-2 px-4 py-2.5 text-sm font-medium text-teal-900 sm:px-6">
            <LoaderCircle
              className="h-4 w-4 shrink-0 animate-spin text-teal-600"
              aria-hidden="true"
            />
            {t.wake.slow}
          </p>
        </div>
      ) : null}
    </div>
  );
}
