# Requirements Document

## Introduction

The Boston Bill Party backend currently runs its five-agent pipeline (Extractor → Auditor → Rights_Checker → Skeptic → Writer) through an explicit sequential runner in `backend/pipeline/graph.py`. That runner was deliberately shaped with LangGraph-compatible `(state) -> state` node contracts so it could be swapped for a real LangGraph `StateGraph` later.

This feature performs that swap. The pipeline becomes a compiled LangGraph `StateGraph` whose nodes wrap the existing node functions unchanged, whose conditional edges encode the existing node-fatal (Extractor, Writer) versus node-local (Rights_Checker) error behavior, and whose execution keeps emitting the same `progress` / `node_error` / `done` events to the session queue and SSE endpoint. An optional, off-by-default human-in-the-loop interrupt before the Writer finalizes the appeal letter is added as a documented design option using a LangGraph checkpointer.

Out of scope: any change to the frontend, the HTTP API contract, the SSE event shapes, the `POST /sessions/{id}/confirm` flow, or the behavior of the individual node functions. The product safety rules still apply: the LLM does no billing math, Sum_Check / Auditor / Skeptic stay pure and deterministic, money stays `Decimal`, a human confirms the letter before any export, tests never call a real LLM, and client-facing errors stay generic.

## Glossary

- **Pipeline_Graph**: The compiled LangGraph `StateGraph` defined in `backend/pipeline/graph.py` that replaces the sequential runner.
- **Pipeline_Runner**: The public `run_pipeline(state, provider, pdf_source, progress=None, kb=None)` entry point in `backend/pipeline/graph.py` that executes the Pipeline_Graph and returns the populated `PipelineState`.
- **Graph_State**: The typed LangGraph state schema used by the Pipeline_Graph; its fields mirror `PipelineState` in `backend/pipeline/state.py` (`session_id`, `language`, `raw_text`, `bill`, `sum_check`, `findings`, `retained_findings`, `rights_status`, `result`, `node_status`, `error`).
- **Pipeline_Node**: One of the five LangGraph nodes: Extractor, Auditor, Rights_Checker, Skeptic, Writer, keyed by the lowercase `config.NodeName` values (`extractor`, `auditor`, `rights_checker`, `skeptic`, `writer`).
- **Node_Function**: The existing per-node logic the Pipeline_Nodes wrap: `read_pdf_text` + `extract_bill`, `compute_sum_check` + `audit`, `run_rights_checker`, `apply_skeptic`, `write_analysis`.
- **Node_Fatal_Error**: A failure in the Extractor, Auditor, Skeptic, or Writer that sets `error = {failedNode, message}` on the state, marks the node `ERROR`, and halts the pipeline (existing Req 9.4 behavior).
- **Node_Local_Failure**: A Rights_Checker failure (retrieval error, empty retrieval result, retrieval timeout, empty built query, missing extracted bill, or unexpected exception) that sets `rights_status = UNAVAILABLE`, marks the node `COMPLETED`, leaves `error` as `None`, and lets the pipeline continue (existing Req 5.5 behavior).
- **Pre_Migration_Runner**: The explicit sequential `run_pipeline` implementation in `backend/pipeline/graph.py` as it exists before this migration; the reference model for equivalence properties.
- **Error_Router**: The conditional-edge routing function(s) that send the Pipeline_Graph to `END` after a Node_Fatal_Error and to the next Pipeline_Node otherwise.
- **Progress_Callback**: The optional `progress(node_name, status, state)` callback accepted by the Pipeline_Runner and invoked on every node status transition.
- **Session_Bridge**: The code in `backend/sessions.py` that runs the Pipeline_Runner on a worker thread and converts Progress_Callback transitions into `(event_type, data)` items on the session's `asyncio.Queue`.
- **SSE_Endpoint**: `GET /sessions/{id}/events` in `backend/main.py`, which relays queue items as `progress`, `node_error`, and `done` SSE frames.
- **Progress_Event**: The `progress` payload `{activeNode, completedNodes, completedCount, total: 5}`.
- **Event_Sequence**: The ordered list of `(event_type, data)` items placed on the session queue during one pipeline run, ending with `("done", None)`.
- **Letter_Review_Interrupt**: The optional LangGraph interrupt that pauses the Pipeline_Graph before the Writer finalizes the appeal letter and resumes after human input.
- **Checkpointer**: A LangGraph checkpointer (in-memory) that persists Graph_State between an interrupt and its resume, keyed by thread id.
- **Interrupt_Option**: The backend configuration flag that enables the Letter_Review_Interrupt; disabled by default.
- **Stub_Provider**: A deterministic in-memory `LLMProvider` test double; tests use Stub_Providers and never a real LLM.
- **Topology_Tests**: New `*_test.py` tests that assert the Pipeline_Graph's node set, node order, and error-routing edges.

## Requirements

### Requirement 1: Pinned LangGraph dependency

**User Story:** As a backend maintainer, I want LangGraph pinned to an exact, Python 3.14-compatible version, so that the pipeline installs reproducibly on the project interpreter.

#### Acceptance Criteria

1. THE backend `requirements.txt` SHALL declare the `langgraph` package exactly once, with an exact `==` version pin that has no range operators (`>=`, `~=`, `<`, `*`) and no unpinned extras.
2. THE backend `pyproject.toml` `[project].dependencies` list SHALL declare the `langgraph` package exactly once, with a version string identical to the `==` pin in `requirements.txt`.
3. WHEN `pip install -r requirements.txt` runs in a fresh Python 3.14 virtual environment with no packages installed beyond pip, THE installation SHALL complete with exit code 0, and running `python -c "import langgraph.graph"` in that environment SHALL exit with code 0.
4. THE backend dependency files SHALL declare only the LangGraph-family packages (package names starting with `langgraph`) that the Pipeline_Graph module imports directly, with each such package pinned with an exact `==` version that is identical in `requirements.txt` and `pyproject.toml`.
5. WHEN the LangGraph dependency is added, THE backend `requirements.txt` and `pyproject.toml` SHALL keep every previously declared dependency at its existing `==` version, and the backend test suite (`.venv/bin/python -m pytest -q`) SHALL exit with code 0 on Python 3.14.
6. IF a LangGraph-family package is declared in `requirements.txt` or `pyproject.toml` but the Pipeline_Graph module does not import it directly, or is imported directly but declared in only one of the two files, THEN THE dependency check SHALL be treated as failed for this requirement.

### Requirement 2: Typed graph state mirroring PipelineState

**User Story:** As a backend maintainer, I want the LangGraph state to mirror `PipelineState`, so that node contracts, sessions, and tests keep working against one state shape.

#### Acceptance Criteria

1. THE Graph_State SHALL contain exactly the 11 fields of `PipelineState` (`session_id`, `language`, `raw_text`, `bill`, `sum_check`, `findings`, `retained_findings`, `rights_status`, `result`, `node_status`, and `error`), with no additional fields and with each field having the same type and the same optionality as the corresponding `PipelineState` field.
2. THE `PipelineState` model SHALL keep `extra="forbid"` and its existing field names, types, and defaults (`raw_text`, `bill`, `sum_check`, `result`, and `error` default to none; `findings` and `retained_findings` default to empty lists; `node_status` defaults to an empty mapping; `rights_status` defaults to completed).
3. WHEN the Pipeline_Runner returns after a run in which every node completed, THE Pipeline_Runner SHALL return the same `PipelineState` instance (identical object identity) that the caller passed in, with each of the 11 fields equal to the corresponding final Graph_State value.
4. FOR ALL Graph_State values produced by a run, converting the Graph_State to a `PipelineState` and back to a Graph_State SHALL produce a Graph_State in which every one of the 11 fields compares equal to the original, including `Decimal` amounts with identical value and identical number of decimal places, findings lists in identical order, and `node_status` with identical keys and statuses (round-trip property).
5. THE Graph_State SHALL carry money amounts as `Decimal` values in `bill`, `sum_check`, `findings`, and `retained_findings`, with no conversion of any amount to `float` at any point in the Pipeline_Graph, such that every amount read from the returned `PipelineState` is a `Decimal` instance.
6. IF a Pipeline_Graph node fails with a Node_Fatal_Error, THEN THE Pipeline_Runner SHALL return the same `PipelineState` instance that the caller passed in, with `error` set to the failed node name and a message, the failed node's `node_status` entry set to error, and all fields written by nodes that completed before the failure retained with their completed values.
7. IF converting a Graph_State to a `PipelineState` encounters a field name not among the 11 `PipelineState` fields or a value that fails `PipelineState` type validation, THEN THE Pipeline_Runner SHALL reject the conversion with a validation error and SHALL leave the caller's `PipelineState` instance fields unchanged by that conversion.

### Requirement 3: Node contracts unchanged

**User Story:** As a backend maintainer, I want each existing node's logic wrapped rather than rewritten, so that pipeline behavior and its safety rules are preserved.

#### Acceptance Criteria

1. THE Pipeline_Graph SHALL contain exactly five Pipeline_Nodes named `extractor`, `auditor`, `rights_checker`, `skeptic`, and `writer`, connected by exactly one edge path that runs them in the order `extractor` → `auditor` → `rights_checker` → `skeptic` → `writer`, with no branching edges and no Pipeline_Node running more than once per run.
2. THE Pipeline_Graph SHALL wrap the existing Node_Functions (`extract_bill`, `read_pdf_text`, `compute_sum_check`, `audit`, `run_rights_checker`, `apply_skeptic`, `write_analysis`) in their current module locations (`pipeline/extractor.py`, `pipeline/sumcheck.py`, `pipeline/auditor.py`, `pipeline/rights_checker.py`, `pipeline/skeptic.py`, `pipeline/writer.py`), with each function's name, parameter list, parameter order, return type, and body identical to the pre-migration version.
3. WHEN the Auditor Pipeline_Node runs, THE Auditor Pipeline_Node SHALL set `sum_check` to the result of `compute_sum_check(bill)` before calling `audit`, SHALL set `findings` to the result of `audit(bill, sum_check)`, and SHALL set `retained_findings` to a new list containing the same Findings as `findings` in the same order (a list object distinct from `findings`).
4. WHEN the Skeptic Pipeline_Node runs, THE Skeptic Pipeline_Node SHALL set `retained_findings` to `apply_skeptic(findings)`, where `findings` contains both the Auditor and Rights_Checker Findings, and SHALL leave `findings` unchanged.
5. WHEN the Writer Pipeline_Node runs, THE Writer Pipeline_Node SHALL call `write_analysis` exactly once with `retained_findings`, the session `language`, and the injected provider, and SHALL set `result` to its return value.
6. THE Auditor Pipeline_Node (including its Sum_Check computation) and the Skeptic Pipeline_Node SHALL make zero LLM provider calls, as measured by a call-counting Stub_Provider whose call count is unchanged across each of those nodes' executions.
7. FOR ALL synthetic bills (the 10 eval-set bills plus at least 100 Hypothesis-generated synthetic bills) and Stub_Provider responses (including responses that make the Extractor or Writer raise its node-fatal error, and a knowledge base whose search raises), running the Pipeline_Runner SHALL produce a final `PipelineState` equal field by field (including `node_status`, `rights_status`, and `error`) to the state produced by the Pre_Migration_Runner on the same inputs, and SHALL emit the same ordered sequence of (node name, node status) progress transitions (model-based property).
8. THE Pipeline_Runner SHALL pass the LLM provider and the knowledge base to Pipeline_Nodes by injection, such that the Graph_State contains only the `PipelineState` fields and holds no reference to the provider object, the knowledge base object, or the LLM API key at any point during or after a run.
9. WHEN the Extractor Pipeline_Node runs, THE Extractor Pipeline_Node SHALL set `raw_text` to the result of `read_pdf_text(pdf_source)` and then set `bill` to the result of `extract_bill(pdf_source, provider)`.
10. WHEN the Rights_Checker Pipeline_Node runs, THE Rights_Checker Pipeline_Node SHALL call `run_rights_checker(state, kb)` exactly once with the injected knowledge base and SHALL leave `retained_findings` unchanged.
11. IF `run_rights_checker` raises an exception, THEN THE Rights_Checker Pipeline_Node SHALL handle the exception as the Node_Local_Failure specified in Requirement 6, criterion 2.

### Requirement 4: Node order and edges

**User Story:** As a backend maintainer, I want the graph topology to encode the canonical pipeline order, so that the pipeline runs the same sequence as before.

#### Acceptance Criteria

1. THE Pipeline_Graph SHALL declare `extractor` as its single entry node, so that `extractor` is the first Pipeline_Node to start in every Pipeline_Runner call.
2. WHEN the Pipeline_Runner executes a run in which no Node_Fatal_Error occurs (including runs where the Rights_Checker has a Node_Local_Failure), THE Pipeline_Graph SHALL execute each of the five Pipeline_Nodes exactly once, in the order `extractor` → `auditor` → `rights_checker` → `skeptic` → `writer`, and then route to `END`, with no Pipeline_Node skipped, repeated, or run in parallel.
3. THE Pipeline_Graph SHALL define exactly one successor for each Pipeline_Node on the success path (the next entry in `NODE_ORDER`, or `END` after `writer`), and SHALL define no other edges besides the Error_Router edges to `END` specified in Requirement 5.
4. THE `NODE_ORDER` constant SHALL keep the value `(EXTRACTOR, AUDITOR, RIGHTS_CHECKER, SKEPTIC, WRITER)`, THE `TOTAL_NODES` constant SHALL keep the value 5, and THE Pipeline_Graph success-path order SHALL equal the order of `NODE_ORDER`, with each Pipeline_Node keyed by its lowercase `NodeName` value.
5. THE Pipeline_Graph SHALL be compiled at most once per Python process, so that 2 or more consecutive Pipeline_Runner calls in the same process all execute the same compiled Pipeline_Graph object and the compile step runs exactly one time.
6. WHEN 2 Pipeline_Runner calls execute concurrently on separate worker threads with different `PipelineState` inputs, THE Pipeline_Runner SHALL return to each caller a `PipelineState` that contains only the `session_id`, findings, `node_status`, and `result` derived from that caller's own input.
7. WHEN the `pipeline.graph` module is imported, THE `pipeline.graph` module SHALL complete the import without constructing a knowledge base, reading any corpus file, invoking any LLM provider, or making any network call.

### Requirement 5: Node-fatal error routing

**User Story:** As a patient using the app, I want a failed extraction or letter-writing step to stop cleanly with a clear failed-step indicator, so that I am not shown partial or invented results.

#### Acceptance Criteria

1. IF the Extractor Pipeline_Node raises `ExtractorError`, THEN THE Pipeline_Graph SHALL set `error` to exactly the value returned by `ExtractorError.to_state_error()` (with `failedNode` equal to `extractor`), mark `extractor` as `ERROR`, and route to `END` through the Error_Router.
2. IF the Writer Pipeline_Node raises `WriterError`, THEN THE Pipeline_Graph SHALL set `error` to exactly the value returned by `WriterError.to_state_error()` (with `failedNode` equal to `writer`), mark `writer` as `ERROR`, and route to `END` through the Error_Router.
3. IF the Extractor, Auditor, Skeptic, or Writer Pipeline_Node raises any exception other than `ExtractorError` or `WriterError`, THEN THE Pipeline_Graph SHALL set `error` to `{failedNode: <lowercase node name>, message: str(exception)}`, identical to the shape the Pre_Migration_Runner records, mark that node as `ERROR`, and route to `END` through the Error_Router.
4. WHEN a Node_Fatal_Error occurs in a Pipeline_Node, THE Pipeline_Graph SHALL leave every Pipeline_Node after it in `NODE_ORDER` in `PENDING` status, SHALL make zero provider calls from those later Pipeline_Nodes, and SHALL leave `result` equal to its value from before the failing Pipeline_Node started.
5. WHEN a Node_Fatal_Error occurs in a Pipeline_Node, THE Pipeline_Graph SHALL keep the `COMPLETED` status of every earlier Pipeline_Node, so that exactly one Pipeline_Node has `ERROR` status in the returned state.
6. WHEN a Pipeline_Node completes without error, THE Error_Router SHALL route the Pipeline_Graph to the next Pipeline_Node in `NODE_ORDER`, or to `END` after `writer`, and SHALL leave `error` as `None`.
7. IF a Node_Fatal_Error occurs, THEN THE Pipeline_Runner SHALL return the state normally without propagating the exception to the caller, and SHALL deliver exactly one `ERROR` status transition for the failing Pipeline_Node to the Progress_Callback, followed by no further transitions for any Pipeline_Node.
8. THE `error.message` values recorded by the Pipeline_Graph SHALL contain neither the configured LLM API key value (as any substring) nor any text taken from a provider response body.
9. IF the Rights_Checker Pipeline_Node raises any exception, THEN THE Error_Router SHALL route the Pipeline_Graph to the `skeptic` Pipeline_Node as specified in Requirement 6, criterion 2, rather than through the Node_Fatal_Error path to `END`.

### Requirement 6: Node-local Rights_Checker failures

**User Story:** As a patient using the app, I want the bill audit to finish even if the rights lookup fails, so that I still get my summary and letter.

#### Acceptance Criteria

1. IF the Rights_Checker Pipeline_Node's retrieval raises an error, returns zero passages, or does not return within 15 seconds, or the query built from the bill and auditor Findings is empty, or the state has no extracted bill, THEN THE Rights_Checker Pipeline_Node SHALL set `rights_status` to `UNAVAILABLE`, set the `rights_checker` node status to `COMPLETED`, and leave `error` as `None`.
2. IF the Rights_Checker Pipeline_Node raises an exception not covered by criterion 1, THEN THE Pipeline_Graph SHALL catch the exception, set `rights_status` to `UNAVAILABLE`, set the `rights_checker` node status to `COMPLETED`, leave `error` as `None`, and route to the `skeptic` Pipeline_Node as the next node.
3. WHEN a Node_Local_Failure occurs, THE Pipeline_Graph SHALL keep every auditor Finding present in `findings` before the Rights_Checker Pipeline_Node ran, in the same order and with unchanged content, and SHALL add zero `rights_issue` Findings.
4. WHEN a Node_Local_Failure occurs, THE Pipeline_Graph SHALL run the Skeptic and Writer Pipeline_Nodes in that order, set both node statuses to `COMPLETED`, and produce a non-null `result` with `error` left as `None`.
5. WHEN the Rights_Checker Pipeline_Node's retrieval succeeds and one or more passages have a relevance score greater than or equal to 0.7, THE Rights_Checker Pipeline_Node SHALL append one `rights_issue` Finding per passage for at most the 3 highest-scoring passages (ties broken by passage identifier, ascending) after the existing auditor Findings, and set `rights_status` to `COMPLETED`.
6. WHEN the Rights_Checker Pipeline_Node's retrieval succeeds, returns at least one passage, and no passage has a relevance score greater than or equal to 0.7, THE Rights_Checker Pipeline_Node SHALL add zero `rights_issue` Findings, keep all auditor Findings unchanged, and set `rights_status` to `COMPLETED`.
7. WHILE a session's `rights_status` is `UNAVAILABLE`, THE Pipeline_Graph SHALL emit the same progress events for the `rights_checker` Pipeline_Node (started, then completed) as on a successful run, and SHALL emit zero `node_error` events for that run.

### Requirement 7: Live progress events

**User Story:** As a patient watching the progress screen, I want the five-step progress display to keep updating live, so that I can see which agent is working.

#### Acceptance Criteria

1. WHEN a Pipeline_Node is about to run, THE Pipeline_Runner SHALL set that node's status to `RUNNING` in `state.node_status` and then invoke the Progress_Callback exactly once with `(node_name, RUNNING, state)` before the Node_Function starts, independently of `graph.stream` update delivery (which yields a node's update only after the node finishes).
2. WHEN a Pipeline_Node's Node_Function returns without a Node_Fatal_Error, THE Pipeline_Runner SHALL set that node's status to `COMPLETED` in `state.node_status` and then invoke the Progress_Callback exactly once with `(node_name, COMPLETED, state)` before the next Pipeline_Node's `RUNNING` invocation.
3. IF a Node_Fatal_Error occurs in the Extractor, Auditor, Skeptic, or Writer, THEN THE Pipeline_Runner SHALL set `state.error` to `{failedNode, message}`, set that node's status to `ERROR`, invoke the Progress_Callback exactly once with `(node_name, ERROR, state)`, and invoke the Progress_Callback zero further times for that run.
4. IF the Rights_Checker has a Node_Local_Failure, THEN THE Pipeline_Runner SHALL invoke the Progress_Callback with `(rights_checker, COMPLETED, state)`, SHALL NOT invoke the Progress_Callback with `ERROR` for the Rights_Checker, and SHALL leave `state.error` unset.
5. WHILE the Pipeline_Graph is executing (for example, driven by `graph.stream`), THE Pipeline_Runner SHALL invoke each Progress_Callback synchronously on the worker thread at its transition point, so that the Session_Bridge enqueues every event before the next Pipeline_Node starts and before `run_pipeline` returns, as verified by a test whose Stub_Provider blocks inside the Writer and which observes the Writer's `RUNNING` Progress_Event already on the session queue while the Writer is still blocked.
6. IF the Progress_Callback raises an exception, THEN THE Pipeline_Runner SHALL discard the exception without setting `state.error`, still record the transition in `state.node_status`, and continue the run, giving the same final `state.result`, `state.error`, and `state.node_status` as a run whose Progress_Callback does not raise.
7. FOR ALL synthetic bills, Stub_Provider behaviors, and knowledge-base behaviors (success, Rights_Checker failure, Extractor failure, Writer failure), THE Event_Sequence produced through the Session_Bridge SHALL equal the Event_Sequence produced by the Pre_Migration_Runner on the same inputs (model-based property), where the expected sequences are: success yields 10 Progress_Events then `done`; Rights_Checker failure yields 10 Progress_Events with no `node_error` then `done`; Extractor failure yields 1 Progress_Event, then 1 `node_error`, then `done`; Writer failure yields 9 Progress_Events, then 1 `node_error`, then `done`.
8. THE Session_Bridge SHALL emit Progress_Event payloads with exactly the fields `activeNode` (the single `RUNNING` node name, or null), `completedNodes` (in pipeline order), `completedCount` (from 0 to 5, equal to the length of `completedNodes`), and `total` (always 5), and SHALL emit `node_error` payloads with exactly the fields `failedNode` and `message`.
9. WHEN a run ends by completing, by a Node_Fatal_Error, or by an unexpected exception outside any Pipeline_Node, THE Session_Bridge SHALL place exactly one `("done", None)` item on the session queue as the final item of that run.
10. THE `backend/sessions.py` module SHALL keep calling the Pipeline_Runner with the signature `run_pipeline(state, provider, pdf_source, progress)` on a worker thread via `asyncio.to_thread`.

### Requirement 8: API contract and frontend unchanged

**User Story:** As a frontend developer, I want the migration to be invisible to the client, so that no frontend or API change is needed.

#### Acceptance Criteria

1. THE backend SHALL keep the routes `POST /sessions`, `GET /sessions/{id}/result`, `GET /sessions/{id}/events`, `POST /sessions/{id}/confirm`, `POST /sessions/{id}/ask`, `POST /sessions/{id}/translate-letter`, and `POST /eval/run` with the same request fields, response fields, field types, and status codes as the pre-migration backend, so that every pre-migration endpoint test passes without modification.
2. WHEN a client sends a request the pre-migration backend rejects (unknown session id, unsupported file type, file size out of range, corrupt PDF, upload not marked synthetic, unsupported language, empty or over-length question, empty or over-length letter, analysis not ready, translation requested for an English session, empty eval set), THE backend SHALL return the same rejection status and the same error category as the pre-migration backend and SHALL leave the session state unchanged.
3. WHEN a client connects to the SSE_Endpoint for an existing session, THE SSE_Endpoint SHALL first emit one `progress` event carrying the session's current progress snapshot, then relay the session queue's `progress` and `node_error` events with the pre-migration payload shapes, and SHALL end the stream with exactly one `done` event whose data is the empty JSON object `{}`.
4. WHEN a pipeline run completes on the migrated backend, THE SSE_Endpoint SHALL emit, after the initial snapshot, `progress` events for the five nodes in the order Extractor, Auditor, Rights_Checker, Skeptic, Writer, with one start update and one completion update per node, matching the pre-migration event sequence.
5. THE migration SHALL leave every file under `frontend/` byte-for-byte unmodified, as verified by an empty diff of `frontend/` against the pre-migration commit.
6. WHILE the Interrupt_Option is disabled, THE `POST /sessions/{id}/confirm` endpoint SHALL remain the only human-confirmation gate for the appeal letter, SHALL accept edited letters of 0 to 20,000 characters, and SHALL reject letters longer than 20,000 characters with status 400 without changing the session's recorded confirmed letter.
7. IF a session's appeal letter has not been confirmed through `POST /sessions/{id}/confirm`, THEN THE backend SHALL keep the session's recorded confirmed letter unset, regardless of whether the Interrupt_Option is enabled or disabled.
8. IF a provider call fails, times out, or a Pipeline_Node raises an error, THEN THE backend SHALL return or emit the same error messages as the pre-migration backend, and the response bodies and SSE payloads SHALL contain no stack trace, no provider request headers, no provider response body text, and no LLM API key value.

### Requirement 9: Optional human-in-the-loop letter review (stretch)

**User Story:** As a product owner, I want an optional LangGraph interrupt before the letter is finalized, so that we can evaluate graph-native human review without changing today's confirm flow.

#### Acceptance Criteria

1. THE Interrupt_Option SHALL be a single boolean backend configuration setting, read once at backend startup, that defaults to disabled when the setting is absent or empty.
2. WHILE the Interrupt_Option is disabled, THE Pipeline_Graph SHALL compile without a Letter_Review_Interrupt and without a Checkpointer, run every Pipeline_Node from Extractor through Writer to `END` without pausing, and produce the same session result shape and node status events as the Pre_Migration_Runner produces for the same input and stubbed provider responses.
3. WHERE the Interrupt_Option is enabled, WHEN the Skeptic Pipeline_Node completes for a session, THE Pipeline_Graph SHALL save the Graph_State to an in-memory Checkpointer under that session's thread id and pause before the Writer Pipeline_Node starts, such that no summary and no appeal letter exist in the Graph_State while the run is paused.
4. WHERE the Interrupt_Option is enabled, WHEN the Pipeline_Graph is resumed once for the same thread id with human input, THE Pipeline_Graph SHALL continue from the saved Graph_State without re-running the Extractor, Auditor, Rights_Checker, or Skeptic Pipeline_Nodes, and SHALL run the Writer Pipeline_Node to `END`.
5. WHERE the Interrupt_Option is enabled, THE Checkpointer SHALL keep Graph_State in process memory only, write no Graph_State to disk, a database, or any external service, and retain no Graph_State after the backend process stops, consistent with in-memory sessions and synthetic data.
6. WHERE the Interrupt_Option is enabled, THE backend SHALL keep `POST /sessions/{id}/confirm` required before any export action, and resuming the Pipeline_Graph SHALL NOT count as confirmation of the appeal letter.
7. THE design document SHALL describe the Letter_Review_Interrupt as a design option, including how the Interrupt_Option is set, how a paused run resumes, what the client observes while a run is paused, and how the option interacts with the existing confirm flow.
8. WHERE the Interrupt_Option is enabled, THE backend SHALL expose the same HTTP endpoints, request fields, and response fields as when the Interrupt_Option is disabled, and SHALL trigger resumption only through an internal, non-HTTP interface.
9. IF a resume is requested for a thread id that has no paused Graph_State in the Checkpointer (unknown, already resumed, or completed), THEN THE Pipeline_Graph SHALL reject the resume with an error indicating no paused run exists, run no Pipeline_Node, and leave every session's Graph_State unchanged.
10. IF the Interrupt_Option setting has a value that is not a recognized boolean value, THEN THE backend SHALL refuse to start and report a configuration error naming the Interrupt_Option setting (raised as `ConfigError`, consistent with the existing `config.py` startup validation), without printing any other configuration value, including the LLM API key.

### Requirement 10: Tests

**User Story:** As a backend maintainer, I want the existing suite to pass and new topology tests to guard the graph, so that regressions in ordering or error routing are caught.

#### Acceptance Criteria

1. WHEN `cd backend && .venv/bin/python -m pytest -q` runs after the migration, THE test suite SHALL report every pre-existing test (71 collected and passing at the time of writing) as passed, with zero pre-existing tests skipped, deselected, marked expected-failure, or changed in their assertions.
2. THE Topology_Tests SHALL assert that the compiled Pipeline_Graph contains exactly five Pipeline_Nodes, named `extractor`, `auditor`, `rights_checker`, `skeptic`, and `writer`, with no other nodes besides the graph's start and end markers, and that the entry node is `extractor`.
3. WHEN a Stub_Provider and injected knowledge base produce a successful run, THE Topology_Tests SHALL assert that the Pipeline_Nodes execute exactly once each in the order `extractor` → `auditor` → `rights_checker` → `skeptic` → `writer` → `END`, that all five Pipeline_Nodes end in `COMPLETED`, and that `error` equals `None`.
4. WHEN a Node_Fatal_Error is induced in each of `extractor`, `auditor`, `skeptic`, and `writer` (one test case per Pipeline_Node; a Stub_Provider induces the Extractor and Writer failures, and a monkeypatched Node_Function induces the Auditor and Skeptic failures because those nodes make no provider calls), THE Topology_Tests SHALL assert that the Pipeline_Graph routes to `END`, that the failing Pipeline_Node is `ERROR`, that every later Pipeline_Node is `PENDING`, that every earlier Pipeline_Node remains `COMPLETED`, that `error.failedNode` equals the failing node name, and that later Pipeline_Nodes made zero provider calls.
5. WHEN an injected knowledge base induces a Node_Local_Failure in `rights_checker` (one test case each for retrieval error, empty result, timeout, and unexpected exception), THE Topology_Tests SHALL assert that the Pipeline_Graph routes to `skeptic`, that `rights_status` equals `UNAVAILABLE`, that `rights_checker` is `COMPLETED`, that `error` equals `None`, and that `skeptic` and `writer` both end in `COMPLETED`.
6. THE new tests SHALL include a Hypothesis property test of the Event_Sequence equivalence in Requirement 7, criterion 7, that draws from all four scenarios (success, Rights_Checker failure, Extractor failure, Writer failure) using Stub_Providers, runs with at least 50 examples, and fails on the first Event_Sequence that differs from the Pre_Migration_Runner's sequence.
7. THE new tests SHALL use only Stub_Providers and injected knowledge bases, and SHALL make zero calls to a real LLM or network endpoint, including when `backend/.env` contains a valid `LLM_API_KEY`.
8. THE new tests SHALL live next to the code as `backend/pipeline/*_test.py` files and SHALL be collected by the command in criterion 1.
9. WHERE the Interrupt_Option is implemented, THE new tests SHALL assert that, with the Interrupt_Option enabled, the Pipeline_Graph pauses with `writer` not yet `COMPLETED`, that after resume with the same thread id the Pipeline_Graph runs `writer` to `END` with a non-null `result`, and that, with the Interrupt_Option disabled, the Pipeline_Graph runs to `END` without pausing.
