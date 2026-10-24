import Link from "next/link";

/**
 * Branded app header: the "Boston Bill Party" wordmark, a small tea-crate mark,
 * and the tagline "No overcharging without representation". Navy band with a
 * teal accent; links back to the upload screen. Reused across all screens so
 * branding is consistent.
 */
export default function BrandHeader() {
  return (
    <header className="w-full border-b border-navy-800/40 bg-navy-900 text-white">
      <div className="mx-auto flex max-w-3xl items-center gap-3 px-5 py-4 sm:px-6">
        <Link href="/" className="flex items-center gap-3 no-underline">
          <span
            aria-hidden="true"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-teal-400 text-xl shadow-card"
          >
            🫖
          </span>
          <span className="flex flex-col leading-tight">
            <span className="text-lg font-semibold tracking-tight text-white sm:text-xl">
              Boston Bill Party
            </span>
            <span className="text-xs font-medium text-teal-300 sm:text-sm">
              No overcharging without representation
            </span>
          </span>
        </Link>
      </div>
    </header>
  );
}
