"use client";

/**
 * Voice Q&A control for the Results screen (task 15.2).
 *
 * Lets a user ask questions about the analyzed bill. Where the browser exposes
 * the Web Speech API it offers a microphone control that transcribes speech to
 * text for review before submission and reads the answer back aloud; where the
 * API is absent it falls back to a plain text input. Both paths submit to the
 * same backend via `ask(sessionId, question)`.
 *
 * Requirement coverage:
 *  - 10.1: WHERE SpeechRecognition exists, render the mic control immediately
 *    (within 1s — no artificial delay; feature-detected in an effect so SSR is
 *    unaffected).
 *  - 10.2: On activation, start recognition and DISPLAY the transcribed text in
 *    an editable field so the user reviews it BEFORE submitting.
 *  - 10.3: Only submit transcripts of 1–500 characters (submit disabled for
 *    empty or >500; the backend validates too) via `ask()`.
 *  - 10.4: If no speech is captured within 10s, stop listening and show a
 *    "didn't catch that" message, leaving the control inactive.
 *  - 10.5: On a transcription error other than no-speech (audio-capture,
 *    not-allowed, network, …) show a clear transcription-error message and do
 *    NOT submit.
 *  - 10.6: Display the returned answer text AND, where available, read it aloud
 *    with speechSynthesis (best-effort; display is required).
 *  - 10.7: On an answer error/timeout (ask() throws) show the error WITHOUT
 *    discarding the last successful answer already shown.
 *  - 10.8: WHERE the Web Speech API is absent, render a text-input fallback that
 *    calls the same `ask()`.
 *
 * SSR safety: all window/navigator access is feature-detected inside effects,
 * and this is a client component, so nothing touches browser globals during
 * server rendering or build.
 *
 * The frontend never handles the LLM API key.
 */

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { ApiError, ask, type SupportedLanguage } from "@/lib/api";
import ReadAloudButton from "./ReadAloudButton";

/** Map a SupportedLanguage to the BCP-47 tag for speech recognition. */
const RECOGNITION_LANG: Record<SupportedLanguage, string> = {
  en: "en-US",
  es: "es-US",
  pt: "pt-BR",
  ht: "ht-HT",
};

// ---------------------------------------------------------------------------
// Minimal Web Speech API typings
//
// The SpeechRecognition interfaces are not part of lib.dom for the project's
// TS target, so we declare just the surface we use rather than reaching for
// `any`. speechSynthesis / SpeechSynthesisUtterance ARE in lib.dom and are used
// directly.
// ---------------------------------------------------------------------------

interface SpeechRecognitionAlternativeLike {
  readonly transcript: string;
}

interface SpeechRecognitionResultLike {
  readonly isFinal: boolean;
  readonly length: number;
  item(index: number): SpeechRecognitionAlternativeLike;
  readonly [index: number]: SpeechRecognitionAlternativeLike;
}

interface SpeechRecognitionResultListLike {
  readonly length: number;
  item(index: number): SpeechRecognitionResultLike;
  readonly [index: number]: SpeechRecognitionResultLike;
}

interface SpeechRecognitionEventLike {
  readonly resultIndex: number;
  readonly results: SpeechRecognitionResultListLike;
}

interface SpeechRecognitionErrorEventLike {
  readonly error: string;
  readonly message?: string;
}

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onspeechend: (() => void) | null;
  onend: (() => void) | null;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

interface SpeechWindow extends Window {
  SpeechRecognition?: SpeechRecognitionCtor;
  webkitSpeechRecognition?: SpeechRecognitionCtor;
}

/** Resolve a SpeechRecognition constructor if the browser exposes one. */
function getSpeechRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as SpeechWindow;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Transcript length bounds the UI enforces before submit (Req 10.3). */
const MIN_QUESTION_CHARS = 1;
const MAX_QUESTION_CHARS = 500;

/** No-speech safety timeout (Req 10.4). */
const NO_SPEECH_TIMEOUT_MS = 10_000;

// ---------------------------------------------------------------------------
// Component state
// ---------------------------------------------------------------------------

/**
 * Whether/how the browser supports speech input.
 *
 * Resolved via {@link useSpeechSupport}: "unknown" on the server (and during
 * hydration), then "voice" or "text" on the client once browser globals can be
 * feature-detected. Feature detection is a static, client-only fact, so we read
 * it through useSyncExternalStore — React uses the stable server snapshot during
 * SSR/hydration and the client snapshot afterward, avoiding a hydration mismatch
 * without calling setState inside an effect.
 */
type Support = "unknown" | "voice" | "text";

/** Server snapshot: browser globals are unavailable, so support is unknown. */
function getServerSupport(): Support {
  return "unknown";
}

/** Client snapshot: feature-detect the Web Speech API (Req 10.1 / 10.8). */
function getClientSupport(): Support {
  return getSpeechRecognitionCtor() ? "voice" : "text";
}

/** No-op subscribe — speech-API support does not change at runtime. */
function subscribeNoop(): () => void {
  return () => {};
}

/** Read the (static, client-only) Web Speech API support level, SSR-safe. */
function useSpeechSupport(): Support {
  return useSyncExternalStore(
    subscribeNoop,
    getClientSupport,
    getServerSupport,
  );
}

/** State of the submit-to-backend round trip. */
type AskState =
  | { kind: "idle" }
  | { kind: "asking" }
  | { kind: "error"; message: string };

/** State of the microphone / recognition lifecycle. */
type MicState =
  | { kind: "idle" }
  | { kind: "listening" }
  | { kind: "no_speech" }
  | { kind: "error"; message: string };

/** Map a recognition error code to a user-facing transcription message. */
function transcriptionErrorMessage(code: string): string {
  switch (code) {
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone access was blocked. Allow microphone access to ask by voice, or type your question below.";
    case "audio-capture":
      return "We couldn't access a microphone. Check that one is connected, or type your question below.";
    case "network":
      return "A network problem interrupted speech recognition. Please try again, or type your question below.";
    default:
      return "We couldn't understand that. Please try again, or type your question below.";
  }
}

export default function VoiceQA({
  sessionId,
  language,
}: {
  sessionId: string;
  language: SupportedLanguage;
}) {
  const transcriptFieldId = useId();
  const charCountId = useId();
  const answerRegionId = useId();

  // Resolved on the client without touching browser globals during SSR
  // (Req 10.1 / 10.8).
  const support = useSpeechSupport();

  // The question text under review before submit — shared by both the voice
  // transcript field (Req 10.2) and the text fallback (Req 10.8).
  const [question, setQuestion] = useState("");

  const [mic, setMic] = useState<MicState>({ kind: "idle" });
  const [askState, setAskState] = useState<AskState>({ kind: "idle" });

  // The last SUCCESSFUL answer. Kept across subsequent errors (Req 10.7).
  const [answer, setAnswer] = useState<string | null>(null);

  // Live recognition instance + the no-speech safety timer.
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const noSpeechTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Tracks whether any speech was captured during the current listen session.
  const heardSpeechRef = useRef(false);

  const clearNoSpeechTimer = useCallback(() => {
    if (noSpeechTimerRef.current) {
      clearTimeout(noSpeechTimerRef.current);
      noSpeechTimerRef.current = null;
    }
  }, []);

  /** Tear down any active recognition session and its timer. */
  const stopRecognition = useCallback(() => {
    clearNoSpeechTimer();
    const rec = recognitionRef.current;
    if (rec) {
      rec.onresult = null;
      rec.onerror = null;
      rec.onspeechend = null;
      rec.onend = null;
      try {
        rec.abort();
      } catch {
        // ignore — some engines throw if already stopped
      }
      recognitionRef.current = null;
    }
  }, [clearNoSpeechTimer]);

  // Cancel recognition and any in-flight speech on unmount.
  useEffect(() => {
    return () => {
      stopRecognition();
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }
    };
  }, [stopRecognition]);

  /** Begin listening (Req 10.2, 10.4, 10.5). */
  const startListening = useCallback(() => {
    const Ctor = getSpeechRecognitionCtor();
    if (!Ctor) {
      // Defensive: this button only renders when support === "voice", so a
      // missing constructor here is unexpected. Surface it as a transcription
      // error rather than silently doing nothing.
      setMic({
        kind: "error",
        message: transcriptionErrorMessage("audio-capture"),
      });
      return;
    }

    // Reset any prior recognition session and transcript/error state.
    stopRecognition();
    heardSpeechRef.current = false;
    setQuestion("");
    setMic({ kind: "listening" });
    setAskState({ kind: "idle" });

    let recognition: SpeechRecognitionLike;
    try {
      recognition = new Ctor();
    } catch {
      setMic({
        kind: "error",
        message: transcriptionErrorMessage("audio-capture"),
      });
      return;
    }

    recognition.lang = RECOGNITION_LANG[language] ?? RECOGNITION_LANG.en;
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    recognition.onresult = (event) => {
      heardSpeechRef.current = true;
      clearNoSpeechTimer();
      // Concatenate all results (interim + final) into the current transcript
      // so the user sees text appear live BEFORE submitting (Req 10.2).
      let transcript = "";
      for (let i = 0; i < event.results.length; i += 1) {
        transcript += event.results[i][0].transcript;
      }
      setQuestion(transcript.slice(0, MAX_QUESTION_CHARS));
    };

    recognition.onerror = (event) => {
      clearNoSpeechTimer();
      recognitionRef.current = null;
      if (event.error === "no-speech") {
        // No speech detected — treat as the Req 10.4 case, not a hard error.
        setMic({ kind: "no_speech" });
        return;
      }
      if (event.error === "aborted") {
        // We aborted intentionally (e.g. restart/unmount); stay idle.
        setMic((prev) => (prev.kind === "listening" ? { kind: "idle" } : prev));
        return;
      }
      // Any other error is a transcription failure (Req 10.5).
      setMic({
        kind: "error",
        message: transcriptionErrorMessage(event.error),
      });
    };

    recognition.onspeechend = () => {
      // Speech ended; stop capturing and let onend settle the state.
      try {
        recognition.stop();
      } catch {
        // ignore
      }
    };

    recognition.onend = () => {
      clearNoSpeechTimer();
      recognitionRef.current = null;
      // If the engine ended without ever hearing speech, surface no-speech
      // (Req 10.4); otherwise return the control to idle with the transcript
      // left in place for review/submit.
      setMic((prev) => {
        if (prev.kind !== "listening") return prev;
        return heardSpeechRef.current ? { kind: "idle" } : { kind: "no_speech" };
      });
    };

    recognitionRef.current = recognition;

    try {
      recognition.start();
    } catch {
      recognitionRef.current = null;
      setMic({
        kind: "error",
        message: transcriptionErrorMessage("audio-capture"),
      });
      return;
    }

    // Safety timeout: if no speech arrives within 10s, stop and report it
    // (Req 10.4). onresult clears this as soon as any speech is captured.
    noSpeechTimerRef.current = setTimeout(() => {
      if (!heardSpeechRef.current) {
        stopRecognition();
        setMic({ kind: "no_speech" });
      }
    }, NO_SPEECH_TIMEOUT_MS);
  }, [clearNoSpeechTimer, stopRecognition, language]);

  /** Stop listening in response to the user (keeps any transcript). */
  const stopListening = useCallback(() => {
    const rec = recognitionRef.current;
    clearNoSpeechTimer();
    if (rec) {
      try {
        rec.stop();
      } catch {
        // ignore
      }
    }
    setMic((prev) => (prev.kind === "listening" ? { kind: "idle" } : prev));
  }, [clearNoSpeechTimer]);

  const trimmed = question.trim();
  const canSubmit =
    trimmed.length >= MIN_QUESTION_CHARS &&
    trimmed.length <= MAX_QUESTION_CHARS &&
    askState.kind !== "asking";

  /** Submit the reviewed transcript/text to the backend (Req 10.3, 10.6, 10.7). */
  const handleSubmit = useCallback(async () => {
    const q = question.trim();
    if (q.length < MIN_QUESTION_CHARS || q.length > MAX_QUESTION_CHARS) return;

    // Make sure we are not still listening when we submit.
    stopRecognition();
    setMic((prev) => (prev.kind === "listening" ? { kind: "idle" } : prev));

    setAskState({ kind: "asking" });
    try {
      const { answer: text } = await ask(sessionId, q);
      // Display the answer (required). It can be read aloud via the visible
      // "Read aloud" control below the answer (Req 10.6).
      setAnswer(text);
      setAskState({ kind: "idle" });
      setQuestion("");
    } catch (err) {
      // Answer error/timeout: show the message but KEEP the prior answer
      // visible (Req 10.7).
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "We couldn't retrieve an answer. Please try again.";
      setAskState({ kind: "error", message });
    }
  }, [question, sessionId, stopRecognition]);

  const onFormSubmit = useCallback(
    (e: React.FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      void handleSubmit();
    },
    [handleSubmit],
  );

  // Don't render anything until support is resolved (avoids a flash of the
  // wrong control and keeps SSR output stable).
  if (support === "unknown") {
    return null;
  }

  const isVoice = support === "voice";
  const isListening = mic.kind === "listening";
  const isAsking = askState.kind === "asking";
  const overLimit = trimmed.length > MAX_QUESTION_CHARS;

  return (
    <section
      aria-labelledby={`${transcriptFieldId}-heading`}
      className="flex flex-col gap-4 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800"
    >
      <div className="flex flex-col gap-1">
        <h2
          id={`${transcriptFieldId}-heading`}
          className="text-lg font-semibold tracking-tight"
        >
          Ask about your bill
        </h2>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          {isVoice
            ? "Use the microphone to ask a question, review the transcription, then send it."
            : "Type a question about your bill and send it."}
        </p>
      </div>

      {isVoice ? (
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={isListening ? stopListening : startListening}
            disabled={isAsking}
            aria-pressed={isListening}
            className={
              isListening
                ? "inline-flex items-center justify-center gap-2 rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-50"
                : "inline-flex items-center justify-center gap-2 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
            }
          >
            <span
              aria-hidden="true"
              className={
                isListening
                  ? "h-2 w-2 animate-pulse rounded-full bg-white"
                  : "h-2 w-2 rounded-full bg-current"
              }
            />
            {isListening ? "Stop listening" : "Ask by voice"}
          </button>
          {isListening ? (
            <span className="text-sm text-zinc-500 dark:text-zinc-400">
              Listening…
            </span>
          ) : null}
        </div>
      ) : null}

      {/* Mic status / no-speech / transcription error (Req 10.4, 10.5). */}
      {isVoice ? (
        <div aria-live="polite" className="min-h-5">
          {mic.kind === "no_speech" ? (
            <p className="text-sm text-amber-700 dark:text-amber-400">
              We didn&apos;t catch that — no speech was detected. Select “Ask by
              voice” to try again, or type your question below.
            </p>
          ) : mic.kind === "error" ? (
            <p role="alert" className="text-sm text-red-600 dark:text-red-400">
              {mic.message}
            </p>
          ) : null}
        </div>
      ) : null}

      {/* Transcript / question field — reviewed BEFORE submit (Req 10.2, 10.8). */}
      <form onSubmit={onFormSubmit} className="flex flex-col gap-3">
        <div className="flex items-baseline justify-between gap-3">
          <label htmlFor={transcriptFieldId} className="text-sm font-medium">
            {isVoice ? "Transcribed question" : "Your question"}
          </label>
          <span
            id={charCountId}
            className={
              overLimit
                ? "font-mono text-xs text-red-600 tabular-nums dark:text-red-400"
                : "font-mono text-xs text-zinc-500 tabular-nums dark:text-zinc-400"
            }
          >
            {trimmed.length} / {MAX_QUESTION_CHARS}
          </span>
        </div>
        <textarea
          id={transcriptFieldId}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          maxLength={MAX_QUESTION_CHARS}
          aria-describedby={charCountId}
          disabled={isAsking}
          rows={3}
          placeholder={
            isVoice
              ? "Your spoken question will appear here for review…"
              : "e.g. Which charges look like duplicates?"
          }
          className="w-full resize-y rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
        />
        <div>
          <button
            type="submit"
            disabled={!canSubmit}
            aria-busy={isAsking}
            className="inline-flex items-center justify-center rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            {isAsking ? "Getting an answer…" : "Send question"}
          </button>
        </div>
      </form>

      {/* Answer error/timeout — shown WITHOUT discarding a prior answer
          (Req 10.7). */}
      <div aria-live="assertive" className="min-h-0">
        {askState.kind === "error" ? (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {askState.message}
          </p>
        ) : null}
      </div>

      {/* Last successful answer — displayed with line breaks preserved and a
          visible "Read aloud" control (Req 10.6). */}
      {answer !== null ? (
        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
            Answer
          </h3>
          <div
            id={answerRegionId}
            aria-live="polite"
            className="rounded-md border border-zinc-200 bg-zinc-50 p-4 text-sm whitespace-pre-line text-zinc-800 dark:border-zinc-800 dark:bg-zinc-900/40 dark:text-zinc-200"
          >
            {answer}
          </div>
          <ReadAloudButton text={answer} language={language} />
        </div>
      ) : null}
    </section>
  );
}
