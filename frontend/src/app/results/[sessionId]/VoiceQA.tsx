"use client";

/**
 * Voice Q&A chat assistant for the Results screen (task 15.2).
 *
 * A floating chat button (bottom-right) opens a chat panel with message
 * bubbles, a text input, a mic button, and a send button. Each answer has a
 * read-aloud control. Questions go to the backend via `ask(sessionId, q)` and
 * the conversation is kept in the panel (it survives closing/reopening).
 *
 * Requirement coverage:
 *  - 10.1: The chat control is available as soon as the Results screen loads;
 *    the mic is feature-detected client-side (no artificial delay).
 *  - 10.2: The mic transcribes into the editable input so the user reviews the
 *    text BEFORE sending. Recognition uses the session language.
 *  - 10.3: Only questions of 1–500 characters can be sent (the backend
 *    validates too).
 *  - 10.4: If no speech is captured within 10s, listening stops and a "didn't
 *    catch that" message is shown.
 *  - 10.5: Other transcription errors show a clear message and send nothing.
 *  - 10.6: Answers are displayed as bubbles and can be read aloud with
 *    speechSynthesis (Volume2 button on each answer).
 *  - 10.7: An answer error/timeout appears as an error bubble; earlier answers
 *    stay in the history.
 *  - 10.8: Without the Web Speech API the mic is hidden and the text input
 *    alone calls the same `ask()`.
 *
 * SSR safety: browser globals are only touched inside effects/handlers, and
 * speech support is read through useSyncExternalStore.
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
import {
  Bot,
  LoaderCircle,
  MessageCircle,
  Mic,
  SendHorizontal,
  Square,
  X,
} from "lucide-react";
import { ApiError, ask, type SupportedLanguage } from "@/lib/api";
import ReadAloudButton from "./ReadAloudButton";
import { useReadAloud } from "./readAloud";
import type { Dictionary } from "@/i18n/dictionary";
import { clientErrorDetail } from "@/i18n/errors";
import { useT } from "@/i18n/uiLanguage";

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
// `any`.
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

/** Question length bounds the UI enforces before send (Req 10.3). */
const MIN_QUESTION_CHARS = 1;
const MAX_QUESTION_CHARS = 500;

/** No-speech safety timeout (Req 10.4). */
const NO_SPEECH_TIMEOUT_MS = 10_000;

// ---------------------------------------------------------------------------
// Speech support detection (SSR-safe)
// ---------------------------------------------------------------------------

type Support = "unknown" | "voice" | "text";

function getServerSupport(): Support {
  return "unknown";
}

function getClientSupport(): Support {
  return getSpeechRecognitionCtor() ? "voice" : "text";
}

function subscribeNoop(): () => void {
  return () => {};
}

/** Read the (static, client-only) Web Speech API support level, SSR-safe. */
function useSpeechSupport(): Support {
  return useSyncExternalStore(subscribeNoop, getClientSupport, getServerSupport);
}

// ---------------------------------------------------------------------------
// State types
// ---------------------------------------------------------------------------

type MicState =
  | { kind: "idle" }
  | { kind: "listening" }
  | { kind: "no_speech" }
  // Recognition error code; turned into a localized message at render.
  | { kind: "error"; code: string };

type ChatMessage =
  | { id: number; role: "user" | "assistant"; text: string }
  // `text` is a backend detail; null means "use the localized fallback".
  // `demoLimit` marks the backend's 429 demo limit (localized at render).
  | { id: number; role: "error"; text: string | null; demoLimit?: boolean };

type NewChatMessage =
  | Omit<Extract<ChatMessage, { role: "user" | "assistant" }>, "id">
  | Omit<Extract<ChatMessage, { role: "error" }>, "id">;

/** Map a recognition error code to a user-facing transcription message. */
function transcriptionErrorMessage(t: Dictionary, code: string): string {
  switch (code) {
    case "not-allowed":
    case "service-not-allowed":
      return t.chat.micBlocked;
    case "audio-capture":
      return t.chat.micMissing;
    case "network":
      return t.chat.micNetwork;
    default:
      return t.chat.micGeneric;
  }
}

export default function VoiceQA({
  sessionId,
  language,
}: {
  sessionId: string;
  language: SupportedLanguage;
}) {
  const t = useT();
  const panelId = useId();
  const inputId = useId();
  const charCountId = useId();

  // Resolved on the client without touching browser globals during SSR
  // (Req 10.1 / 10.8).
  const support = useSpeechSupport();
  const isVoice = support === "voice";

  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [mic, setMic] = useState<MicState>({ kind: "idle" });
  const [asking, setAsking] = useState(false);
  // Conversation history, kept while the Results screen is mounted.
  const [messages, setMessages] = useState<ChatMessage[]>([]);

  const nextMessageIdRef = useRef(1);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const toggleRef = useRef<HTMLButtonElement | null>(null);
  const logEndRef = useRef<HTMLDivElement | null>(null);

  // Live recognition instance + the no-speech safety timer.
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const noSpeechTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Tracks whether any speech was captured during the current listen session.
  const heardSpeechRef = useRef(false);

  const { activeId: speakingId, stop: stopSpeaking } = useReadAloud();

  const appendMessage = useCallback((message: NewChatMessage) => {
    const id = nextMessageIdRef.current;
    nextMessageIdRef.current += 1;
    setMessages((prev) => [...prev, { ...message, id } as ChatMessage]);
  }, []);

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

  // Cancel recognition on unmount.
  useEffect(() => stopRecognition, [stopRecognition]);

  // Focus the input whenever the panel opens.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // Keep the newest message in view.
  useEffect(() => {
    if (open) logEndRef.current?.scrollIntoView({ block: "end" });
  }, [messages, asking, open]);

  /** Begin listening (Req 10.2, 10.4, 10.5). */
  const startListening = useCallback(() => {
    const Ctor = getSpeechRecognitionCtor();
    if (!Ctor) {
      setMic({ kind: "error", code: "audio-capture" });
      return;
    }

    stopRecognition();
    heardSpeechRef.current = false;
    setQuestion("");
    setMic({ kind: "listening" });

    let recognition: SpeechRecognitionLike;
    try {
      recognition = new Ctor();
    } catch {
      setMic({ kind: "error", code: "audio-capture" });
      return;
    }

    recognition.lang = RECOGNITION_LANG[language] ?? RECOGNITION_LANG.en;
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    recognition.onresult = (event) => {
      heardSpeechRef.current = true;
      clearNoSpeechTimer();
      // Show the live transcript in the input for review BEFORE sending.
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
        setMic({ kind: "no_speech" });
        return;
      }
      if (event.error === "aborted") {
        setMic((prev) => (prev.kind === "listening" ? { kind: "idle" } : prev));
        return;
      }
      setMic({ kind: "error", code: event.error });
    };

    recognition.onspeechend = () => {
      try {
        recognition.stop();
      } catch {
        // ignore
      }
    };

    recognition.onend = () => {
      clearNoSpeechTimer();
      recognitionRef.current = null;
      setMic((prev) => {
        if (prev.kind !== "listening") return prev;
        return heardSpeechRef.current ? { kind: "idle" } : { kind: "no_speech" };
      });
      inputRef.current?.focus();
    };

    recognitionRef.current = recognition;

    try {
      recognition.start();
    } catch {
      recognitionRef.current = null;
      setMic({ kind: "error", code: "audio-capture" });
      return;
    }

    // Safety timeout: no speech within 10s -> stop and report it (Req 10.4).
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
  const isListening = mic.kind === "listening";
  const canSend =
    trimmed.length >= MIN_QUESTION_CHARS &&
    trimmed.length <= MAX_QUESTION_CHARS &&
    !asking;

  /** Send the reviewed question (Req 10.3, 10.6, 10.7). */
  const handleSend = useCallback(async () => {
    const q = question.trim();
    if (q.length < MIN_QUESTION_CHARS || q.length > MAX_QUESTION_CHARS) return;
    if (asking) return;

    stopRecognition();
    setMic({ kind: "idle" });

    appendMessage({ role: "user", text: q });
    setQuestion("");
    setAsking(true);
    try {
      const { answer } = await ask(sessionId, q);
      appendMessage({ role: "assistant", text: answer });
    } catch (err) {
      // Answer error/timeout: shown in the thread; earlier answers stay
      // (Req 10.7). Restore the question so the user can resend it.
      // A 429 is the demo limit: shown as a localized message at render.
      appendMessage({
        role: "error",
        text:
          err instanceof ApiError && err.status === 429 ? null : clientErrorDetail(err),
        demoLimit: err instanceof ApiError && err.status === 429,
      });
      setQuestion((prev) => prev || q);
    } finally {
      setAsking(false);
    }
  }, [appendMessage, asking, question, sessionId, stopRecognition]);

  const openPanel = useCallback(() => setOpen(true), []);

  const closePanel = useCallback(() => {
    stopRecognition();
    setMic((prev) => (prev.kind === "listening" ? { kind: "idle" } : prev));
    // Stop an answer that is being read aloud from this panel.
    if (speakingId?.startsWith("chat-")) stopSpeaking();
    setOpen(false);
    toggleRef.current?.focus();
  }, [speakingId, stopRecognition, stopSpeaking]);

  const overLimit = trimmed.length > MAX_QUESTION_CHARS;

  return (
    <>
      {/* Floating chat toggle. */}
      <button
        ref={toggleRef}
        type="button"
        onClick={open ? closePanel : openPanel}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={open ? t.chat.closeAssistant : t.chat.open}
        className="fixed right-4 bottom-4 z-50 inline-flex h-14 items-center gap-2 rounded-full bg-linear-to-br from-teal-500 via-teal-600 to-navy-700 px-4 text-white shadow-card-lg transition-transform hover:scale-105 focus:outline-none focus-visible:ring-4 focus-visible:ring-teal-200 sm:right-6 sm:bottom-6"
      >
        {open ? (
          <X className="h-6 w-6" aria-hidden="true" />
        ) : (
          <MessageCircle className="h-6 w-6" aria-hidden="true" />
        )}
        <span className="hidden text-sm font-semibold sm:inline">
          {open ? t.chat.close : t.chat.open}
        </span>
      </button>

      {open ? (
        <section
          id={panelId}
          role="dialog"
          aria-modal="false"
          aria-labelledby={`${panelId}-title`}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.stopPropagation();
              closePanel();
            }
          }}
          className="fixed inset-x-3 top-20 bottom-22 z-50 flex flex-col overflow-hidden rounded-3xl border border-navy-100 bg-white shadow-card-lg sm:inset-x-auto sm:top-auto sm:right-6 sm:bottom-24 sm:h-[min(600px,calc(100vh-8rem))] sm:w-[400px]"
        >
          {/* Header. */}
          <div className="flex items-center gap-3 bg-linear-to-r from-navy-800 via-navy-700 to-teal-600 px-4 py-3 text-white">
            <span
              aria-hidden="true"
              className="flex h-9 w-9 items-center justify-center rounded-xl bg-white/15 ring-1 ring-white/25"
            >
              <Bot className="h-5 w-5" />
            </span>
            <div className="flex min-w-0 flex-1 flex-col">
              <h2 id={`${panelId}-title`} className="text-sm font-semibold">
                {t.chat.title}
              </h2>
              <p className="truncate text-xs text-teal-100">
                {isVoice ? t.chat.subtitleVoice : t.chat.subtitleText}
              </p>
            </div>
            <button
              type="button"
              onClick={closePanel}
              aria-label={t.chat.closeAssistant}
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-white/80 hover:bg-white/15 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-white"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>

          {/* Message log. */}
          <div
            role="log"
            aria-live="polite"
            aria-label={t.chat.conversation}
            className="flex flex-1 flex-col gap-3 overflow-y-auto bg-navy-50/50 px-4 py-4"
          >
            <AssistantBubble>{t.chat.greeting}</AssistantBubble>

            {messages.map((m) =>
              m.role === "user" ? (
                <div key={m.id} className="flex justify-end">
                  <p className="max-w-[85%] rounded-2xl rounded-br-md bg-linear-to-br from-navy-700 to-teal-600 px-3.5 py-2.5 text-sm whitespace-pre-line text-white shadow-sm">
                    <span className="sr-only">{t.chat.you}</span>
                    {m.text}
                  </p>
                </div>
              ) : m.role === "assistant" ? (
                <AssistantBubble key={m.id}>
                  <span className="sr-only">{t.chat.assistant}</span>
                  {m.text}
                  <span className="mt-1 -mb-1 flex justify-end">
                    <ReadAloudButton
                      id={`chat-${m.id}`}
                      text={m.text}
                      language={language}
                      label={t.readAloud.labels.answer}
                      iconOnly
                    />
                  </span>
                </AssistantBubble>
              ) : (
                <div key={m.id} className="flex justify-start">
                  <p
                    role="alert"
                    className="max-w-[85%] rounded-2xl rounded-bl-md border border-coral-200 bg-coral-50 px-3.5 py-2.5 text-sm text-coral-800"
                  >
                    {"demoLimit" in m && m.demoLimit
                      ? t.chat.demoLimit
                      : (m.text ?? t.chat.answerFailed)}
                  </p>
                </div>
              ),
            )}

            {asking ? (
              <div className="flex justify-start">
                <p className="inline-flex items-center gap-2 rounded-2xl rounded-bl-md bg-white px-3.5 py-2.5 text-sm text-navy-500 shadow-sm">
                  <LoaderCircle
                    className="h-4 w-4 animate-spin text-teal-500"
                    aria-hidden="true"
                  />
                  {t.chat.thinking}
                </p>
              </div>
            ) : null}
            <div ref={logEndRef} />
          </div>

          {/* Mic status (Req 10.4, 10.5). */}
          {isVoice && mic.kind !== "idle" ? (
            <div aria-live="polite" className="border-t border-navy-100 bg-white px-4 pt-2 text-xs">
              {mic.kind === "listening" ? (
                <p className="flex items-center gap-2 font-medium text-teal-700">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-coral-500" />
                  {t.chat.listening}
                </p>
              ) : mic.kind === "no_speech" ? (
                <p className="text-amber-700">{t.chat.noSpeech}</p>
              ) : (
                <p role="alert" className="text-coral-700">
                  {transcriptionErrorMessage(t, mic.code)}
                </p>
              )}
            </div>
          ) : null}

          {/* Composer. */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void handleSend();
            }}
            className="flex items-end gap-2 border-t border-navy-100 bg-white p-3"
          >
            {isVoice ? (
              <button
                type="button"
                onClick={isListening ? stopListening : startListening}
                disabled={asking}
                aria-pressed={isListening}
                aria-label={isListening ? t.chat.stopListening : t.chat.askByVoice}
                className={[
                  "inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 disabled:cursor-not-allowed disabled:opacity-50",
                  isListening
                    ? "bg-coral-500 text-white hover:bg-coral-600"
                    : "bg-navy-50 text-navy-600 hover:bg-teal-50 hover:text-teal-700",
                ].join(" ")}
              >
                {isListening ? (
                  <Square className="h-4 w-4 fill-current" aria-hidden="true" />
                ) : (
                  <Mic className="h-5 w-5" aria-hidden="true" />
                )}
              </button>
            ) : null}

            <div className="flex min-w-0 flex-1 flex-col">
              <label htmlFor={inputId} className="sr-only">
                {t.chat.yourQuestion}
              </label>
              <textarea
                id={inputId}
                ref={inputRef}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  // Enter sends; Shift+Enter inserts a newline.
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    !e.nativeEvent.isComposing
                  ) {
                    e.preventDefault();
                    void handleSend();
                  }
                }}
                maxLength={MAX_QUESTION_CHARS}
                aria-describedby={charCountId}
                rows={1}
                placeholder={
                  isListening ? t.chat.placeholderListening : t.chat.placeholder
                }
                className="max-h-28 min-h-10 w-full resize-none rounded-xl border border-navy-200 bg-navy-50/40 px-3 py-2 text-sm text-navy-900 outline-none focus:border-teal-400 focus:bg-white focus:ring-2 focus:ring-teal-200"
              />
              <span
                id={charCountId}
                className={`mt-1 self-end font-mono text-[10px] tabular-nums ${
                  overLimit ? "text-coral-600" : "text-navy-400"
                }`}
              >
                {trimmed.length} / {MAX_QUESTION_CHARS}
              </span>
            </div>

            <button
              type="submit"
              disabled={!canSend}
              aria-label={t.chat.send}
              aria-busy={asking}
              className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-linear-to-br from-teal-500 to-navy-700 text-white shadow-sm transition-all hover:brightness-110 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-300 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <SendHorizontal className="h-4 w-4" aria-hidden="true" />
            </button>
          </form>
        </section>
      ) : null}
    </>
  );
}

function AssistantBubble({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-end gap-2">
      <span
        aria-hidden="true"
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-teal-100 text-teal-700"
      >
        <Bot className="h-4 w-4" />
      </span>
      <div className="flex max-w-[85%] flex-col rounded-2xl rounded-bl-md bg-white px-3.5 py-2.5 text-sm whitespace-pre-line text-navy-800 shadow-sm">
        {children}
      </div>
    </div>
  );
}
