"""Shared test fixtures for the LangGraph migration tests (TESTS ONLY).

langgraph-migration spec, task 2.1. Production code must never import this
module; the leading underscore keeps pytest from collecting it.

What lives here
---------------
* :func:`synthetic_bills` - Hypothesis strategy producing :class:`BillCase`
  values (fake PDF text + the stub Extractor JSON for that text). Synthetic
  data only: invented providers, invented account numbers, no PHI.
* :func:`eval_bill_cases` - the 10 synthetic eval-set bills as ``BillCase``
  values (rendered from ``eval/eval_set/bills_data.py``; no PDF parsing), for
  ``@example`` / ``pytest.mark.parametrize`` use. :func:`bill_cases` mixes both.
* :func:`scenarios` - Hypothesis strategy over :class:`ScenarioKind`
  (success, six Rights_Checker failure modes, Extractor / Auditor / Skeptic /
  Writer failures) with arbitrary exception messages.
* :class:`StubProvider` - scripted per-node ``LLMProvider`` double that records
  every call together with the node that was RUNNING at call time, and carries
  a sentinel ``api_key`` plus optional sentinel body text.
* :class:`StubKB` - knowledge-base double with modes ``match``, ``empty``,
  ``raise``, ``slow``.
* pytest fixtures (:func:`hermetic` is autouse once imported into a test
  module) and equivalent context managers for use inside Hypothesis examples.
* :func:`run_both` - runs ``pipeline.graph.run_pipeline`` and
  ``pipeline._reference_runner.run_pipeline_reference`` on fresh, identical
  inputs and records the ``(node, status)`` progress transitions of each.

Nothing here calls a real LLM or opens a network connection.
"""

from __future__ import annotations

import contextlib
import json
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from functools import lru_cache
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence, Union

import pytest
from hypothesis import strategies as st

import pipeline._reference_runner as reference_mod
import pipeline.extractor as extractor_mod
import pipeline.graph as graph_mod
import pipeline.rights_checker as rights_checker_mod
from models import SupportedLanguage
from pipeline.state import NodeStatus, PipelineState

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Env vars that would enable LangSmith tracing (and network calls) if set.
LANGSMITH_ENV_VARS: tuple[str, ...] = (
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_API_KEY",
)

#: Lowered Rights_Checker retrieval budget for the ``slow`` KB mode.
FAST_RETRIEVAL_TIMEOUT_S: float = 0.02

#: How long a ``slow`` StubKB blocks (comfortably past the lowered budget).
SLOW_KB_DELAY_S: float = 0.1

#: Canned Writer completion (non-empty so the Writer succeeds).
WRITER_TEXT = "This charge looks like a billing error you can ask the billing office to review."

_TWO_PLACES = Decimal("0.01")
_MIN_MONEY = Decimal("0.01")
_MAX_MONEY = Decimal("99999.99")

# Small pools so duplicates (same code + description) happen often. Mix of
# recognized codes, unrecognized codes, and a missing code.
CODE_POOL: tuple[Optional[str], ...] = ("99213", "85025", "36415", "J1885", "88888", "ZZ999", None)
DESCRIPTION_POOL: tuple[str, ...] = (
    "Office visit",
    "Blood panel",
    "Routine venipuncture",
    "Injection",
    "Supply charge",
)
PROVIDER_POOL: tuple[Optional[str], ...] = (
    "Synthetic General Hospital",
    "Example Community Clinic",
    None,
)

_ALL_LANGUAGES: tuple[SupportedLanguage, ...] = tuple(SupportedLanguage)

# Real functions captured at import so patches can delegate to them.
_REAL_READ_PDF_TEXT = extractor_mod.read_pdf_text
_REAL_RUN_RIGHTS_CHECKER = rights_checker_mod.run_rights_checker
_REAL_SOCKET_CONNECT = socket.socket.connect
_REAL_SOCKET_CONNECT_EX = socket.socket.connect_ex


# ---------------------------------------------------------------------------
# Bills
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakePdf:
    """Stand-in ``pdf_source``: the patched ``read_pdf_text`` returns ``text``."""

    text: str


@dataclass(frozen=True)
class BillCase:
    """One synthetic bill: the fake PDF text and the stub Extractor JSON."""

    name: str
    pdf_text: str
    extractor_json: str

    def pdf(self) -> FakePdf:
        return FakePdf(self.pdf_text)


def _money_str(value: Optional[Decimal]) -> Optional[str]:
    return None if value is None else str(value.quantize(_TWO_PLACES))


def _render_pdf_text(payload: Mapping[str, Any]) -> str:
    """Render a bill payload as plain synthetic "PDF" text."""
    lines = ["*** SYNTHETIC TEST BILL - NOT REAL PATIENT DATA ***"]
    if payload.get("provider_name"):
        lines.append(f"PROVIDER: {payload['provider_name']}")
    lines.append(f"ACCOUNT: {payload.get('account_number') or 'NONE'}")
    for item in payload["line_items"]:
        lines.append(
            f"LINE {item['id']} | CODE {item['billing_code'] or 'NONE'} | "
            f"{item['description']} | QTY {item['quantity'] or '-'} | "
            f"UNIT {item['unit_price'] or '-'} | AMT {item['amount'] or '-'}"
        )
    lines.append(f"TOTAL: {payload.get('stated_total') or 'NONE'}")
    return "\n".join(lines)


def make_bill_case(name: str, payload: Mapping[str, Any]) -> BillCase:
    """Build a :class:`BillCase` from an ``ExtractedBill``-shaped payload."""
    return BillCase(
        name=name,
        pdf_text=_render_pdf_text(payload),
        extractor_json=json.dumps(payload),
    )


_money = st.decimals(
    min_value=_MIN_MONEY,
    max_value=_MAX_MONEY,
    places=2,
    allow_nan=False,
    allow_infinity=False,
)


@st.composite
def _line_item(draw: st.DrawFn, index: int) -> dict:
    code = draw(st.sampled_from(CODE_POOL))
    description = draw(st.sampled_from(DESCRIPTION_POOL))
    quantity = draw(st.one_of(st.none(), st.integers(min_value=1, max_value=5)))
    unit_price = draw(st.one_of(st.none(), _money))
    amount_mode = draw(st.sampled_from(("consistent", "arbitrary", "missing")))
    amount: Optional[Decimal]
    if amount_mode == "missing":
        amount = None
    elif amount_mode == "consistent" and quantity is not None and unit_price is not None:
        expected = (unit_price * quantity).quantize(_TWO_PLACES)
        amount = expected if expected <= _MAX_MONEY else draw(_money)
    else:
        amount = draw(_money)
    return {
        "id": f"L{index}",
        "description": description,
        "billing_code": code,
        "quantity": quantity,
        "unit_price": _money_str(unit_price),
        "amount": _money_str(amount),
    }


@st.composite
def synthetic_bills(draw: st.DrawFn) -> BillCase:
    """Hypothesis strategy for synthetic bills.

    1-12 line items, codes/descriptions from small pools (duplicates occur),
    two-decimal ``Decimal`` amounts in ``[0.01, 99999.99]`` (serialized as
    strings so the Extractor parses them into ``Decimal``, never ``float``),
    and a stated total that matches, mismatches, or is absent.
    """
    count = draw(st.integers(min_value=1, max_value=12))
    items = [draw(_line_item(i)) for i in range(1, count + 1)]
    computed = sum(
        (Decimal(item["amount"]) for item in items if item["amount"] is not None),
        Decimal("0.00"),
    )
    total_mode = draw(st.sampled_from(("match", "mismatch", "absent")))
    if total_mode == "match":
        stated: Optional[Decimal] = computed
    elif total_mode == "mismatch":
        stated = computed + draw(_money)
    else:
        stated = None
    payload = {
        "provider_name": draw(st.sampled_from(PROVIDER_POOL)),
        "provider_address": None,
        "account_number": f"SYN-{draw(st.integers(min_value=0, max_value=9999)):04d}",
        "line_items": items,
        "stated_total": _money_str(stated),
    }
    return make_bill_case("hypothesis", payload)


@lru_cache(maxsize=1)
def eval_bill_cases() -> tuple[BillCase, ...]:
    """The 10 synthetic eval-set bills as :class:`BillCase` values.

    Rendered from the canonical ``bills_data.BILLS`` (the same source the eval
    PDFs are generated from) and structured with the eval harness's
    deterministic parser, so no PDF is read and no LLM is involved.
    """
    from eval.eval_set.bills_data import BILLS, render_lines
    from eval.harness import parse_eval_bill_text

    cases = []
    for spec in BILLS:
        text = "\n".join(render_lines(spec))
        cases.append(
            BillCase(
                name=spec.id,
                pdf_text=text,
                extractor_json=json.dumps(parse_eval_bill_text(text)),
            )
        )
    return tuple(cases)


def bill_cases() -> st.SearchStrategy[BillCase]:
    """Eval-set bills and Hypothesis-generated bills, mixed."""
    return st.one_of(st.sampled_from(eval_bill_cases()), synthetic_bills())


def languages() -> st.SearchStrategy[SupportedLanguage]:
    return st.sampled_from(_ALL_LANGUAGES)


#: Bill whose Rights_Checker query is empty: no provider, no lines, and a
#: matching 0.00 total so the Auditor produces no Findings.
EMPTY_QUERY_CASE = make_bill_case(
    "empty-query",
    {
        "provider_name": None,
        "provider_address": None,
        "account_number": None,
        "line_items": [],
        "stated_total": "0.00",
    },
)


# ---------------------------------------------------------------------------
# Stub provider
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Respond:
    """Scripted provider action: return ``text`` (plus body sentinel if asked)."""

    text: str
    include_body: bool = False


@dataclass(frozen=True)
class Raise:
    """Scripted provider action: raise ``exc_type(message)``."""

    exc_type: type[BaseException] = RuntimeError
    message: str = "provider failure"
    include_body: bool = False


Action = Union[Respond, Raise, Callable[[str, str], str]]


@dataclass(frozen=True)
class ProviderCall:
    """One recorded provider call."""

    node: str
    running_node: Optional[str]  # node RUNNING on the watched state at call time
    system: str
    user: str


class StubProvider:
    """Deterministic, scripted ``LLMProvider`` double (no network, no secrets).

    ``script`` maps a node name (``"extractor"``, ``"writer"``) to a
    :class:`Respond`, a :class:`Raise`, or a ``callable(system, user) -> str``.
    A call from an unscripted node is recorded in ``unexpected_calls`` and
    raises ``AssertionError``.

    ``api_key`` is a random sentinel the provider "holds" (tests check it never
    reaches state or errors). ``body_sentinel`` is optional text embedded in
    responses / exception messages whose action sets ``include_body=True``.
    Call :meth:`watch` with the live ``PipelineState`` to record which node was
    RUNNING at each call.
    """

    def __init__(
        self,
        script: Mapping[str, Action],
        *,
        api_key: Optional[str] = None,
        body_sentinel: Optional[str] = None,
    ) -> None:
        self.script = dict(script)
        self.api_key = api_key or f"sk-test-sentinel-{secrets.token_hex(8)}"
        self.body_sentinel = body_sentinel
        self.calls: list[ProviderCall] = []
        self.unexpected_calls: list[str] = []
        self._watched: Optional[PipelineState] = None
        self._lock = threading.Lock()

    def watch(self, state: PipelineState) -> None:
        self._watched = state

    def _running_node(self) -> Optional[str]:
        if self._watched is None:
            return None
        running = [
            name
            for name, status in self._watched.node_status.items()
            if status == NodeStatus.RUNNING
        ]
        return running[0] if len(running) == 1 else None

    def calls_for(self, node: str) -> list[ProviderCall]:
        return [c for c in self.calls if c.node == node]

    def complete(self, *, system: str, user: str, timeout_s: float, node: Any) -> str:
        node_id = str(getattr(node, "value", node))
        with self._lock:
            self.calls.append(ProviderCall(node_id, self._running_node(), system, user))
        action = self.script.get(node_id)
        if action is None:
            self.unexpected_calls.append(node_id)
            raise AssertionError(f"unscripted provider call from node {node_id!r}")
        if isinstance(action, Respond):
            return self._with_body(action.text, action.include_body)
        if isinstance(action, Raise):
            raise action.exc_type(self._with_body(action.message, action.include_body))
        return action(system, user)

    def _with_body(self, text: str, include: bool) -> str:
        if include and self.body_sentinel:
            return f"{text} {self.body_sentinel}"
        return text


# ---------------------------------------------------------------------------
# Stub knowledge base
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StubPassage:
    """Structural stand-in for ``rag.knowledge_base.SearchResult``."""

    id: str
    score: float
    text: str = "Patients may ask the billing office to review and correct billing errors."
    source: str = "Synthetic MA Patient Rights Reference"
    title: str = ""


#: Default passages: one clears the 0.7 relevance cut (score 100 -> ~0.96),
#: one does not (score 1 -> 0.2).
DEFAULT_PASSAGES: tuple[StubPassage, ...] = (
    StubPassage(id="ma-duplicate-charges", score=100.0, title="Duplicate charges"),
    StubPassage(id="ma-itemized-bill", score=1.0, title="Itemized bill"),
)


class KBMode(str, Enum):
    MATCH = "match"
    EMPTY = "empty"
    RAISE = "raise"
    SLOW = "slow"


class StubKB:
    """Knowledge-base double. Build with :meth:`match`, :meth:`empty`,
    :meth:`raising`, or :meth:`slow`. Records every query in ``queries``."""

    def __init__(
        self,
        mode: KBMode,
        passages: Sequence[StubPassage] = (),
        *,
        message: str = "retrieval backend unavailable",
        delay_s: float = SLOW_KB_DELAY_S,
    ) -> None:
        self.mode = mode
        self.passages = tuple(passages)
        self.message = message
        self.delay_s = delay_s
        self.queries: list[str] = []

    @classmethod
    def match(cls, passages: Sequence[StubPassage] = DEFAULT_PASSAGES) -> "StubKB":
        return cls(KBMode.MATCH, passages)

    @classmethod
    def empty(cls) -> "StubKB":
        return cls(KBMode.EMPTY)

    @classmethod
    def raising(cls, message: str = "retrieval backend unavailable") -> "StubKB":
        return cls(KBMode.RAISE, message=message)

    @classmethod
    def slow(cls, delay_s: float = SLOW_KB_DELAY_S) -> "StubKB":
        """Blocks ``delay_s``; pair with :data:`FAST_RETRIEVAL_TIMEOUT_S`."""
        return cls(KBMode.SLOW, DEFAULT_PASSAGES, delay_s=delay_s)

    def search(self, query: str, k: int = 10) -> list[StubPassage]:
        self.queries.append(query)
        if self.mode is KBMode.RAISE:
            raise RuntimeError(self.message)
        if self.mode is KBMode.EMPTY:
            return []
        if self.mode is KBMode.SLOW:
            time.sleep(self.delay_s)
        return list(self.passages[:k])


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


class ScenarioKind(str, Enum):
    SUCCESS = "success"
    RC_RAISE = "rights_checker_raise"  # KB.search raises
    RC_EMPTY = "rights_checker_empty"  # KB returns no passages
    RC_TIMEOUT = "rights_checker_timeout"  # KB exceeds the (lowered) budget
    RC_UNEXPECTED = "rights_checker_unexpected"  # run_rights_checker itself raises
    RC_EMPTY_QUERY = "rights_checker_empty_query"  # query built from the bill is empty
    RC_MISSING_BILL = "rights_checker_missing_bill"  # no bill visible to the node
    EXTRACTOR_FAIL = "extractor_fail"
    AUDITOR_FAIL = "auditor_fail"
    SKEPTIC_FAIL = "skeptic_fail"
    WRITER_FAIL = "writer_fail"


RIGHTS_CHECKER_FAILURES: tuple[ScenarioKind, ...] = (
    ScenarioKind.RC_RAISE,
    ScenarioKind.RC_EMPTY,
    ScenarioKind.RC_TIMEOUT,
    ScenarioKind.RC_UNEXPECTED,
    ScenarioKind.RC_EMPTY_QUERY,
    ScenarioKind.RC_MISSING_BILL,
)
FATAL_FAILURES: tuple[ScenarioKind, ...] = (
    ScenarioKind.EXTRACTOR_FAIL,
    ScenarioKind.AUDITOR_FAIL,
    ScenarioKind.SKEPTIC_FAIL,
    ScenarioKind.WRITER_FAIL,
)

#: Node that fails for each fatal scenario.
FAILING_NODE: dict[ScenarioKind, str] = {
    ScenarioKind.EXTRACTOR_FAIL: "extractor",
    ScenarioKind.AUDITOR_FAIL: "auditor",
    ScenarioKind.SKEPTIC_FAIL: "skeptic",
    ScenarioKind.WRITER_FAIL: "writer",
}

#: Failure variants per scenario (first entry is the default).
VARIANTS: dict[ScenarioKind, tuple[str, ...]] = {
    # invalid_json / timeout -> typed ExtractorError; raise -> generic str(exc);
    # empty_text -> ExtractorError before any provider call.
    ScenarioKind.EXTRACTOR_FAIL: ("invalid_json", "timeout", "raise", "empty_text"),
    # all variants surface as WriterError.
    ScenarioKind.WRITER_FAIL: ("raise", "empty", "timeout"),
}


@dataclass(frozen=True)
class Scenario:
    """A pipeline scenario plus the message used for any injected exception."""

    kind: ScenarioKind
    message: str = "injected failure"
    variant: str = ""

    @property
    def resolved_variant(self) -> str:
        return self.variant or VARIANTS.get(self.kind, ("",))[0]


@st.composite
def scenarios(
    draw: st.DrawFn, kinds: Optional[Sequence[ScenarioKind]] = None
) -> Scenario:
    """Strategy over scenarios (default: every :class:`ScenarioKind`)."""
    kind = draw(st.sampled_from(tuple(kinds) if kinds else tuple(ScenarioKind)))
    message = draw(st.text(max_size=40))
    variants = VARIANTS.get(kind)
    variant = draw(st.sampled_from(variants)) if variants else ""
    return Scenario(kind=kind, message=message, variant=variant)


def _extractor_action(case: BillCase, scenario: Scenario) -> Action:
    if scenario.kind is ScenarioKind.EXTRACTOR_FAIL:
        variant = scenario.resolved_variant
        if variant == "invalid_json":
            return Respond("this is not json")
        if variant == "timeout":
            return Raise(TimeoutError, scenario.message)
        if variant == "raise":
            return Raise(RuntimeError, scenario.message)
    return Respond(case.extractor_json)


def _writer_action(scenario: Scenario) -> Action:
    if scenario.kind is ScenarioKind.WRITER_FAIL:
        variant = scenario.resolved_variant
        if variant == "empty":
            return Respond("   ")
        if variant == "timeout":
            return Raise(TimeoutError, scenario.message)
        return Raise(RuntimeError, scenario.message)
    return Respond(WRITER_TEXT)


def effective_case(case: BillCase, scenario: Scenario) -> BillCase:
    """The bill actually used for ``scenario`` (some scenarios override it)."""
    if scenario.kind is ScenarioKind.RC_EMPTY_QUERY:
        return EMPTY_QUERY_CASE
    if (
        scenario.kind is ScenarioKind.EXTRACTOR_FAIL
        and scenario.resolved_variant == "empty_text"
    ):
        return BillCase(case.name + ":blank", "   \n  ", case.extractor_json)
    return case


def make_provider(case: BillCase, scenario: Scenario, **kwargs: Any) -> StubProvider:
    """A fresh :class:`StubProvider` scripted for ``scenario``."""
    case = effective_case(case, scenario)
    return StubProvider(
        {"extractor": _extractor_action(case, scenario), "writer": _writer_action(scenario)},
        **kwargs,
    )


def make_kb(scenario: Scenario) -> StubKB:
    """A fresh :class:`StubKB` for ``scenario``."""
    if scenario.kind is ScenarioKind.RC_RAISE:
        return StubKB.raising(scenario.message)
    if scenario.kind is ScenarioKind.RC_EMPTY:
        return StubKB.empty()
    if scenario.kind is ScenarioKind.RC_TIMEOUT:
        return StubKB.slow()
    return StubKB.match()


# ---------------------------------------------------------------------------
# Patching helpers (context managers usable inside Hypothesis examples)
# ---------------------------------------------------------------------------


class NetworkBlockedError(RuntimeError):
    """Raised when a test tries to open an IP network connection."""


def fake_read_pdf_text(pdf_source: Any) -> str:
    """``read_pdf_text`` replacement: ``FakePdf`` -> its text, else the real reader."""
    if isinstance(pdf_source, FakePdf):
        return pdf_source.text
    return _REAL_READ_PDF_TEXT(pdf_source)


def _is_ip_socket(sock: socket.socket) -> bool:
    return sock.family in (socket.AF_INET, socket.AF_INET6)


def _blocked_connect(self: socket.socket, address: Any, *args: Any) -> Any:
    if _is_ip_socket(self):
        raise NetworkBlockedError(f"network access blocked in tests: {address!r}")
    return _REAL_SOCKET_CONNECT(self, address, *args)


def _blocked_connect_ex(self: socket.socket, address: Any, *args: Any) -> Any:
    if _is_ip_socket(self):
        raise NetworkBlockedError(f"network access blocked in tests: {address!r}")
    return _REAL_SOCKET_CONNECT_EX(self, address, *args)


@contextlib.contextmanager
def patched(targets: Sequence[tuple[Any, str, Any]]) -> Iterator[None]:
    """Set ``(obj, attr, value)`` triples, restoring the originals on exit.

    Every attribute must already exist (catches drift such as a rename in
    ``pipeline.graph``).
    """
    originals: list[tuple[Any, str, Any]] = []
    try:
        for obj, name, value in targets:
            if not hasattr(obj, name):
                raise AttributeError(f"{obj!r} has no attribute {name!r} to patch")
            originals.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)
        yield
    finally:
        for obj, name, value in reversed(originals):
            setattr(obj, name, value)


@contextlib.contextmanager
def hermetic_environment() -> Iterator[None]:
    """Non-pytest equivalent of :func:`hermetic` (env, socket, PDF reader)."""
    import os

    saved = {var: os.environ.pop(var) for var in LANGSMITH_ENV_VARS if var in os.environ}
    try:
        with patched(
            [
                (socket.socket, "connect", _blocked_connect),
                (socket.socket, "connect_ex", _blocked_connect_ex),
                (extractor_mod, "read_pdf_text", fake_read_pdf_text),
            ]
        ):
            yield
    finally:
        os.environ.update(saved)


def _raise_message(message: str) -> Callable[..., Any]:
    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(message)

    return _boom


def _run_rights_checker_without_bill(state: Any, kb: Any) -> Any:
    """Call the real ``run_rights_checker`` with ``state.bill`` hidden."""
    bill = state.bill
    state.bill = None
    try:
        return _REAL_RUN_RIGHTS_CHECKER(state, kb)
    finally:
        state.bill = bill


#: Modules whose by-name imports get patched identically for both runners.
RUNNER_MODULES: tuple[Any, ...] = (graph_mod, reference_mod)


def scenario_patches(scenario: Scenario) -> list[tuple[Any, str, Any]]:
    """Attribute patches a scenario needs, applied to BOTH runner modules."""
    if scenario.kind is ScenarioKind.AUDITOR_FAIL:
        name, value = "audit", _raise_message(scenario.message)
    elif scenario.kind is ScenarioKind.SKEPTIC_FAIL:
        name, value = "apply_skeptic", _raise_message(scenario.message)
    elif scenario.kind is ScenarioKind.RC_UNEXPECTED:
        name, value = "run_rights_checker", _raise_message(scenario.message)
    elif scenario.kind is ScenarioKind.RC_MISSING_BILL:
        name, value = "run_rights_checker", _run_rights_checker_without_bill
    else:
        name = ""
    targets = [(mod, name, value) for mod in RUNNER_MODULES] if name else []
    if scenario.kind is ScenarioKind.RC_TIMEOUT:
        targets.append((rights_checker_mod, "RETRIEVAL_TIMEOUT_S", FAST_RETRIEVAL_TIMEOUT_S))
    return targets


@contextlib.contextmanager
def scenario_environment(scenario: Scenario) -> Iterator[None]:
    """Fake PDF reader plus every patch ``scenario`` needs, restored on exit."""
    with patched(
        [(extractor_mod, "read_pdf_text", fake_read_pdf_text), *scenario_patches(scenario)]
    ):
        yield


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Autouse once imported: no LangSmith tracing, no IP sockets, fake PDF reader.

    Self-contained (depends only on ``monkeypatch``) so importing just this
    name into a test module is enough.
    """
    for var in LANGSMITH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_connect_ex)
    monkeypatch.setattr(extractor_mod, "read_pdf_text", fake_read_pdf_text)


@pytest.fixture
def no_langsmith_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in LANGSMITH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_connect_ex)


@pytest.fixture
def fake_pdf_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extractor_mod, "read_pdf_text", fake_read_pdf_text)


@pytest.fixture
def fast_retrieval_timeout(monkeypatch: pytest.MonkeyPatch) -> float:
    monkeypatch.setattr(rights_checker_mod, "RETRIEVAL_TIMEOUT_S", FAST_RETRIEVAL_TIMEOUT_S)
    return FAST_RETRIEVAL_TIMEOUT_S


# ---------------------------------------------------------------------------
# Dual-run helper
# ---------------------------------------------------------------------------

Transition = tuple[str, NodeStatus]
Runner = Callable[..., PipelineState]


@dataclass
class RunRecord:
    """Outcome of one runner call."""

    caller_state: PipelineState  # the instance passed in
    returned: PipelineState  # what the runner returned
    transitions: list[Transition]
    provider: StubProvider
    kb: StubKB


@dataclass
class DualRun:
    new: RunRecord  # pipeline.graph.run_pipeline
    ref: RunRecord  # pipeline._reference_runner.run_pipeline_reference


def fresh_state(
    language: SupportedLanguage = SupportedLanguage.EN, session_id: str = "s-dual"
) -> PipelineState:
    return PipelineState(session_id=session_id, language=language)


def run_one(
    runner: Runner,
    case: BillCase,
    scenario: Scenario,
    language: SupportedLanguage = SupportedLanguage.EN,
    *,
    session_id: str = "s-dual",
    progress_hook: Optional[Callable[[str, NodeStatus, PipelineState], None]] = None,
) -> RunRecord:
    """Run ``runner`` once on fresh inputs for ``(case, scenario, language)``.

    Applies :func:`scenario_environment` for the duration of the call. Each
    transition is recorded as ``(node, status)``; ``progress_hook`` (if given)
    is called afterwards and may raise (the runner must swallow it).
    """
    state = fresh_state(language, session_id)
    provider = make_provider(case, scenario)
    provider.watch(state)
    kb = make_kb(scenario)
    transitions: list[Transition] = []

    def progress(node: str, status: NodeStatus, live: PipelineState) -> None:
        transitions.append((node, status))
        if progress_hook is not None:
            progress_hook(node, status, live)

    with scenario_environment(scenario):
        returned = runner(
            state, provider, effective_case(case, scenario).pdf(), progress, kb=kb
        )
    return RunRecord(state, returned, transitions, provider, kb)


def run_both(
    case: BillCase,
    scenario: Scenario,
    language: SupportedLanguage = SupportedLanguage.EN,
    *,
    session_id: str = "s-dual",
) -> DualRun:
    """Run the current ``run_pipeline`` and the frozen reference on identical inputs."""
    new = run_one(graph_mod.run_pipeline, case, scenario, language, session_id=session_id)
    ref = run_one(
        reference_mod.run_pipeline_reference, case, scenario, language, session_id=session_id
    )
    return DualRun(new=new, ref=ref)
