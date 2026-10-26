/**
 * Friendly, translated error text.
 *
 * The backend sends a fixed English message for node-fatal errors and generic
 * details for provider failures; neither is shown as-is. Node errors are
 * described by the failed node, and server-side (5xx) failures fall back to
 * each screen's own translated message.
 */
import { ApiError } from "@/lib/api";
import type { Dictionary } from "./dictionary";

/** Translated message for a node-fatal pipeline error. */
export function nodeErrorMessage(t: Dictionary, failedNode: string | null | undefined): string {
  switch (failedNode) {
    case "extractor":
      return t.errors.extractor;
    case "auditor":
    case "rights_checker":
    case "skeptic":
      return t.errors.check;
    case "writer":
      return t.errors.writer;
    default:
      return t.errors.generic;
  }
}

/**
 * Backend detail that is safe to show: client errors (4xx, e.g. "file too
 * large") only. Returns null for server/provider failures and non-API errors
 * so callers use their translated fallback.
 */
export function clientErrorDetail(err: unknown): string | null {
  return err instanceof ApiError && err.status >= 400 && err.status < 500
    ? err.message
    : null;
}
