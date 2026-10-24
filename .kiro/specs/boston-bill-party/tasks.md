# Implementation Plan: Boston Bill Party

## Overview

This plan is sequenced **MVP-first**: the earliest tasks build a complete, demonstrable slice — project scaffolding, data models, LLM provider configuration, the Extractor, the deterministic Sum_Check, the Auditor, the Writer, a minimal backend API, and the three-screen Next.js UI (upload → progress → results). A clearly marked **MVP Milestone** (task 11) marks the point where the demo runs end to end with the pipeline wired as Extractor → Auditor → Writer directly.

After the milestone, the remaining stages are added as additive pipeline nodes and features, in order: Rights_Checker (BM25 RAG with `rank-bm25`), Skeptic, SSE live-progress polish, voice Q&A, and finally the ten-bill synthetic Eval_Set plus the evaluation harness.

Implementation language: **Python** (FastAPI backend) and **TypeScript** (Next.js + Tailwind frontend), as specified in the design.

Property-based tests use **Hypothesis**. Per the delivery directive, most property tests are optional; the three that guard the trust-critical core — Sum_Check (Properties 1 & 2), duplicate detection (Property 6), and the Skeptic threshold (Property 10) — are **required** and are not marked optional.

## Tasks

- [x] 1. Set up backend project structure and core data models
  - Create the `backend/` package layout (`main.py`, `config.py`, `sessions.py`, `pipeline/`, `rag/`, `llm/`, `eval/`, `models.py`) and a `pyproject.toml`/`requirements.txt` pinning FastAPI, pdfplumber, pydantic v2, rank-bm25, and hypothesis
  - Implement the Pydantic v2 models in `models.py` with `extra="forbid"`: `SupportedLanguage`, `LineItem`, `ExtractedBill`, `FindingType`, `Finding`, `SumCheckResult`, `RightsStatus`, `Passage`, `AnalysisResult`
  - Use `Decimal` for all monetary fields serialized as two-decimal strings; constrain `confidence` and `relevance_score` to [0,1]
  - _Requirements: 2.3, 2.4, 2.5, 4.6, 5.3_

- [ ] 2. Implement configurable LLM provider
  - [ ] 2.1 Implement `.env` loading and config validation in `config.py`
    - Load `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL` and optional per-node overrides (e.g. `LLM_MODEL_EXTRACTOR`) from `.env` at startup
    - Abort startup naming each missing/empty required variable (Req 11.2); abort naming an unsupported provider value and listing supported providers (Req 11.3)
    - Resolve each node's effective model/params as the per-node override when present, else the shared value (Req 11.5)
    - _Requirements: 11.1, 11.2, 11.3, 11.5_

  - [ ] 2.2 Implement the `LLMProvider` abstraction and factory in `llm/provider.py`
    - Define the `LLMProvider` protocol (`complete(system, user, timeout_s, node)`) and `build_provider(config)` factory routing all node calls to the configured provider without code changes (Req 11.4)
    - _Requirements: 11.4_

  - [ ]* 2.3 (Optional) Write property test for missing-config startup abort
    - **Property 13: Missing required config aborts startup naming exactly the missing variables**
    - **Validates: Requirements 11.2**

  - [ ]* 2.4 (Optional) Write property test for per-node config resolution
    - **Property 14: Per-node config resolution prefers overrides then shared values**
    - **Validates: Requirements 11.5**

  - [ ]* 2.5 (Optional) Write unit test for unsupported-provider startup error
    - Assert startup aborts and the error names the bad value and lists supported providers
    - _Requirements: 11.3_

- [ ] 3. Implement the Extractor node
  - [ ] 3.1 Implement PDF reading and LLM structuring in `pipeline/extractor.py`
    - Read all PDF pages in order with pdfplumber and concatenate text (Req 2.1)
    - If no extractable text, return a "no readable text" error and do not call the LLM (Req 2.7)
    - Call the LLM provider with a 60s timeout (Req 2.8); validate output against `ExtractedBill`, returning per-field errors with no partial object on failure (Req 2.2, 2.6)
    - Set missing optional line-item fields to null (Req 2.4); normalize amounts/unit prices to non-negative two-decimal `Decimal` and quantities to non-negative numeric (Req 2.5)
    - _Requirements: 2.1, 2.2, 2.4, 2.5, 2.6, 2.7, 2.8_

  - [ ]* 3.2 (Optional) Write unit tests for Extractor edge cases
    - Empty-text short-circuit asserting the LLM mock is never called (Req 2.7); page-order concatenation on a fixed multi-page PDF (Req 2.1); nonconforming-output validation error naming each bad field (Req 2.6)
    - _Requirements: 2.1, 2.6, 2.7_

- [ ] 4. Implement the deterministic Sum_Check
  - [ ] 4.1 Implement `compute_sum_check(bill) -> SumCheckResult` in `pipeline/sumcheck.py`
    - Add every present, parseable line-item amount using `Decimal`; empty/no-amount bills yield `0.00` (Req 3.1)
    - Exclude missing/unparseable amounts, recording their line-item ids in `excluded_line_item_ids`, without aborting (Req 3.5)
    - Use exact decimal arithmetic only, never the LLM, and produce identical results across repeated calls (Req 3.3, 3.4)
    - _Requirements: 3.1, 3.3, 3.4, 3.5_

  - [ ] 4.2 Write property test for Sum_Check exactness
    - **Property 1: Sum_Check equals the exact sum of parseable amounts**
    - **Validates: Requirements 3.1, 3.5**

  - [ ] 4.3 Write property test for Sum_Check determinism
    - **Property 2: Sum_Check is deterministic**
    - **Validates: Requirements 3.4**

- [ ] 5. Implement the Auditor node
  - [ ] 5.1 Implement billing-error detection in `pipeline/auditor.py`
    - Pure function over `ExtractedBill` + `SumCheckResult`
    - Math errors: bill-total mismatch > 0.01 carrying both totals (Req 3.2), absent stated total (Req 3.6), unparseable line amount (Req 3.5), and per-line `|amount - quantity*unit_price| > 0.01` (Req 4.3)
    - Duplicate charge: group lines sharing identical code **and** description into one Finding referencing all members (Req 4.2)
    - Suspicious code: code absent from the recognized set (Req 4.4) or missing entirely (Req 4.5)
    - Assign each Finding a confidence in [0,1] (Req 4.6); zero line items yields empty Findings (Req 4.7)
    - _Requirements: 3.2, 3.6, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

  - [ ] 5.2 Write property test for duplicate-charge grouping
    - **Property 6: Duplicate charges are grouped into one Finding referencing all members**
    - **Validates: Requirements 4.2**

  - [ ]* 5.3 (Optional) Write property test for bill-total mismatch Finding
    - **Property 3: Bill-total mismatch produces a math-error Finding carrying both totals**
    - **Validates: Requirements 3.2**

  - [ ]* 5.4 (Optional) Write property test for unparseable-amount exclusion and flagging
    - **Property 4: Unparseable amounts are excluded and flagged without aborting**
    - **Validates: Requirements 3.5**

  - [ ]* 5.5 (Optional) Write property test for per-line math errors
    - **Property 5: Per-line math errors are flagged within tolerance**
    - **Validates: Requirements 4.3**

  - [ ]* 5.6 (Optional) Write property test for suspicious/missing codes
    - **Property 7: Missing or unrecognized codes produce a suspicious-code Finding**
    - **Validates: Requirements 4.4, 4.5**

  - [ ]* 5.7 (Optional) Write property test for Finding confidence range
    - **Property 8: All Findings carry a confidence in [0, 1]**
    - **Validates: Requirements 4.6, 5.3**

- [ ] 6. Implement the Writer node (MVP: no-skeptic path)
  - [ ] 6.1 Implement Summary and Appeal_Letter generation in `pipeline/writer.py`
    - With ≥1 Finding, produce a Summary with one entry per Finding and an Appeal_Letter with one contesting statement per Finding, both in the session language (Req 7.1, 7.2, 7.3)
    - With zero Findings, produce a "no credible issues" Summary and no letter (Req 7.4)
    - On generation failure return an error with no partial output (Req 7.5)
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [ ]* 6.2 (Optional) Write property test for one-summary-entry-per-finding
    - **Property 11: Summary has one entry per retained Finding**
    - **Validates: Requirements 7.1**

  - [ ]* 6.3 (Optional) Write property test for language tagging
    - **Property 12: Output is tagged with the selected language**
    - **Validates: Requirements 7.3**

  - [ ]* 6.4 (Optional) Write unit tests for Writer branches
    - No-findings "none detected" summary with no letter (Req 7.4); generation-failure branch returns no partial output (Req 7.5)
    - _Requirements: 7.4, 7.5_

- [ ] 7. Assemble the MVP pipeline and session management
  - [ ] 7.1 Define `PipelineState` and the MVP LangGraph graph
    - Implement `pipeline/state.py` with the shared `PipelineState` (session id, language, raw_text, bill, sum_check, findings, retained_findings, rights_status, result, node_status, error)
    - Implement `pipeline/graph.py` wiring **Extractor → Auditor → Writer** directly for the MVP (Sum_Check invoked at the start of the Auditor); leave the graph shaped so Rights_Checker and Skeptic can be inserted later
    - _Requirements: 2.2, 3.1, 4.1, 7.1_

  - [ ] 7.2 Implement the session registry and background pipeline task in `sessions.py`
    - Create an in-memory `Session` keyed by UUID holding language, status, per-node progress, `PipelineState`, and an async event queue; launch the pipeline as a background async task on session creation
    - _Requirements: 1.1, 1.7_

- [ ] 8. Implement the minimal backend HTTP API
  - [ ] 8.1 Implement `POST /sessions` with upload validation in `main.py`
    - Validate in order: reject non-PDF content type with a PDF-format message (Req 1.2); reject size outside [1 byte, 10 MB] with a range message (Req 1.3); reject unparseable PDFs as corrupt/unreadable (Req 1.6); reject input not marked synthetic (Req 12.2); rejections retain no session
    - On acceptance, start a session and return the `sessionId` within 5s (Req 1.1, 1.7)
    - _Requirements: 1.1, 1.2, 1.3, 1.6, 1.7, 12.1, 12.2_

  - [ ] 8.2 Implement `GET /sessions/{id}/result`
    - Return the Summary, Appeal_Letter, and retained Findings; on node-fatal error return the error plus any Summary already produced to support the writer-retry path
    - _Requirements: 7.1, 8.1_

  - [ ]* 8.3 (Optional) Write unit tests for upload validation boundaries
    - Messages and boundaries at 0 bytes / 1 byte / 10 MB / 10 MB+1, non-PDF, corrupt PDF, and non-synthetic input (Req 1.2, 1.3, 1.6, 12.2)
    - _Requirements: 1.2, 1.3, 1.6, 12.2_

  - [ ]* 8.4 (Optional) Write integration test for fast session creation
    - Assert `POST /sessions` returns a `sessionId` within 5s (Req 1.7)
    - _Requirements: 1.7_

- [ ] 9. Build the Next.js frontend scaffolding and Upload screen
  - [ ] 9.1 Scaffold the Next.js (App Router) + Tailwind project with API client
    - Create the three-screen route structure and a typed fetch client for the backend endpoints
    - _Requirements: 1.1_

  - [ ] 9.2 Implement the Upload screen
    - File picker plus `Supported_Language` selector defaulting to English (Req 1.5), recording the selection for the session (Req 1.4), and POSTing the file to `/sessions`
    - _Requirements: 1.1, 1.4, 1.5_

  - [ ]* 9.3 (Optional) Write component test for the language selector
    - Records selection and defaults to English (Req 1.4, 1.5)
    - _Requirements: 1.4, 1.5_

- [ ] 10. Build the Progress and Results screens (MVP)
  - [ ] 10.1 Implement the Progress screen with basic polling/streaming
    - Display the active node from {Extractor, Auditor, Writer} and a `completed/5` count, updating within 2s; show a node-error state that retains prior completed nodes (Req 9.1, 9.3, 9.4)
    - _Requirements: 9.1, 9.3, 9.4_

  - [ ] 10.2 Implement the Results screen with the editable appeal letter
    - Render the Summary and the Appeal_Letter in an editable field accepting up to 20,000 characters within 2s (Req 8.1, 8.3); retain edits until confirm (Req 8.4); require an explicit confirm control before any send/export and transmit the current edited text on confirm (Req 8.5, 8.6, 8.7); show a retry action on writer failure without discarding the Summary (Req 8.2)
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

  - [ ] 10.3 Implement `POST /sessions/{id}/confirm` on the backend
    - Transmit the current (edited) letter text only after the explicit confirm action (Req 8.5, 8.6, 8.7)
    - _Requirements: 8.5, 8.6, 8.7_

  - [ ]* 10.4 (Optional) Write component tests for the Results screen
    - Editable field accepts up to 20,000 characters, retains edits, and the transmit call fires only after confirm carrying the current edited text (Req 8.1–8.7)
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

- [ ] 11. ★ MVP MILESTONE — end-to-end demo works
  - Wire the Upload → Progress → Results flow against the Extractor → Auditor → Writer pipeline and confirm a synthetic bill flows from upload to a rendered summary and editable appeal letter
  - Ensure all tests pass, ask the user if questions arise.
  - _Requirements: 1.1, 2.2, 3.1, 4.1, 7.1, 8.1, 9.1_

- [ ] 12. Add the Rights_Checker node (BM25 RAG)
  - [ ] 12.1 Implement the BM25 knowledge base in `rag/knowledge_base.py`
    - Load the MA patient-rights JSON corpus from `rag/corpus/`, tokenize, and score with `rank-bm25` (BM25 lexical ranking — no embedding model or vector store); add a small curated corpus of reference passages
    - _Requirements: 5.1_

  - [ ] 12.2 Implement `pipeline/rights_checker.py`
    - Build a query from the `ExtractedBill` and auditor Findings, retrieve up to 10 passages within 15s (Req 5.1), normalize each BM25 score to a 0–1 `Relevance_Score`
    - Record a rights-issue Finding citing a passage iff its `Relevance_Score ≥ 0.7`, with confidence in [0,1] (Req 5.2, 5.3); omit below-threshold issues (Req 5.4)
    - On failure/empty/timeout record no rights Findings, return "unavailable" status, and preserve auditor Findings (Req 5.5)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ] 12.3 Insert Rights_Checker into the pipeline between Auditor and Writer
    - Update `pipeline/graph.py` to run Extractor → Auditor → Rights_Checker → Writer, carrying rights Findings in shared state
    - _Requirements: 5.1_

  - [ ]* 12.4 (Optional) Write property test for the relevance threshold
    - **Property 9: Rights issues are recorded exactly at the relevance threshold**
    - **Validates: Requirements 5.2, 5.4**

  - [ ]* 12.5 (Optional) Write unit test for the rights-unavailable branch
    - Retrieval failure preserves auditor Findings and returns "unavailable" (Req 5.5)
    - _Requirements: 5.5_

  - [ ]* 12.6 (Optional) Write integration test for BM25 retrieval limits
    - At most 10 passages within 15s against the real BM25 index (Req 5.1)
    - _Requirements: 5.1_

- [ ] 13. Add the Skeptic node
  - [ ] 13.1 Implement confidence/evidence filtering in `pipeline/skeptic.py`
    - Pure filter: drop Findings with confidence < 0.5 (Req 6.2) or referencing zero line items **and** zero passages (Req 6.3); keep Findings with confidence ≥ 0.5 **and** at least one line-item or passage reference (Req 6.4); empty result passes an empty set (Req 6.5)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ] 13.2 Insert Skeptic into the pipeline between Rights_Checker and Writer
    - Update `pipeline/graph.py` to run the full Extractor → Auditor → Rights_Checker → Skeptic → Writer chain, with the Writer consuming `retained_findings`
    - _Requirements: 6.1, 7.1_

  - [ ] 13.3 Write property test for the Skeptic inclusion rule
    - **Property 10: Skeptic retains a Finding exactly when it is confident and evidenced**
    - **Validates: Requirements 6.2, 6.3, 6.4, 6.5**

- [ ] 14. Polish live progress with Server-Sent Events
  - [ ] 14.1 Implement `GET /sessions/{id}/events` SSE endpoint
    - Emit `progress` ({activeNode, completedNodes, completedCount, total:5}), `node_error` ({failedNode, message}), and `done` events; emit a node update on each node start/completion across all five nodes (Req 9.2)
    - _Requirements: 9.2_

  - [ ] 14.2 Upgrade the Progress screen to consume SSE
    - Subscribe to the SSE stream, update the active node and `completed/5` within 2s (Req 9.1, 9.3), keep prior completed state on node error (Req 9.4), and show an interruption message after 60s without an event (Req 9.5)
    - _Requirements: 9.1, 9.3, 9.4, 9.5_

  - [ ]* 14.3 (Optional) Write integration test for the SSE event sequence
    - A full run emits one start/completion per node and a final `done` (Req 9.2)
    - _Requirements: 9.2_

  - [ ]* 14.4 (Optional) Write component tests for progress states
    - Active-node/`completed/5` updates, node-error state, and 60s interruption message with a faked clock (Req 9.1, 9.3, 9.4, 9.5)
    - _Requirements: 9.1, 9.3, 9.4, 9.5_

- [ ] 15. Add voice Q&A
  - [ ] 15.1 Implement `POST /sessions/{id}/ask` on the backend
    - Answer questions about the analyzed bill, returning an answer within 30s or a timeout error (Req 10.6, 10.7)
    - _Requirements: 10.3, 10.6, 10.7_

  - [ ] 15.2 Implement the Voice_QA control on the Results screen
    - Where the Web Speech API exists, show the control within 1s (Req 10.1), transcribe and display text before submit (Req 10.2), submit 1–500 char transcripts (Req 10.3), handle no-speech within 10s (Req 10.4) and transcription failure (Req 10.5), read and display answers (Req 10.6), handle answer errors/timeouts without discarding prior answers (Req 10.7), and fall back to a text input where the API is absent (Req 10.8)
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8_

  - [ ]* 15.3 (Optional) Write component tests for Voice_QA
    - Control appears where supported, transcribe-then-show before submit, 1–500 char submission, no-speech/transcription-error handling, answer read/display, and text fallback where unsupported (Req 10.1–10.8)
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8_

  - [ ]* 15.4 (Optional) Write integration test for `/ask` timeout behavior
    - Returns an answer or a timeout error within 30s (Req 10.6, 10.7)
    - _Requirements: 10.6, 10.7_

- [ ] 16. Build the synthetic Eval_Set and evaluation harness
  - [ ] 16.1 Implement evaluation data models and the ten-bill Eval_Set
    - Implement `PlantedError`, `EvalBill`, and `EvalReport` in the eval module; create exactly ten synthetic bill PDFs under `eval/eval_set/`, each with 1–10 planted errors and a manifest recording each planted error's expected Finding type and unique location id; mark all bills synthetic (Req 12.1)
    - Treat a planted error lacking an expected Finding type as invalid, raising an error identifying the bill (Req 12.5)
    - _Requirements: 12.1, 12.3, 12.4, 12.5_

  - [ ] 16.2 Implement the evaluation harness and `POST /eval/run`
    - Run the pipeline once per bill recording retained Findings (Req 13.1); terminate with an empty-set error if the Eval_Set has zero bills (Req 13.2); record a failed bill and continue on pipeline failure, including the failed count (Req 13.3)
    - Match each retained Finding against planted errors classifying each planted error detected/missed (Req 13.4) and each unmatched Finding as a false positive (Req 13.5); report aggregate detected/missed/false-positive totals (Req 13.6)
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6_

  - [ ]* 16.3 (Optional) Write property test for planted-error classification
    - **Property 15: Planted-error classification is detected-iff-matched**
    - **Validates: Requirements 13.4, 13.5**

  - [ ]* 16.4 (Optional) Write property test for report-total conservation
    - **Property 16: Evaluation report totals conserve per-bill counts**
    - **Validates: Requirements 13.6**

  - [ ]* 16.5 (Optional) Write unit tests for Eval_Set validity and harness resilience
    - Exactly ten bills, 1–10 planted errors each with expected type and unique id, missing-expected-type rejection naming the bill (Req 12.3, 12.4, 12.5); empty-set termination (Req 13.2); failing-bill resilience (Req 13.3)
    - _Requirements: 12.3, 12.4, 12.5, 13.2, 13.3_

- [ ] 17. Final checkpoint — full pipeline and evaluation
  - Ensure all tests pass, ask the user if questions arise.
  - _Requirements: 5.1, 6.1, 9.2, 10.1, 13.6_

## Notes

- Tasks are ordered **MVP-first**: tasks 1–11 deliver a demonstrable Extractor → Auditor → Writer slice with the full three-screen UI; task 11 is the explicit MVP milestone. Tasks 12–16 add Rights_Checker, Skeptic, SSE polish, voice Q&A, and the evaluation harness as additive stages.
- Tasks marked with `*` and labeled `(Optional)` can be skipped for a faster MVP.
- Per the delivery directive, three property tests are **required** (not optional): the Sum_Check property tests (tasks 4.2, 4.3 — Properties 1 & 2), the duplicate-detection property test (task 5.2 — Property 6), and the Skeptic-threshold property test (task 13.3 — Property 10).
- All other property-based tests (Properties 3, 4, 5, 7, 8, 9, 11, 12, 13, 14, 15, 16) are optional.
- Each task references the specific requirements it implements for traceability; property-test tasks additionally cite the correctness property they validate.
- RAG uses `rank-bm25` (BM25 lexical ranking), not sentence-transformers.
- The System operates exclusively on synthetic data (Req 12.1).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "2.2", "3.1", "9.1"] },
    { "id": 2, "tasks": ["2.3", "2.4", "2.5", "3.2", "4.1", "9.2"] },
    { "id": 3, "tasks": ["4.2", "4.3", "5.1"] },
    { "id": 4, "tasks": ["5.2", "5.3", "5.4", "5.5", "5.6", "5.7", "6.1"] },
    { "id": 5, "tasks": ["6.2", "6.3", "6.4", "7.1"] },
    { "id": 6, "tasks": ["7.2"] },
    { "id": 7, "tasks": ["8.1", "8.2"] },
    { "id": 8, "tasks": ["8.3", "8.4", "10.1", "10.3"] },
    { "id": 9, "tasks": ["10.2"] },
    { "id": 10, "tasks": ["9.3", "10.4"] },
    { "id": 11, "tasks": ["12.1"] },
    { "id": 12, "tasks": ["12.2"] },
    { "id": 13, "tasks": ["12.3", "12.4", "12.5", "12.6"] },
    { "id": 14, "tasks": ["13.1"] },
    { "id": 15, "tasks": ["13.2", "13.3"] },
    { "id": 16, "tasks": ["14.1", "15.1", "16.1"] },
    { "id": 17, "tasks": ["14.2", "14.3", "15.2", "16.2"] },
    { "id": 18, "tasks": ["14.4", "15.3", "15.4", "16.3", "16.4", "16.5"] }
  ]
}
```
