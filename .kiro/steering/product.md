# Product: Boston Bill Party

"No overcharging without representation."

## Mission

Help Massachusetts patients catch medical-billing errors and dispute them. A user uploads a bill PDF; a five-agent pipeline audits it, checks it against Massachusetts patient-rights rules, filters out weak findings, and drafts a plain-language summary plus an appeal letter the user reviews before sending.

## Target users

- Massachusetts residents disputing hospital or insurer bills.
- Seniors and anyone who finds medical bills hard to read: plain language, large tap targets, read-aloud, and a chat assistant they can talk to.
- Non-English speakers: the UI and summaries support English, Spanish, Portuguese, and Haitian Creole.

## Safety rules (non-negotiable)

- **The LLM never does billing math.** Totals, per-line math, and tolerances use exact `Decimal` arithmetic in `pipeline/sumcheck.py` and `pipeline/auditor.py`. The LLM only structures PDF text, phrases prose, translates, and answers questions.
- **A human always confirms.** Nothing is sent until the user presses Confirm on the (editable) appeal letter. Export actions (Print, Gmail, Outlook, Copy, Download) appear only after confirmation and are user-initiated.
- **Synthetic data only.** The backend rejects uploads not marked synthetic. Never add real patient data, PHI, or real bills to the repo, tests, or prompts.
- **The appeal letter is always in English.** It goes to MA billing offices and insurers. Non-English users get a read-only translation ("Send the English version. The translation is only to help you understand it."). Summaries follow the session language.
- **Findings need evidence.** The Skeptic drops any finding with confidence < 0.5 or no line-item/passage reference. Don't bypass it.
- **Secrets stay server-side.** The LLM API key lives only in `backend/.env`; never log, print, return, or commit it. The frontend never sees it.

## Product voice

Plain, calm, supportive. Explain what was found and what to do next; avoid legal or medical advice.
