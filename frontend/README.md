# Boston Bill Party — Frontend

Next.js (App Router) + TypeScript + Tailwind frontend for the Boston Bill Party
medical-bill review tool.

## Screens / routes

| Route                    | Screen   | Status |
|--------------------------|----------|--------|
| `/`                      | Upload   | scaffold (task 9.2 wires it up) |
| `/progress/[sessionId]`  | Progress | scaffold (tasks 10.1 / 14.2) |
| `/results/[sessionId]`   | Results  | scaffold (tasks 10.2 / 15.2) |

## API client

`src/lib/api.ts` is the typed fetch client for the backend. It exposes:

- `createSession(file, language)` → `POST /sessions`
- `subscribeToEvents(sessionId, handlers)` → `GET /sessions/{id}/events` (SSE)
- `getResult(sessionId)` → `GET /sessions/{id}/result`
- `confirmLetter(sessionId, text)` → `POST /sessions/{id}/confirm`
- `ask(sessionId, question)` → `POST /sessions/{id}/ask`

Types mirror `backend/models.py`. The frontend never handles the backend LLM
API key.

## Configuration

Copy `.env.example` to `.env.local` and set the backend base URL:

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

It defaults to `http://localhost:8000` when unset.

## Development

```bash
npm install
npm run dev     # http://localhost:3000
npm run build   # production build + type-check
npm run lint
```
