"use client";

/**
 * UI language store (no i18n library).
 *
 * The chosen language lives in localStorage and is read through
 * useSyncExternalStore, so the server render (and hydration) uses English and
 * the client switches to the saved choice right after, without a hydration
 * mismatch. Every component reading it updates together, and other tabs follow
 * via the `storage` event. If localStorage is unavailable (e.g. blocked), the
 * choice is kept in memory for the current page.
 */

import { useSyncExternalStore } from "react";
import {
  DEFAULT_LANGUAGE,
  SUPPORTED_LANGUAGES,
  type SupportedLanguage,
} from "@/lib/api";
import { dictionaries, type Dictionary } from "./dictionary";

const STORAGE_KEY = "bbp.uiLanguage";

const listeners = new Set<() => void>();
let memoryValue: SupportedLanguage | null = null;

function isSupported(value: unknown): value is SupportedLanguage {
  return SUPPORTED_LANGUAGES.some((l) => l.value === value);
}

function getSnapshot(): SupportedLanguage {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (isSupported(stored)) return stored;
  } catch {
    // Storage unavailable: fall through to the in-memory value.
  }
  return memoryValue ?? DEFAULT_LANGUAGE;
}

function getServerSnapshot(): SupportedLanguage {
  return DEFAULT_LANGUAGE;
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  const onStorage = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY) onChange();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onStorage);
  };
}

/** Save the UI language and notify every subscriber. */
export function setUiLanguage(language: SupportedLanguage): void {
  memoryValue = language;
  try {
    window.localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // Storage unavailable: the in-memory value still applies.
  }
  listeners.forEach((listener) => listener());
}

/** The current UI language (English during SSR/hydration). */
export function useUiLanguage(): SupportedLanguage {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

/** The dictionary for the current UI language. */
export function useT(): Dictionary {
  return dictionaries[useUiLanguage()];
}
