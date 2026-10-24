"use client";

/**
 * Post-confirm actions for the appeal letter: Print, Open in Gmail, Open in
 * Outlook, Copy, and Download .txt.
 *
 * - Print uses window.print(). A print-only copy of the letter is portaled into
 *   <body> as `.print-letter`; the print stylesheet in globals.css hides every
 *   other top-level element, so only the letter is printed.
 * - Gmail/Outlook open a web compose window in a new tab with the subject and
 *   body URL-encoded (no recipient). Doing so hands the letter text to that
 *   provider, which is the user's explicit choice.
 * - Copy/Download act locally.
 *
 * Only rendered after the user confirms, which happens client-side, so the
 * portal never runs during server rendering.
 */

import { useState } from "react";
import { createPortal } from "react-dom";
import { Check, Copy, Download, Mail, Printer } from "lucide-react";
import { useT } from "@/i18n/uiLanguage";

// The letter is always English, so its email subject is too (not translated).
const LETTER_SUBJECT = "Appeal regarding my medical bill";

const actionClass =
  "inline-flex items-center justify-center gap-2 rounded-xl border border-navy-200 bg-white px-3.5 py-2.5 text-sm font-semibold text-navy-800 shadow-sm transition-colors hover:border-teal-300 hover:bg-teal-50 hover:text-teal-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400";

export default function LetterActions({ letter }: { letter: string }) {
  const t = useT();
  const [copied, setCopied] = useState(false);

  const subject = encodeURIComponent(LETTER_SUBJECT);
  const body = encodeURIComponent(letter);
  const gmailHref = `https://mail.google.com/mail/?view=cm&fs=1&su=${subject}&body=${body}`;
  const outlookHref = `https://outlook.office.com/mail/deeplink/compose?subject=${subject}&body=${body}`;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(letter);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard may be unavailable (e.g. insecure context); ignore silently.
    }
  };

  const handleDownload = () => {
    const blob = new Blob([letter], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "appeal-letter.txt";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-teal-200 bg-linear-to-br from-teal-50 to-white p-4">
      <p className="text-sm font-semibold text-navy-900">{t.actions.sendTitle}</p>
      <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap">
        <button type="button" onClick={() => window.print()} className={actionClass}>
          <Printer className="h-4 w-4" aria-hidden="true" />
          {t.actions.print}
        </button>
        <a
          href={gmailHref}
          target="_blank"
          rel="noopener noreferrer"
          className={actionClass}
        >
          <Mail className="h-4 w-4 text-coral-500" aria-hidden="true" />
          {t.actions.gmail}
          <span className="sr-only">{t.common.newTab}</span>
        </a>
        <a
          href={outlookHref}
          target="_blank"
          rel="noopener noreferrer"
          className={actionClass}
        >
          <Mail className="h-4 w-4 text-navy-500" aria-hidden="true" />
          {t.actions.outlook}
          <span className="sr-only">{t.common.newTab}</span>
        </a>
        <button type="button" onClick={handleCopy} className={actionClass}>
          {copied ? (
            <Check className="h-4 w-4 text-teal-600" aria-hidden="true" />
          ) : (
            <Copy className="h-4 w-4" aria-hidden="true" />
          )}
          {copied ? t.actions.copied : t.actions.copy}
        </button>
        <button
          type="button"
          onClick={handleDownload}
          className={`${actionClass} col-span-2 sm:col-span-1`}
        >
          <Download className="h-4 w-4" aria-hidden="true" />
          {t.actions.download}
        </button>
      </div>
      <p className="text-sm text-teal-800">
        {t.actions.note}
      </p>
      <span aria-live="polite" className="sr-only">
        {copied ? t.actions.copiedSr : ""}
      </span>

      {/* Print-only copy of the letter (see the print stylesheet). */}
      {typeof document !== "undefined"
        ? createPortal(<div className="print-letter">{letter}</div>, document.body)
        : null}
    </div>
  );
}
