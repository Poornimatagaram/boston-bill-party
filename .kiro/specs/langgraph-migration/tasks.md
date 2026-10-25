# Implementation Plan: LangGraph Migration

## Overview

Replace the explicit sequential runner in `backend/pipeline/graph.py` with a compiled LangGraph `StateGraph`. The public `run_pipeline(state, provider, pdf_source, progress=None, kb=None)` signature, `sessions.py` bridge, HTTP/SSE contract, node functions, and frontend stay unchanged. Implementation language is Python (as in the design).

Order of work: pin `langgraph`, snapshot the pre-migration runner as a reference, build shared test fixtures, rewrite `graph.py` internals, add required topology and equivalence tests, then checkpoint and commit the core migration. The optional letter-review interrupt (flag, review graph, resume, `langgraph-checkpoint` pin) comes last.

Conventions for every task: tests are sibling `*_test.py` files, use stub providers and injected KBs only, never call a real LLM or network, and unset `LANGSMITH_TRACING`, `LANGCHAIN_TRACING_V2`, and `LANGSMITH_API_KEY` via an autouse fixture. Money stays `Decimal`. Sum_Check, Auditor, and Skeptic stay pure. The core `graph.py` must not import `langgraph.checkpoint` anywhere.

## Tasks

- [ ] 1. Pin LangGraph and snapshot the pre-migration runner
  - [ ] 1.1 Add the `langgraph` pin to both dependency files
    - Add `langgraph==1.2.17` exactly once to `backend/requirements.txt` and to `[project].dependencies` in `backend/pyproject.toml`
    - Leave every existing pin unchanged; do not pin `langgraph-checkpoint`, `langchain-core`, `langgraph-prebuilt`, `langgraph-sdk`, or `langsmith` in this task
    - Install into `backend/.venv` with `.venv/bin/python -m pip install -r requirements.txt` and confirm `import langgraph.graph` works
    - _Requirements: 1.1, 1.2, 1.4, 1.5_

  - [ ] 1.2 Create `backend/pipeline/_reference_runner.py` as a verbatim copy of the current runner
    - Copy the current (pre-migration) `backend/pipeline/graph.py` orchestration verbatim before any rewrite: `_emit`, `_seed_node_status`, the `_run_*` node functions, `_run_node`, `_run_node_local`, and `run_pipeline` renamed to `run_pipeline_reference`
    - Import the same Node_Functions from their existing modules so equivalence tests compare orchestration only; keep module-attribute lookup of `read_pdf_text` the same as the original
    - Add a module docstring stating it is the frozen Pre_Migration_Runner used only by tests; the leading underscore keeps pytest from collecting it
    - _Requirements: 3.7, 7.7_

  - [ ] 1.3 Write `backend/deps_test.py` for the dependency rules
    - Parse `requirements.txt` and `pyproject.toml` (`tomllib`); assert each `langgraph*` package is declared once with an identical `==` pin and no range operators or extras
    - AST-scan `pipeline/graph.py` for `langgraph*` imports (including imports inside functions), map `langgraph.checkpoint*` to `langgraph-checkpoint` and other `langgraph.*` to `langgraph`, and assert the declared langgraph-family set equals the imported set
    - Assert all pre-migration pins are still present at their existing versions
    - _Requirements: 1.1, 1.2, 1.4, 1.5, 1.6_

- [ ] 2. Build shared test fixtures
  - [ ] 2.1 Create `backend/pipeline/_graph_fixtures.py`
    - `synthetic_bills()` Hypothesis strategy: 1–12 line items, codes from a small pool (so duplicates occur), `Decimal` amounts quantized to `0.01` in `[0.01, 99999.99]`, optional stated-total mismatch; outputs stub Extractor JSON plus fake PDF text
    - Helper to load the 10 eval-set bills for `@example`/parametrized use
    - `scenarios()` strategy over success, Rights_Checker failures (raise, empty, timeout, unexpected exception, empty query, missing bill), Extractor, Auditor, Skeptic, and Writer failures, with arbitrary exception messages via `text()`
    - `StubProvider`: per-node scripted responses or raises, records each call with the live RUNNING node, carries a sentinel `api_key` attribute and optional sentinel body text
    - `StubKB` with modes `match(passages)`, `empty`, `raise`, `slow`
    - Autouse-ready fixtures: unset LangSmith tracing env vars, block `socket.socket.connect`, monkeypatch `extractor.read_pdf_text` at module level, and lower `rights_checker.RETRIEVAL_TIMEOUT_S` for the slow mode
    - Helper that runs both `run_pipeline` and `run_pipeline_reference` on fresh `PipelineState`s with identical inputs and records `(node, status)` transitions
    - Synthetic data only; no real patient data
    - _Requirements: 3.7, 10.6, 10.7_

- [ ] 3. Rewrite `backend/pipeline/graph.py` on LangGraph
  - [ ] 3.1 Add runtime context, state conversion, and update helpers
    - Add frozen dataclass `PipelineContext(provider, kb, pdf_source, live_state, progress)` passed via `context_schema`; it never enters Graph_State
    - Implement `to_graph_input(state)` (exactly the 11 fields, deep-copied) and `from_graph_state(values)` (fill omitted keys from the caller, reject unknown keys, strict `PipelineState.model_validate`, raise `ValidationError` without touching the caller)
    - Implement `_diff(before, after)` and `_apply_update(live, update)` (validate then `setattr`)
    - Keep `NODE_ORDER`, `TOTAL_NODES`, `LLMProvider`, `ProgressCallback`, `_emit`, `_seed_node_status`, and `_get_knowledge_base` (now lock-guarded)
    - Do not modify `pipeline/state.py` or any Node_Function module
    - _Requirements: 2.1, 2.2, 2.4, 2.5, 2.7, 3.2, 3.8, 4.4_

  - [ ]* 3.2 Write property test for Graph_State conversion round trip in `backend/pipeline/graph_state_conversion_test.py`
    - **Property 3: Graph_State conversion round trip**
    - **Validates: Requirements 2.4, 2.5**

  - [ ]* 3.3 Write property test for invalid conversion rejection in `backend/pipeline/graph_state_conversion_test.py`
    - **Property 4: Invalid conversion is rejected without side effects**
    - **Validates: Requirements 2.7**

  - [ ] 3.4 Implement node wrappers and node bodies
    - `_fatal_node(node, body)`: emit RUNNING, run body on `state.model_copy(deep=True)`, map `ExtractorError`/`WriterError` to `to_state_error()` and other exceptions to `{failedNode, message: str(exc)}`, diff to keep partial writes, set `node_status`, mirror onto `live_state`, emit final status, return the update dict, never raise
    - `_local_node(node, body)` for `rights_checker`: any exception sets `rights_status = UNAVAILABLE`, status always `COMPLETED`, `error` never written
    - Bodies with the same calls and order as the pre-migration `_run_*`: extractor (`read_pdf_text` via module attribute, then `extract_bill`), auditor (`compute_sum_check`, `audit`, `retained_findings = list(findings)`), rights_checker (`run_rights_checker(w, ctx.kb)` once), skeptic (`apply_skeptic(findings)`), writer (`write_analysis(retained_findings, language, provider)` once)
    - No provider access in auditor or skeptic bodies
    - _Requirements: 3.2, 3.3, 3.4, 3.5, 3.6, 3.9, 3.10, 3.11, 5.1, 5.2, 5.3, 5.7, 5.8, 6.1, 6.2, 7.1, 7.2, 7.3, 7.4, 7.6_

  - [ ] 3.5 Build the graph, Error_Router, and compile-once accessor
    - `_route_after(node)`: `END` if `state.error` is set, else next node in `NODE_ORDER`
    - `_build_graph()`: `StateGraph(PipelineState, context_schema=PipelineContext)`, five nodes keyed by lowercase `NodeName`, `START → extractor`, conditional edges for extractor/auditor/skeptic `{next, END}`, plain edge `rights_checker → skeptic`, conditional `writer → {END}`
    - `get_compiled_graph()`: module-level `_COMPILED` with `threading.Lock` double-checked init, no checkpointer
    - Import only `langgraph.graph` and `langgraph.runtime`; no `langgraph.checkpoint` import; build nothing at import time
    - _Requirements: 3.1, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 5.4, 5.5, 5.6, 5.9_

  - [ ] 3.6 Rewrite `run_pipeline` to invoke the compiled graph
    - Seed node status, resolve the KB before `invoke`, build `PipelineContext`, call `get_compiled_graph().invoke(to_graph_input(state), context=ctx)`
    - Convert with `from_graph_state`, assign every field onto the caller's instance, return that same instance
    - Keep the signature `run_pipeline(state, provider, pdf_source, progress=None, kb=None)`; `sessions.py` needs no change
    - _Requirements: 2.3, 2.6, 4.2, 5.7, 7.5, 7.10_

- [ ] 4. Checkpoint - Ensure all tests pass
  - Run `cd backend && .venv/bin/python -m pytest -q`; all 71 pre-existing tests must pass unmodified. Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Required topology and equivalence tests
  - [ ] 5.1 Write topology tests in `backend/pipeline/graph_topology_test.py`
    - Node set is exactly `{__start__, extractor, auditor, rights_checker, skeptic, writer, __end__}`; entry node is `extractor`; edge set and `conditional` flags match the design
    - Success run executes each node once in order to `END` (via `stream_mode="updates"`), all `COMPLETED`, `error is None`
    - One fatal case each for extractor and writer (stub provider) and auditor and skeptic (monkeypatched `pipeline.graph.audit` / `apply_skeptic`): routes to `END`, failing node `ERROR`, earlier `COMPLETED`, later `PENDING`, `error.failedNode` correct, zero later provider calls
    - Four Rights_Checker failure cases (retrieval error, empty, timeout, unexpected exception): `rights_status == UNAVAILABLE`, `rights_checker`/`skeptic`/`writer` `COMPLETED`, `error is None`
    - `NODE_ORDER`/`TOTAL_NODES` values; compile-once (reset `_COMPILED`, spy `compile`, sequential and concurrent first calls give one compile); Graph_State fields equal `PipelineState.model_fields`; default graph `checkpointer is None`
    - _Requirements: 2.1, 4.1, 4.2, 4.3, 4.4, 4.5, 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 9.2, 10.2, 10.3, 10.4, 10.5, 10.7, 10.8_

  - [ ] 5.2 Write the required model-based equivalence property test in `backend/pipeline/graph_equivalence_test.py`
    - **Property 1: Model-based equivalence with the Pre_Migration_Runner**
    - Compare `run_pipeline` against `run_pipeline_reference` from `_reference_runner.py` over the 10 eval-set bills plus Hypothesis bills (`max_examples=100`), all languages and scenarios; assert caller identity, `model_dump()` equality, `Decimal` scale via `str()`, and identical `(node, status)` transition lists
    - **Validates: Requirements 2.3, 2.5, 2.6, 3.7, 3.9, 4.2, 5.6, 7.2, 9.2**

  - [ ] 5.3 Write the required Event_Sequence equivalence property test and bridge examples in `backend/pipeline/graph_events_test.py`
    - **Property 2: Event_Sequence equivalence through the Session_Bridge**
    - Async test with a real `asyncio.Queue` and `_make_progress_bridge`, drawing from success, Rights_Checker failure, Extractor failure, Writer failure, at least 50 examples (configure 100); assert equality with the reference runner driven through the same bridge, payload key sets, and a single final `("done", None)`
    - Req 7.5 example: writer stub blocks on a `threading.Event`; the writer `RUNNING` progress is observed on the queue while blocked
    - Req 7.9 example: KB builder raises, giving `node_error` then `done`
    - **Validates: Requirements 6.7, 7.5, 7.7, 7.8, 7.9, 8.4, 10.6**

  - [ ] 5.4 Write the import-side-effect test in `backend/pipeline/graph_import_test.py`
    - Subprocess import of `pipeline.graph` with `socket.socket`, `KnowledgeBase`, and `build_provider` patched to raise; import succeeds and `_COMPILED` and `_KNOWLEDGE_BASE` are still `None`
    - Assert `langgraph.checkpoint` is not in `sys.modules` after the import
    - _Requirements: 4.7_

  - [ ]* 5.5 Write property test for node-fatal failure invariants in `backend/pipeline/graph_equivalence_test.py`
    - **Property 5: Node-fatal failure invariants**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.7, 7.3**

  - [ ]* 5.6 Write property test for node-local Rights_Checker failures in `backend/pipeline/graph_equivalence_test.py`
    - **Property 6: Rights_Checker failures are node-local**
    - **Validates: Requirements 3.11, 5.9, 6.1, 6.2, 6.3, 6.4, 7.4**

  - [ ]* 5.7 Write property test for rights threshold and top-3 selection in `backend/pipeline/graph_equivalence_test.py`
    - **Property 7: Rights retrieval threshold and top-3 selection through the graph**
    - **Validates: Requirements 6.5, 6.6**

  - [ ]* 5.8 Write property test for pure-node contracts in `backend/pipeline/graph_equivalence_test.py`
    - **Property 8: Pure-node contracts**
    - **Validates: Requirements 3.3, 3.4, 3.6**

  - [ ]* 5.9 Write property test for no dependencies or secrets in state and errors in `backend/pipeline/graph_equivalence_test.py`
    - **Property 9: No dependencies or secrets in state or error messages**
    - **Validates: Requirements 3.8, 5.8, 8.8**

  - [ ]* 5.10 Write property test for RUNNING announced before node body in `backend/pipeline/graph_equivalence_test.py`
    - **Property 10: RUNNING is announced before the node body runs**
    - **Validates: Requirements 7.1, 7.2**

  - [ ]* 5.11 Write property test for progress-callback exceptions in `backend/pipeline/graph_equivalence_test.py`
    - **Property 11: Progress-callback exceptions do not change the outcome**
    - **Validates: Requirements 7.6**

  - [ ]* 5.12 Write property test for concurrent run isolation in `backend/pipeline/graph_equivalence_test.py`
    - **Property 12: Concurrent runs are isolated** (25 examples, barrier in stub provider)
    - **Validates: Requirements 4.6**

- [ ] 6. Checkpoint - Verify and commit the core migration
  - Run `cd backend && .venv/bin/python -m pytest -q`; all 71 pre-existing tests plus the new tests must pass, with no pre-existing test skipped, deselected, xfailed, or edited
  - Confirm `git diff --stat frontend/` is empty
  - Confirm `git diff --exit-code HEAD -- backend/pipeline/extractor.py backend/pipeline/sumcheck.py backend/pipeline/auditor.py backend/pipeline/rights_checker.py backend/pipeline/skeptic.py backend/pipeline/writer.py backend/pipeline/state.py backend/main.py backend/sessions.py` shows no changes
  - Confirm `pipeline/graph.py` has no `langgraph.checkpoint` import
  - Verify a fresh install in a temporary directory outside the repo: `python3.14 -m venv <tmp>`, `<tmp>/bin/pip install -r backend/requirements.txt`, `<tmp>/bin/python -c "import langgraph.graph"`, then delete the temp venv
  - Create branch `feat/langgraph-migration`, stage only the specific changed/new files (`backend/requirements.txt`, `backend/pyproject.toml`, `backend/pipeline/graph.py`, `backend/pipeline/_reference_runner.py`, `backend/pipeline/_graph_fixtures.py`, the new `backend/pipeline/graph_*_test.py` files, `backend/deps_test.py`), never `git add -A` and never `backend/.env`, then commit the core migration
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Optional letter-review interrupt: configuration
  - [ ] 7.1 Add the `LETTER_REVIEW_INTERRUPT` flag to `backend/config.py`
    - Add `LETTER_REVIEW_INTERRUPT_VAR`, `parse_bool_flag(name, raw)` (absent/empty/whitespace → `False`; case-insensitive `1/true/yes/on` and `0/false/no/off`; otherwise `ConfigError("Invalid LETTER_REVIEW_INTERRUPT value. Use true or false.")` echoing no value), `PipelineOptions`, and `load_pipeline_options(env=None)` using the existing `.env` + `os.environ` resolution
    - Add `configure(options)` and `letter_review_enabled()` to `pipeline/graph.py` (default `False` when never configured)
    - Call `load_pipeline_options()` in `main.lifespan` next to `load_config()` and pass the result to `pipeline.graph.configure`
    - _Requirements: 9.1, 9.10_

  - [ ]* 7.2 Write property test for interrupt flag parsing in `backend/config_pipeline_options_test.py`
    - **Property 15: Interrupt flag parsing**
    - **Validates: Requirements 9.1, 9.10**

- [ ] 8. Optional letter-review interrupt: review graph and resume
  - [ ] 8.1 Pin `langgraph-checkpoint` for the interrupt option
    - Add `langgraph-checkpoint==4.3.0` exactly once to both `backend/requirements.txt` and `backend/pyproject.toml`, matching pins; reinstall into `backend/.venv`
    - `deps_test.py` must still pass once task 8.2 adds the direct import
    - _Requirements: 1.4, 1.6_

  - [ ] 8.2 Implement `get_review_graph()` and pause behavior in `pipeline/graph.py`
    - Lazily import `langgraph.checkpoint.memory.InMemorySaver` and `langgraph.checkpoint.serde.jsonplus.JsonPlusSerializer` inside the builder only
    - Compile the same builder with `checkpointer=InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=[...]))` listing the `models` and `pipeline.state` types, and `interrupt_before=["writer"]`; compile once, lock-guarded
    - In `run_pipeline`, use the review graph with `{"configurable": {"thread_id": state.session_id}}` only when `letter_review_enabled()`; otherwise unchanged default path with no checkpointer
    - _Requirements: 9.2, 9.3, 9.5_

  - [ ] 8.3 Implement `resume_letter_review` and `NoPausedRunError`
    - Pre-check `get_state(cfg).next == ("writer",)`; otherwise raise `NoPausedRunError("No paused run exists for this session.")` before touching any state
    - Resume with `graph.invoke(None, cfg, context=ctx)` (runs only `writer`), convert and assign onto the caller's instance, then `checkpointer.delete_thread(session_id)`
    - Resuming never sets `confirmed_letter`; Python entry point only, no HTTP route
    - _Requirements: 9.4, 9.5, 9.6, 9.8, 9.9_

  - [ ] 8.4 Handle paused runs in `backend/sessions.py`
    - In `_run_session_pipeline`, skip the terminal `done` when the returned state is paused (`writer` `PENDING`, `error is None`, `result is None`)
    - Add internal coroutine `resume_session_pipeline(session_id)` that runs `resume_letter_review` via `asyncio.to_thread`, then enqueues `done`; not wired to any route
    - Default (flag off) path stays identical
    - _Requirements: 7.9, 7.10, 8.6, 8.7, 9.6, 9.8_

  - [ ]* 8.5 Write property test for pause then resume in `backend/pipeline/graph_interrupt_test.py`
    - **Property 13: Pause then resume equals an uninterrupted run**
    - **Validates: Requirements 9.3, 9.4, 10.9**

  - [ ]* 8.6 Write property test for resume without a paused run in `backend/pipeline/graph_interrupt_test.py`
    - **Property 14: Resume without a paused run is rejected**
    - **Validates: Requirements 9.9**

  - [ ]* 8.7 Write interrupt example tests in `backend/pipeline/graph_interrupt_test.py`
    - Flag enabled: pauses with `writer` not `COMPLETED`, resume runs `writer` to `END` with non-null `result`; flag disabled: runs to `END` without pausing
    - `confirmed_letter is None` after resume; `app.routes` identical with flag on and off; checkpointer is `InMemorySaver` and the thread is deleted after resume
    - Reset `pipeline.graph.configure(...)` in teardown; never read the flag from `backend/.env`
    - _Requirements: 8.7, 9.5, 9.6, 9.8, 10.9_

  - [ ] 8.8 Document the Letter_Review_Interrupt as a design option
    - Add a code-level docstring/comment block in `pipeline/graph.py` above `get_review_graph` and `resume_letter_review` covering how the flag is set, how a paused run resumes, what the client observes while paused, and how it interacts with `/confirm`; add `LETTER_REVIEW_INTERRUPT=` (commented, default off) to any backend `.env.example` if one exists, never to `backend/.env`
    - _Requirements: 9.7_

- [ ] 9. Final checkpoint - Ensure all tests pass
  - Run `cd backend && .venv/bin/python -m pytest -q` with the interrupt work included; confirm `git diff --stat frontend/` is still empty. Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP. Properties 1 and 2, the reference runner, and the topology tests are required.
- The reference runner (1.2) must be copied before `graph.py` is rewritten (task 3).
- The core migration imports only `langgraph`; `langgraph-checkpoint` is pinned in task 8.1 together with its first direct import, so `deps_test.py` holds at both commits.
- Requirement 1.3 (fresh-venv install) and 3.2 (Node_Function sources unchanged) are checked in the task 6 checkpoint; Requirement 8.5 (frontend unchanged) in tasks 6 and 9.
- Tasks 7–8 are the optional stretch; all of them come after the core commit.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "2.1"] },
    { "id": 2, "tasks": ["3.1"] },
    { "id": 3, "tasks": ["3.2", "3.4"] },
    { "id": 4, "tasks": ["3.3", "3.5"] },
    { "id": 5, "tasks": ["3.6"] },
    { "id": 6, "tasks": ["5.1", "5.2", "5.3", "5.4"] },
    { "id": 7, "tasks": ["5.5"] },
    { "id": 8, "tasks": ["5.6"] },
    { "id": 9, "tasks": ["5.7"] },
    { "id": 10, "tasks": ["5.8"] },
    { "id": 11, "tasks": ["5.9"] },
    { "id": 12, "tasks": ["5.10"] },
    { "id": 13, "tasks": ["5.11"] },
    { "id": 14, "tasks": ["5.12"] },
    { "id": 15, "tasks": ["7.1", "8.1"] },
    { "id": 16, "tasks": ["7.2", "8.2"] },
    { "id": 17, "tasks": ["8.3"] },
    { "id": 18, "tasks": ["8.4", "8.5"] },
    { "id": 19, "tasks": ["8.6"] },
    { "id": 20, "tasks": ["8.7", "8.8"] }
  ]
}
```
