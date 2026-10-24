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
import { useId, useState } from "react";
import {
  ApiError,
  createSession,
  DEFAULT_LANGUAGE,
  SUPPORTED_LANGUAGES,
  type SupportedLanguage,
} from "@/lib/api";

export default function UploadPage() {
  const router = useRouter();
  const fileInputId = useId();
  const languageSelectId = useId();

  const [file, setFile] = useState<File | null>(null);
  // Records the selected language for the session (Req 1.4); defaults to
  // English (Req 1.5).
  const [language, setLanguage] = useState<SupportedLanguage>(DEFAULT_LANGUAGE);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col gap-6 px-6 py-16">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">Boston Bill Party</h1>
        <p className="text-zinc-600 dark:text-zinc-400">
          Upload a medical bill to review it for billing errors and patient-rights
          issues.
        </p>
      </header>

      <form
        onSubmit={handleSubmit}
        className="flex flex-col gap-6 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={fileInputId} className="text-sm font-medium">
            Medical bill (PDF)
          </label>
          <input
            id={fileInputId}
            name="pdf"
            type="file"
            accept="application/pdf,.pdf"
            required
            disabled={pending}
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setError(null);
            }}
            className="block w-full text-sm text-zinc-700 file:mr-4 file:rounded-md file:border-0 file:bg-zinc-900 file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-zinc-700 disabled:opacity-50 dark:text-zinc-300 dark:file:bg-zinc-100 dark:file:text-zinc-900"
          />
        </div>

        <div className="flex flex-col gap-2">
          <label htmlFor={languageSelectId} className="text-sm font-medium">
            Output language
          </label>
          <select
            id={languageSelectId}
            name="language"
            value={language}
            disabled={pending}
            onChange={(e) => setLanguage(e.target.value as SupportedLanguage)}
            className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
          >
            {SUPPORTED_LANGUAGES.map(({ value, label }) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>

        {error ? (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={!file || pending}
          aria-busy={pending}
          className="inline-flex items-center justify-center rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          {pending ? "Uploading…" : "Analyze bill"}
        </button>
      </form>
    </main>
  );
}
