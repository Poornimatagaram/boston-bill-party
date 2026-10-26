# Boston Bill Party

**No overcharging without representation.**

A multi-agent AI patient advocate that audits medical bills and drafts appeal letters for Massachusetts residents. Built at Hackachusetts 2026.

> **Synthetic data only.** This project works exclusively with synthetic test bills. Never upload real patient data or protected health information.

## The problem

Medical bills are hard to read and often wrong: duplicate charges, totals that don't add up, missing or unrecognized billing codes, and charges that conflict with patient protections like surprise-billing rules. In Massachusetts, where health care is among the most expensive in the country, these errors land on people already struggling with affordability. Disputing them takes time, billing knowledge, and confidence in English, which many patients, especially seniors and non-English speakers, don't have.

Boston Bill Party reads the bill, finds the likely errors, explains them in plain language, and drafts the appeal for you, while you stay in control of what gets sent.

## Features

- **5-agent pipeline:** Extractor → Auditor → Rights Checker → Skeptic → Writer, with live per-agent progress over Server-Sent Events.
- **Deterministic Decimal math:** the sum check, per-line math, and tolerances use exact `Decimal` arithmetic. The LLM never does billing math.
- **BM25 RAG over MA patient-rights docs:** the Rights Checker retrieves from a curated Massachusetts patient-rights corpus (surprise billing, itemized bills, duplicate charges, financial assistance, appeals, and more), keeps passages scoring at least 0.7 relevance, and cites the top 3 by readable title.
- **The Skeptic filter:** a finding reaches you only if its confidence is at least 0.5 and it points to evidence (a line item or a rights passage).
- **Voice chatbot:** a floating assistant on the results page answers questions about your bill. Type or speak (Web Speech API, in the session language), and play any answer aloud.
- **4-language UI:** English, Español, Português, and Kreyòl Ayisyen. The choice is remembered and preselects the summary language.
- **English appeal letter with translation:** the letter is always written in English for billing offices and insurers. Non-English users can open a read-only translation to understand it.
- **Export:** after you confirm the letter, print it (only the letter prints), open it in Gmail or Outlook, copy it, or download a `.txt`.
- **Accessible results:** Overview / Issues / Appeal Letter tabs, color-coded issue cards with confidence bars, and read-aloud on every summary item, issue, and the letter.

## Architecture

```
PDF ──► Extractor ──► Auditor ──► Rights Checker ──► Skeptic ──► Writer ──► Summary + Appeal letter
        pdfplumber    Decimal      BM25 over MA       confidence   summary in session
        + LLM →       sum check,   patient-rights     ≥ 0.5 and    language; letter
        ExtractedBill duplicates,  corpus (≥ 0.7,     evidence     always in English
                      bad codes    top 3)
```

| Layer | Stack |
| --- | --- |
| Backend | Python 3.11+ (deployed on 3.14), FastAPI, Pydantic v2, pdfplumber, rank-bm25 |
| Pipeline | Compiled LangGraph `StateGraph` (`backend/pipeline/graph.py`) |
| LLM | Pluggable `LLMProvider` (Anthropic by default), configured in `backend/.env` |
| Frontend | Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS v4, lucide-react |
| Tests | pytest + Hypothesis property-based tests |

The LLM is used only where language judgment is needed: structuring PDF text into a schema, phrasing the summary and letter, translating the letter, and answering chat questions.

## Safety

- **No LLM math:** totals and tolerances are computed with exact decimals and are covered by property-based tests.
- **A human always confirms:** nothing is sent until you press Confirm on the editable letter, and every export is something you start.
- **Synthetic data only:** the backend rejects any upload not marked synthetic.
- **Evidence or it's dropped:** the Skeptic removes low-confidence or unsupported findings.
- **The letter you send is the English one:** the translation is labeled read-only and "Send the English version. The translation is only to help you understand it."
- **Secrets stay server-side:** the LLM API key lives only in `backend/.env` (git-ignored). It is never logged or returned, and the frontend never sees it.

## Evaluation

On a 10-bill synthetic evaluation set with 26 planted billing errors (math errors, duplicate charges, suspicious codes), the pipeline detects **24 of 26**.

- **2 missed:** both are bill-level total problems (a total mismatch and a missing total). Those findings point to no specific line item, so the Skeptic's evidence rule drops them. Tuning that rule is on the list below.
- **17 false positives:** all are rights-issue findings. The eval set plants only billing errors, so any rights citation counts as unmatched.

The harness is hermetic: it uses a deterministic stub instead of the real LLM, so it costs no API credits. Run it from `backend/` with:

```bash
.venv/bin/python -c "from eval.harness import run_evaluation; print(run_evaluation())"
```

or `POST /eval/run` on a running backend.

## How Kiro was used

- **Spec-driven development:** the app was built from a Kiro spec in `.kiro/specs/boston-bill-party/`: `requirements.md` (EARS-style requirements), `design.md` (architecture plus 16 correctness properties), and `tasks.md` (an MVP-first implementation plan).
- **Subagent task execution:** spec tasks were delegated to Kiro subagents and executed one at a time against the plan, with requirement traceability in the code and tests.
- **Property-based tests:** the trust-critical core is checked with Hypothesis: Sum_Check exactness and determinism, duplicate-charge grouping, and the Skeptic's retain-iff-confident-and-evidenced rule.
- **Steering files:** `.kiro/steering/product.md`, `tech.md`, and `structure.md` give every Kiro session the mission, safety rules, conventions, and folder layout.
- **Agent hooks:** `.kiro/hooks/pytest-on-pipeline-save.json` runs the backend test suite whenever a file in `backend/pipeline/` is saved.

## Run it locally

You need Python 3.11+ and Node.js 20+. Use two terminals.

**1. Backend (port 8000)**

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `backend/.env` with your own values:

```
LLM_PROVIDER=anthropic
LLM_API_KEY=<your-api-key>
LLM_MODEL=<model-name>
```

`LLM_MODEL_WRITER` and the other `LLM_MODEL_<NODE>` overrides are optional. Then start the server:

```bash
uvicorn main:app --reload --port 8000
```

**2. Frontend (port 3000)**

```bash
cd frontend
cp .env.example .env.local   # sets NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm install
npm run dev
```

Open <http://localhost:3000> and press **Try a sample bill**, or upload a bill from `backend/eval/eval_set/` (all synthetic).

**Tests**

```bash
cd backend && .venv/bin/python -m pytest -q
cd frontend && npx tsc --noEmit && npm run lint && npm run build
```

## Deploy

The backend runs on Render (`render.yaml` at the repo root, Python 3.14.3). The frontend is a standard Next.js app; the steps below use Vercel, but any Next.js host works.

**1. Backend on Render**

1. Push the repo to GitHub.
2. In the Render Dashboard, choose **New → Blueprint** and select the repo. Render reads `render.yaml` and creates the `boston-bill-party-api` web service (root `backend/`, `pip install -r requirements.txt`, `uvicorn main:app --host 0.0.0.0 --port $PORT`).
3. When prompted, set the values marked `sync: false`:
   - `LLM_API_KEY`: your provider key (stored only in Render, never in the repo).
   - `LLM_MODEL`: the model name.
   - `ALLOWED_ORIGINS`: your frontend URL, e.g. `https://your-app.vercel.app`. Separate several with commas. If unset, only `http://localhost:3000` is allowed.
4. Deploy, then note the service URL (e.g. `https://boston-bill-party-api.onrender.com`).

**2. Frontend**

1. Import the repo into Vercel with **Root Directory** `frontend`.
2. Set `NEXT_PUBLIC_API_BASE_URL` to the Render service URL and deploy.
3. If the frontend URL changed, update `ALLOWED_ORIGINS` on Render.

**Cost protection (demo limits)**

The public demo limits LLM spend. When a limit is hit, the API returns `429` and the UI shows a "demo limit reached" message.

| Limit | Default | Configure |
| --- | --- | --- |
| Bill analyses per IP | 5 per hour | `rate_limit.py` |
| Chat questions per IP | 20 per hour | `rate_limit.py` |
| Bill analyses, all visitors | 50 per UTC day | `MAX_DAILY_ANALYSES` env var |

Counters live in memory, like sessions: they reset when the service restarts and assume a single instance. Client IPs come from `X-Forwarded-For`, which a determined client can spoof, so the daily cap is the hard backstop.

## What's next

- Let bill-level total errors through the Skeptic without weakening its evidence rule.
- Add rights-issue cases to the eval set so rights findings are scored, not just counted as false positives.
- Persist sessions (currently in memory) and add authentication before any non-demo use.
- Expand the patient-rights corpus and the recognized billing-code set.
- Native-speaker review of the Spanish, Portuguese, and Haitian Creole UI text.
- Track appeal outcomes so users know what to do after sending.

## License

See [LICENSE](LICENSE).
