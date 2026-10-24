# Tech stack and conventions

## Backend (`backend/`)

- Python 3.11+ (verified on 3.14), FastAPI, Pydantic v2, pdfplumber, rank-bm25, Hypothesis, pytest. Dependencies are pinned in `requirements.txt` / `pyproject.toml`; pin any new one exactly.
- LLM access goes through the `LLMProvider` protocol (`llm/provider.py`). The Anthropic provider uses stdlib `urllib` (no SDK). Config comes from `backend/.env`: `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, optional `LLM_MODEL_<NODE>` overrides.
- The pipeline is an explicit sequential runner (`pipeline/graph.py`) with LangGraph-compatible `(state) -> state` node contracts. Sessions are in-memory only.
- HTTP: `POST /sessions`, `GET /sessions/{id}/result`, `GET /sessions/{id}/events` (SSE), `POST /sessions/{id}/confirm`, `POST /sessions/{id}/ask`, `POST /sessions/{id}/translate-letter`, `POST /eval/run`.

### Conventions

- Money is `Decimal`, serialized as two-decimal strings. Never use floats for amounts.
- Pydantic models use `extra="forbid"`.
- Keep the Sum_Check, Auditor, and Skeptic pure and deterministic.
- Read the provider via `sessions.get_provider()` (module attribute) so tests can monkeypatch it. Tests never call a real LLM.
- Errors returned to clients are generic; never leak provider internals or the API key.
- Tests live next to the code as `*_test.py`. Use Hypothesis property tests for pure logic and stub providers for pipeline/endpoint tests.
- Run tests: `cd backend && .venv/bin/python -m pytest -q`.

## Frontend (`frontend/`)

- Next.js 16 (App Router, Turbopack, `cacheComponents`), React 19, TypeScript, Tailwind CSS v4, lucide-react icons (no emojis).
- This Next.js version has breaking changes: read the relevant guide in `frontend/node_modules/next/dist/docs/` before using an API (see `frontend/AGENTS.md`). Example: `next/image` uses `preload`/`loading="eager"`, not `priority`.
- The backend URL comes from `NEXT_PUBLIC_API_BASE_URL` (`frontend/.env.local`, copied from `.env.example`). The API client is `src/lib/api.ts`.

### Conventions

- All static UI text goes in `src/i18n/dictionary.ts` (English is the source type; every language must have every key). Read it with `useT()`; the UI language comes from `useUiLanguage()`. Store client-side error states as keys/flags and resolve text at render.
- Design tokens (navy/teal/coral, `shadow-card`) are in `src/app/globals.css`. The app is light-theme only.
- React hooks lint is strict: no `setState` inside effects; read browser-only state with `useSyncExternalStore`.
- Accessibility: label every control, use `aria-live` for status, keep keyboard support (tabs use arrow keys), mark decorative icons `aria-hidden`.
- Before finishing frontend work: `npx tsc --noEmit && npm run lint && npm run build`.
