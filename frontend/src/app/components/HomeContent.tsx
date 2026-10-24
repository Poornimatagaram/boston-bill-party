"use client";

/**
 * Home page content: hero, "How it works" 3D cards, stats strip, the upload
 * card (`#upload`), and an About section. Client component so all static text
 * follows the UI language selector.
 */

import {
  ArrowRight,
  Bot,
  FileCheck2,
  FileUp,
  HeartPulse,
  Languages,
  Scale,
  ShieldCheck,
  Sparkles,
  Target,
  type LucideIcon,
} from "lucide-react";
import { useT } from "@/i18n/uiLanguage";
import HeroVisual from "./HeroVisual";
import UploadCard from "./UploadCard";

/**
 * Per-step visuals. Each icon tile sets a solid background color as well as
 * the gradient so the tile stays clearly colored even if a browser drops the
 * gradient inside the tilted 3D card.
 */
const STEP_VISUALS: ReadonlyArray<{ icon: LucideIcon; tilt: string; tile: string }> = [
  {
    icon: FileUp,
    tilt: "tilt-left",
    tile: "bg-navy-700 bg-linear-to-br from-navy-600 to-navy-800",
  },
  {
    icon: Bot,
    tilt: "tilt-center",
    tile: "bg-teal-600 bg-linear-to-br from-teal-400 to-teal-700",
  },
  {
    icon: FileCheck2,
    tilt: "tilt-right",
    tile: "bg-coral-500 bg-linear-to-br from-coral-500 to-coral-700",
  },
];

const UPLOAD_BULLET_ICONS: ReadonlyArray<LucideIcon> = [Bot, Scale, Languages];

export default function HomeContent({ hasHeroImage }: { hasHeroImage: boolean }) {
  const t = useT();

  const stats: ReadonlyArray<{ value: string; label: string; icon: LucideIcon }> = [
    { value: "5", label: t.stats.agents, icon: Bot },
    { value: "4", label: t.stats.languages, icon: Languages },
    { value: "92%", label: t.stats.detection, icon: Target },
  ];

  return (
    <main className="flex flex-1 flex-col">
      {/* ------------------------------------------------------------ Hero */}
      <section className="relative overflow-hidden">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-linear-to-br from-teal-50 via-white to-coral-50"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -top-24 -right-24 h-96 w-96 rounded-full bg-teal-200/50 blur-3xl"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -bottom-32 -left-24 h-96 w-96 rounded-full bg-coral-200/50 blur-3xl"
        />

        <div className="relative mx-auto grid max-w-6xl items-center gap-10 px-4 py-14 sm:px-6 sm:py-20 lg:grid-cols-2 lg:gap-14">
          <div className="flex flex-col gap-6">
            <span className="inline-flex w-fit items-center gap-2 rounded-full border border-teal-200 bg-white/80 px-3 py-1 text-xs font-semibold text-teal-700 shadow-sm">
              <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
              {t.hero.badge}
            </span>
            <h1 className="text-4xl leading-[1.05] font-extrabold tracking-tight text-navy-900 sm:text-5xl lg:text-6xl">
              {t.hero.titleStart}{" "}
              {/* Darker gradient end (coral-700) keeps the last word readable
                  on the light hero background. */}
              <span className="bg-linear-to-r from-teal-600 via-teal-700 to-coral-700 bg-clip-text text-transparent">
                {t.hero.titleHighlight}
              </span>
              {t.hero.titleEnd}
            </h1>
            <p className="max-w-xl text-base leading-relaxed text-navy-600 sm:text-lg">
              {t.hero.subtitle}
            </p>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
              <a
                href="#upload"
                className="inline-flex items-center justify-center gap-2 rounded-xl bg-linear-to-r from-navy-700 via-navy-600 to-teal-600 px-6 py-3.5 text-base font-semibold text-white shadow-card-lg transition-all hover:brightness-110 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-300 focus-visible:ring-offset-2"
              >
                {t.hero.cta}
                <ArrowRight className="h-5 w-5" aria-hidden="true" />
              </a>
              <a
                href="#how-it-works"
                className="inline-flex items-center justify-center gap-2 rounded-xl px-5 py-3.5 text-sm font-semibold text-navy-700 transition-colors hover:bg-white/80 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-300"
              >
                {t.hero.secondary}
              </a>
            </div>
            <p className="flex items-center gap-2 text-xs text-navy-500">
              <ShieldCheck className="h-4 w-4 text-teal-600" aria-hidden="true" />
              {t.hero.synthetic}
            </p>
          </div>

          <HeroVisual hasImage={hasHeroImage} />
        </div>
      </section>

      {/* --------------------------------------------------- How it works */}
      <section
        id="how-it-works"
        className="scroll-mt-20 bg-white py-16 sm:py-24"
        aria-labelledby="how-it-works-title"
      >
        <div className="mx-auto max-w-6xl px-4 sm:px-6">
          <div className="mx-auto flex max-w-2xl flex-col items-center gap-3 text-center">
            <span className="text-xs font-bold tracking-widest text-coral-600 uppercase">
              {t.howItWorks.eyebrow}
            </span>
            <h2
              id="how-it-works-title"
              className="text-3xl font-bold tracking-tight text-navy-900 sm:text-4xl"
            >
              {t.howItWorks.title}
            </h2>
          </div>

          <ol className="tilt-stage mt-14 grid gap-8 md:grid-cols-3 md:gap-6">
            {t.howItWorks.steps.map((step, i) => {
              const visual = STEP_VISUALS[i];
              const Icon = visual.icon;
              return (
                <li
                  key={i}
                  className={`tilt-card ${visual.tilt} relative flex flex-col gap-4 rounded-3xl border border-navy-100 bg-linear-to-b from-white to-navy-50/60 p-7 shadow-card`}
                >
                  <div className="flex items-center justify-between">
                    <span
                      aria-hidden="true"
                      className={`flex h-14 w-14 items-center justify-center rounded-2xl text-white shadow-card ring-4 ring-white ${visual.tile}`}
                    >
                      <Icon className="h-7 w-7" />
                    </span>
                    <span className="text-5xl font-black text-navy-100">
                      {i + 1}
                    </span>
                  </div>
                  <h3 className="text-xl font-bold text-navy-900">{step.title}</h3>
                  <p className="text-sm leading-relaxed text-navy-600">
                    {step.body}
                  </p>
                </li>
              );
            })}
          </ol>
        </div>
      </section>

      {/* ------------------------------------------------------ Stats strip */}
      <section
        aria-label={t.stats.aria}
        className="bg-linear-to-r from-navy-800 via-teal-700 to-coral-500"
      >
        <ul className="mx-auto grid max-w-6xl gap-6 px-4 py-10 sm:grid-cols-3 sm:px-6">
          {stats.map((stat) => {
            const Icon = stat.icon;
            return (
              <li
                key={stat.value}
                className="flex items-center justify-center gap-4 text-white"
              >
                <span
                  aria-hidden="true"
                  className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white/15 ring-1 ring-white/25"
                >
                  <Icon className="h-6 w-6" />
                </span>
                <span className="flex flex-col">
                  <span className="text-3xl font-extrabold tracking-tight">
                    {stat.value}
                  </span>
                  <span className="text-sm font-medium text-white/85">
                    {stat.label}
                  </span>
                </span>
              </li>
            );
          })}
        </ul>
      </section>

      {/* ----------------------------------------------------------- Upload */}
      <section
        id="upload"
        className="relative scroll-mt-20 overflow-hidden py-16 sm:py-24"
        aria-labelledby="upload-title"
      >
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-linear-to-b from-navy-50 via-teal-50/50 to-white"
        />
        <div className="relative mx-auto grid max-w-6xl items-start gap-10 px-4 sm:px-6 lg:grid-cols-[1fr_1.1fr]">
          <div className="flex flex-col gap-4 lg:pt-6">
            <span className="text-xs font-bold tracking-widest text-coral-600 uppercase">
              {t.uploadSection.eyebrow}
            </span>
            <h2
              id="upload-title"
              className="text-3xl font-bold tracking-tight text-navy-900 sm:text-4xl"
            >
              {t.uploadSection.title}
            </h2>
            <p className="text-base leading-relaxed text-navy-600">
              {t.uploadSection.body}
            </p>
            <ul className="flex flex-col gap-3 text-sm text-navy-700">
              {t.uploadSection.bullets.map((text, i) => {
                const Icon = UPLOAD_BULLET_ICONS[i];
                return (
                  <li key={i} className="flex items-center gap-3">
                    <span
                      aria-hidden="true"
                      className="flex h-8 w-8 items-center justify-center rounded-lg bg-teal-100 text-teal-700"
                    >
                      <Icon className="h-4 w-4" />
                    </span>
                    {text}
                  </li>
                );
              })}
            </ul>
          </div>
          <UploadCard />
        </div>
      </section>

      {/* ------------------------------------------------------------ About */}
      <section
        id="about"
        className="scroll-mt-20 bg-white py-16 sm:py-20"
        aria-labelledby="about-title"
      >
        <div className="mx-auto flex max-w-3xl flex-col items-center gap-4 px-4 text-center sm:px-6">
          <span
            aria-hidden="true"
            className="flex h-12 w-12 items-center justify-center rounded-2xl bg-linear-to-br from-navy-700 via-teal-500 to-coral-400 text-white shadow-card"
          >
            <HeartPulse className="h-6 w-6" />
          </span>
          <h2
            id="about-title"
            className="text-3xl font-bold tracking-tight text-navy-900"
          >
            {t.about.title}
          </h2>
          <p className="text-base leading-relaxed text-navy-600">{t.about.body}</p>
        </div>
      </section>
    </main>
  );
}
