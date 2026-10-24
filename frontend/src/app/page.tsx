"use client";

/**
 * Upload screen (root route).
 *
 * Lets the user pick a PDF medical bill and choose an output language
 * (defaulting to English, Req 1.5). The chosen language is recorded for the
 * session (Req 1.4) and sent with the file to POST /sessions (Req 1.1). On
 * success we route to the Progress screen keyed by the returned sessionId.
 *
 * The backend performs authoritative validation; the client only surfaces
 * ApiError messages and basic pending/error states. The frontend never handles
 * the LLM API key.
 */

import { useRouter } from "next/navigation";
import { useCallback, useId, useRef, useState } from "react";
import {
  ApiError,
  createSession,
  DEFAULT_LANGUAGE,
  SUPPORTED_LANGUAGES,
  type SupportedLanguage,
} from "@/lib/api";
import AppShell from "./components/AppShell";

/** Accept a dropped/selected file only if it looks like a PDF. */
function isPdf(file: File): boolean {
  return (
    file.type === "application/pdf" ||
    file.name.toLowerCase().endsWith(".pdf")
  );
}

export default function UploadPage() {
  const router = useRouter();
  const fileInputId = useId();
  const languageSelectId = useId();
  const inputRef = useRef<HTMLInputElement | null>(null);

  const [file, setFile] = useState<File | null>(null);
  // Records the selected language for the session (Req 1.4); defaults to
  // English (Req 1.5).
  const [language, setLanguage] = useState<SupportedLanguage>(DEFAULT_LANGUAGE);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  const pickFile = useCallback((next: File | null) => {
    setFile(next);
    setError(null);
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragging(false);
      if (pending) return;
      const dropped = e.dataTransfer.files?.[0];
      if (!dropped) return;
      if (!isPdf(dropped)) {
        setError("That doesn’t look like a PDF. Please drop a PDF medical bill.");
        return;
      }
      pickFile(dropped);
    },
    [pending, pickFile],
  );

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || pending) return;

    setPending(true);
    setError(null);
    try {
      const { sessionId } = await createSession(file, language);
      router.push(`/progress/${encodeURIComponent(sessionId)}`);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Upload failed. Please try again.";
      setError(message);
      setPending(false);
    }
  }

  return (
    <AppShell
      title="Review your medical bill"
      subtitle="Upload a bill and our agents check it for billing errors and patient-rights issues."
    >
      <form
        onSubmit={handleSubmit}
        className="flex flex-col gap-6 rounded-2xl border border-border-subtle bg-surface p-5 shadow-card sm:p-7"
      >
        {/* Drag-and-drop PDF dropzone (click to browse too). */}
        <div className="flex flex-col gap-2">
          <span className="text-sm font-medium text-navy-700 dark:text-navy-100">
            Medical bill (PDF)
          </span>
          <div
            role="button"
            tabIndex={0}
            aria-disabled={pending}
            onClick={() => !pending && inputRef.current?.click()}
            onKeyDown={(e) => {
              if ((e.key === "Enter" || e.key === " ") && !pending) {
                e.preventDefault();
                inputRef.current?.click();
              }
            }}
            onDragOver={(e) => {
              e.preventDefault();
              if (!pending) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            className={[
              "flex flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed px-6 py-12 text-center transition-colors",
              pending ? "cursor-not-allowed opacity-60" : "cursor-pointer",
              dragging
                ? "border-teal-400 bg-teal-50 dark:bg-teal-900/20"
                : "border-navy-200 bg-navy-50/50 hover:border-teal-300 hover:bg-teal-50/60 dark:border-navy-700 dark:bg-navy-900/30 dark:hover:border-teal-500",
            ].join(" ")}
          >
            <span
              aria-hidden="true"
              className="flex h-14 w-14 items-center justify-center rounded-full bg-teal-100 text-2xl text-teal-700 dark:bg-teal-900/40 dark:text-teal-300"
            >
              📄
            </span>
            {file ? (
              <div className="flex flex-col gap-1">
                <span className="text-sm font-medium text-navy-900 dark:text-white">
                  {file.name}
                </span>
                <span className="text-xs text-navy-500 dark:text-navy-300">
                  {(file.size / 1024).toLocaleString(undefined, {
                    maximumFractionDigits: 0,
                  })}{" "}
                  KB · click or drop to replace
                </span>
              </div>
            ) : (
              <div className="flex flex-col gap-1">
                <span className="text-sm font-medium text-navy-900 dark:text-white">
                  Drag &amp; drop your PDF here
                </span>
                <span className="text-xs text-navy-500 dark:text-navy-300">
                  or click to browse your files
                </span>
              </div>
            )}
            <input
              id={fileInputId}
              ref={inputRef}
              name="pdf"
              type="file"
              accept="application/pdf,.pdf"
              required
              disabled={pending}
              onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
              className="sr-only"
            />
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <label
            htmlFor={languageSelectId}
            className="text-sm font-medium text-navy-700 dark:text-navy-100"
          >
            Output language
          </label>
          <select
            id={languageSelectId}
            name="language"
            value={language}
            disabled={pending}
            onChange={(e) => setLanguage(e.target.value as SupportedLanguage)}
            className="w-full rounded-xl border border-border-subtle bg-surface px-3 py-2.5 text-sm text-navy-900 outline-none focus:border-teal-400 focus:ring-2 focus:ring-teal-200 disabled:opacity-50 dark:text-white dark:focus:ring-teal-900"
          >
            {SUPPORTED_LANGUAGES.map(({ value, label }) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>

        {/* Synthetic-data notice (Req 12.1/12.2). */}
        <div className="flex items-start gap-3 rounded-xl border border-teal-200 bg-teal-50 px-4 py-3 text-sm text-teal-900 dark:border-teal-800 dark:bg-teal-900/20 dark:text-teal-100">
          <span aria-hidden="true" className="text-base">
            🔒
          </span>
          <p>
            <span className="font-semibold">Synthetic data only</span> — no real
            patient data. Please upload test bills, not real medical records.
          </p>
        </div>

        {error ? (
          <p
            role="alert"
            className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-300"
          >
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={!file || pending}
          aria-busy={pending}
          className="inline-flex items-center justify-center rounded-xl bg-navy-700 px-5 py-3 text-sm font-semibold text-white shadow-card transition-colors hover:bg-navy-600 focus:outline-none focus:ring-2 focus:ring-teal-300 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {pending ? "Uploading…" : "Analyze bill"}
        </button>
      </form>
    </AppShell>
  );
}
