"use client";

/**
 * Sticky site header: gradient logo tile, "Boston Bill Party" name, the
 * tagline, the main nav (Home / How it works / About), and the UI language
 * selector. On small screens the nav and selector collapse behind a menu
 * button. Also keeps <html lang> in sync with the UI language.
 */

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { HeartPulse, Languages, Menu, X } from "lucide-react";
import { SUPPORTED_LANGUAGES, type SupportedLanguage } from "@/lib/api";
import { setUiLanguage, useT, useUiLanguage } from "@/i18n/uiLanguage";

function LanguageSelect({ className = "" }: { className?: string }) {
  const id = useId();
  const t = useT();
  const language = useUiLanguage();

  return (
    <div className={`flex items-center gap-1.5 ${className}`}>
      <label htmlFor={id} className="flex items-center text-navy-500">
        <Languages className="h-4 w-4" aria-hidden="true" />
        <span className="sr-only">{t.header.language}</span>
      </label>
      <select
        id={id}
        value={language}
        onChange={(e) => setUiLanguage(e.target.value as SupportedLanguage)}
        className="rounded-lg border border-navy-200 bg-white py-1.5 pr-7 pl-2 text-sm font-medium text-navy-800 outline-none hover:border-teal-300 focus:border-teal-400 focus:ring-2 focus:ring-teal-200"
      >
        {SUPPORTED_LANGUAGES.map(({ value, label }) => (
          <option key={value} value={value} lang={value}>
            {label}
          </option>
        ))}
      </select>
    </div>
  );
}

export default function SiteHeader() {
  const [menuOpen, setMenuOpen] = useState(false);
  const t = useT();
  const language = useUiLanguage();

  // Keep the document language in sync for screen readers and hyphenation.
  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  const navLinks = [
    { href: "/", label: t.header.home },
    { href: "/#how-it-works", label: t.header.howItWorks },
    { href: "/#about", label: t.header.about },
  ];

  return (
    <header className="sticky top-0 z-40 border-b border-navy-100/80 bg-white/85 backdrop-blur-md">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
        <Link
          href="/"
          className="flex min-w-0 items-center gap-3 rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
          onClick={() => setMenuOpen(false)}
        >
          <span
            aria-hidden="true"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-linear-to-br from-navy-700 via-teal-500 to-coral-400 text-white shadow-card"
          >
            <HeartPulse className="h-5 w-5" />
          </span>
          <span className="flex min-w-0 flex-col leading-tight">
            <span className="text-base font-bold tracking-tight text-navy-900 sm:text-lg">
              Boston Bill Party
            </span>
            <span className="truncate text-[11px] font-medium text-teal-700 sm:text-xs">
              {t.header.tagline}
            </span>
          </span>
        </Link>

        <div className="hidden items-center gap-4 md:flex">
          <nav aria-label={t.header.mainNav}>
            <ul className="flex items-center gap-1">
              {navLinks.map((link) => (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    className="rounded-lg px-3 py-2 text-sm font-medium text-navy-700 transition-colors hover:bg-teal-50 hover:text-teal-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
                  >
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
          <LanguageSelect />
        </div>

        <button
          type="button"
          className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-navy-700 hover:bg-navy-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 md:hidden"
          aria-expanded={menuOpen}
          aria-controls="mobile-nav"
          aria-label={menuOpen ? t.header.closeMenu : t.header.openMenu}
          onClick={() => setMenuOpen((open) => !open)}
        >
          {menuOpen ? (
            <X className="h-5 w-5" aria-hidden="true" />
          ) : (
            <Menu className="h-5 w-5" aria-hidden="true" />
          )}
        </button>
      </div>

      {menuOpen ? (
        <div id="mobile-nav" className="border-t border-navy-100 bg-white md:hidden">
          <nav aria-label={t.header.mainNav}>
            <ul className="mx-auto flex max-w-6xl flex-col px-4 py-2">
              {navLinks.map((link) => (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    onClick={() => setMenuOpen(false)}
                    className="block rounded-lg px-3 py-3 text-sm font-medium text-navy-700 hover:bg-teal-50 hover:text-teal-700"
                  >
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
          <div className="mx-auto max-w-6xl border-t border-navy-100 px-7 py-3">
            <LanguageSelect />
          </div>
        </div>
      ) : null}
    </header>
  );
}
