"use client";

/**
 * Upload card (home page `#upload` section).
 *
 * Lets the user pick a PDF medical bill (drag-and-drop or browse) and choose an
 * output language (defaulting to English, Req 1.5). The chosen language is
 * recorded for the session (Req 1.4) and sent with the file to POST /sessions
 * (Req 1.1). On success we route to the Progress screen keyed by the returned
 * sessionId.
 *
 * The backend performs authoritative validation; the client only surfaces
 * ApiError messages and basic pending/error states. The frontend never handles
 * the LLM API key.
 */

import { useRouter } from "next/navigation";
import { useCallback, useId, useRef, useState } from "react";
import {
  ArrowRight,
  FileText,
  FileUp,
  Languages,
  LoaderCircle,
  ShieldCheck,
} from "lucide-react";
import {
  ApiError,
  createSession,
  SUPPORTED_LANGUAGES,
  type SupportedLanguage,
} from "@/lib/api";
import { useT, useUiLanguage } from "@/i18n/uiLanguage";

/** Accept a dropped/selected file only if it looks like a PDF. */
function isPdf(file: File): boolean {
  return (
    file.type === "application/pdf" ||
    file.name.toLowerCase().endsWith(".pdf")
  );
}

/** Synthetic sample bill served from `public/samples/` (copied from the eval set). */
const SAMPLE_BILL_URL = "/samples/bill_10.pdf";
const SAMPLE_BILL_NAME = "sample_bill_10.pdf";

export default function UploadCard() {
  const router = useRouter();
  const fileInputId = useId();
  const languageSelectId = useId();
  const inputRef = useRef<HTMLInputElement | null>(null);

  const t = useT();
  const uiLanguage = useUiLanguage();

  const [file, setFile] = useState<File | null>(null);
  // Records the selected language for the session (Req 1.4). Until the user
  // picks one here, it follows the UI language from the header (which itself
  // defaults to English, Req 1.5).
  const [chosenLanguage, setChosenLanguage] = useState<SupportedLanguage | null>(
    null,
  );
  const language = chosenLanguage ?? uiLanguage;
  const [pending, setPending] = useState(false);
  // Either a backend message, or a dictionary key for client-side errors so the
  // text follows the UI language.
  const [error, setError] = useState<
    | { kind: "server"; message: string }
    | { kind: "notPdf" | "failed" | "sampleFailed" | "demoLimit" }
    | null
  >(null);
  const [dragging, setDragging] = useState(false);
  const [loadingSample, setLoadingSample] = useState(false);
  const busy = pending || loadingSample;

  const pickFile = useCallback((next: File | null) => {
    setFile(next);
    setError(null);
  }, []);

  // Load the bundled synthetic bill so visitors can try the app without a PDF.
  // It only selects the file; the user still presses Analyze.
  async function loadSample() {
    if (busy) return;
    setLoadingSample(true);
    setError(null);
    try {
      const res = await fetch(SAMPLE_BILL_URL);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const sample = new File([blob], SAMPLE_BILL_NAME, {
        type: "application/pdf",
      });
      // Mirror the file into the native (required) input so form validation
      // treats it like a browsed file.
      if (inputRef.current) {
        const transfer = new DataTransfer();
        transfer.items.add(sample);
        inputRef.current.files = transfer.files;
      }
      pickFile(sample);
    } catch {
      setError({ kind: "sampleFailed" });
    } finally {
      setLoadingSample(false);
    }
  }

  const onDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragging(false);
      if (pending) return;
      const dropped = e.dataTransfer.files?.[0];
      if (!dropped) return;
      if (!isPdf(dropped)) {
        setError({ kind: "notPdf" });
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
      setError(
        err instanceof ApiError
          ? err.status === 429
            ? { kind: "demoLimit" }
            : { kind: "server", message: err.message }
          : { kind: "failed" },
      );
      setPending(false);
    }
  }

  const errorText =
    error === null
      ? null
      : error.kind === "server"
        ? error.message
        : error.kind === "notPdf"
          ? t.upload.notPdf
          : error.kind === "sampleFailed"
            ? t.upload.sampleFailed
            : error.kind === "demoLimit"
              ? t.upload.demoLimit
              : t.upload.failed;

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col gap-5 rounded-[1.75rem] border border-white bg-white/95 p-5 shadow-card-lg sm:p-8"
    >
      {/* Drag-and-drop PDF dropzone (click or keyboard to browse too). */}
      <div className="flex flex-col gap-2">
        <label
          htmlFor={fileInputId}
          className="text-sm font-semibold text-navy-800"
        >
          {t.upload.fileLabel}
        </label>
        <div
          role="button"
          tabIndex={0}
          aria-disabled={pending}
          aria-describedby={`${fileInputId}-hint`}
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
            "flex flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 sm:py-14",
            pending ? "cursor-not-allowed opacity-60" : "cursor-pointer",
            dragging
              ? "border-teal-400 bg-teal-50"
              : "border-navy-200 bg-linear-to-br from-navy-50/60 via-white to-teal-50/60 hover:border-teal-300",
          ].join(" ")}
        >
          <span
            aria-hidden="true"
            className="flex h-14 w-14 items-center justify-center rounded-2xl bg-linear-to-br from-teal-400 to-navy-600 text-white shadow-card"
          >
            <FileUp className="h-6 w-6" />
          </span>
          {file ? (
            <div className="flex flex-col gap-1">
              <span className="text-sm font-semibold break-all text-navy-900">
                {file.name}
              </span>
              <span id={`${fileInputId}-hint`} className="text-xs text-navy-500">
                {t.upload.fileMeta(
                  (file.size / 1024).toLocaleString(uiLanguage, {
                    maximumFractionDigits: 0,
                  }),
                )}
              </span>
            </div>
          ) : (
            <div className="flex flex-col gap-1">
              <span className="text-sm font-semibold text-navy-900">
                {t.upload.dropTitle}
              </span>
              <span id={`${fileInputId}-hint`} className="text-xs text-navy-500">
                {t.upload.dropHint}
              </span>
            </div>
          )}
        </div>
        {/* Native input drives the file dialog; the dropzone above is its
            keyboard/pointer surface, so the input itself skips the tab order. */}
        <input
          id={fileInputId}
          ref={inputRef}
          name="pdf"
          type="file"
          accept="application/pdf,.pdf"
          required
          tabIndex={-1}
          disabled={pending}
          onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
          className="sr-only"
        />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs text-navy-500">{t.upload.noPdf}</span>
          <button
            type="button"
            onClick={loadSample}
            disabled={busy}
            aria-busy={loadingSample}
            className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-teal-300 bg-white px-4 py-2 text-sm font-semibold text-teal-800 transition-colors hover:bg-teal-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-300 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loadingSample ? (
              <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <FileText className="h-4 w-4" aria-hidden="true" />
            )}
            {loadingSample ? t.upload.loadingSample : t.upload.trySample}
          </button>
        </div>
        {/* Announces the selected file (browsed, dropped, or sample). */}
        <p className="sr-only" aria-live="polite">
          {file ? t.upload.selected(file.name) : ""}
        </p>
      </div>

      <div className="flex flex-col gap-2">
        <label
          htmlFor={languageSelectId}
          className="flex items-center gap-2 text-sm font-semibold text-navy-800"
        >
          <Languages className="h-4 w-4 text-teal-600" aria-hidden="true" />
          {t.upload.outputLanguage}
        </label>
        <select
          id={languageSelectId}
          name="language"
          value={language}
          disabled={pending}
          aria-describedby={`${languageSelectId}-hint`}
          onChange={(e) =>
            setChosenLanguage(e.target.value as SupportedLanguage)
          }
          className="w-full rounded-xl border border-navy-200 bg-white px-3 py-2.5 text-sm text-navy-900 outline-none focus:border-teal-400 focus:ring-2 focus:ring-teal-200 disabled:opacity-50"
        >
          {SUPPORTED_LANGUAGES.map(({ value, label }) => (
            <option key={value} value={value} lang={value}>
              {label}
            </option>
          ))}
        </select>
        <p id={`${languageSelectId}-hint`} className="text-xs text-navy-500">
          {t.upload.outputLanguageHint}
        </p>
      </div>

      {/* Synthetic-data notice (Req 12.1/12.2). */}
      <div className="flex items-start gap-3 rounded-xl border border-teal-200 bg-teal-50 px-4 py-3 text-sm text-teal-900">
        <ShieldCheck
          className="mt-0.5 h-4 w-4 shrink-0 text-teal-600"
          aria-hidden="true"
        />
        <p>
          <span className="font-semibold">{t.upload.syntheticStrong}</span>{" "}
          {t.upload.syntheticRest}
        </p>
      </div>

      {errorText ? (
        <p
          role="alert"
          className="rounded-xl border border-coral-200 bg-coral-50 px-4 py-3 text-sm text-coral-800"
        >
          {errorText}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={!file || pending}
        aria-busy={pending}
        className="inline-flex items-center justify-center gap-2 rounded-xl bg-linear-to-r from-navy-700 via-navy-600 to-teal-600 px-5 py-3 text-sm font-semibold text-white shadow-card transition-all hover:brightness-110 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-300 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {pending ? (
          <>
            <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
            {t.upload.uploading}
          </>
        ) : (
          <>
            {t.upload.analyze}
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </>
        )}
      </button>
    </form>
  );
}
