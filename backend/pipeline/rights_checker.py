"""Rights_Checker node: BM25 RAG over the MA patient-rights corpus (Req 5).

The Rights_Checker runs after the Auditor. It builds a free-text query from the
:class:`ExtractedBill` and the auditor :class:`Finding` set, retrieves up to 10
reference passages from the BM25 :class:`~rag.knowledge_base.KnowledgeBase`
within a 15-second budget (Req 5.1), normalizes each passage's raw BM25 score to
a ``Relevance_Score`` in [0, 1], and records a rights-issue :class:`Finding`
citing any passage whose ``Relevance_Score >= 0.7`` (Req 5.2, 5.3). Passages
below the threshold are omitted (Req 5.4). On any failure, empty result, or
timeout it records NO rights Findings, returns the ``UNAVAILABLE`` status, and
PRESERVES the auditor Findings (Req 5.5).

No LLM is used
--------------
Retrieval and scoring are purely lexical (BM25) and deterministic, so this node
never calls the LLM provider — keeping Req 5 scoring testable and reproducible.
Confidence is derived deterministically from the relevance score (see below).
The LLM API key is never read, logged, or stored here.

Query construction (documented)
-------------------------------
:func:`build_query` concatenates, in order:

* the provider name (if present),
* each line item's description and billing code (if present),
* each auditor Finding's type (humanized, e.g. "duplicate charge") and its
  description.

This drives retrieval by what the bill actually contains AND by what the auditor
already flagged as suspicious, so a bill with duplicate charges or an ER visit
surfaces the matching rights passages. The query is plain text; the
KnowledgeBase applies the same tokenizer used to index the corpus.

Normalization (documented, deterministic): bounded saturation
--------------------------------------------------------------
Raw BM25 scores are unbounded non-negative floats whose scale depends on the
corpus and query length, so a *relative* "divide by the max" scheme would force
the top hit to exactly 1.0 and make the ``>= 0.7`` boundary degenerate. Instead
:func:`normalize_score` maps a raw score ``s >= 0`` through a bounded, monotonic
saturating function::

    relevance = s / (s + SATURATION_K)

with ``SATURATION_K`` a fixed positive constant. This is order-preserving (a
higher raw score always yields a higher relevance), maps ``0 -> 0`` and
``+inf -> 1`` while never reaching exactly 1, and makes ``relevance`` a smooth,
meaningful quantity on which the ``>= 0.7`` cut is a genuine, testable boundary.
Solving ``s / (s + K) = 0.7`` gives ``s = 0.7/0.3 * K ≈ 2.333 * K``: any passage
whose raw BM25 score is at least that crosses the threshold. ``SATURATION_K`` is
chosen so that a solid lexical match against this corpus clears 0.7 while
weak/incidental matches stay below it. A non-finite or non-positive raw score
normalizes to ``0.0``.

Thresholding (documented): :func:`is_relevant` is the single, pure decision
``relevance_score >= Decimal("0.7")`` (inclusive). Normalization and
thresholding are kept as separate pure steps so the boundary semantics stay
crisp (Property 9, task 12.4).

Confidence (documented): a recorded rights-issue Finding's ``confidence`` is set
equal to its passage's ``Relevance_Score``. Because the relevance score is in
[0, 1) by construction, the confidence is always within the [0, 1] range the
model requires (Req 5.3), and a more relevant passage yields a more confident
Finding.
"""

from __future__ import annotations

import concurrent.futures
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional, Protocol

from models import ExtractedBill, Finding, FindingType, Passage, RightsStatus

# Node identifier. Mirrors ``config.NodeName.RIGHTS_CHECKER`` ("rights_checker");
# kept as a plain string so this module imports without a hard config dependency.
NODE_NAME = "rights_checker"

# Maximum passages to retrieve per the design/Req 5.1.
MAX_PASSAGES: int = 10

# Retrieval time budget in seconds (Req 5.1). BM25 over a tiny corpus is
# effectively instantaneous; the budget guards against a pathological/blocked
# knowledge base so a hang degrades to the "unavailable" branch (Req 5.5).
RETRIEVAL_TIMEOUT_S: float = 15.0

# Relevance threshold (inclusive). A passage is "relevant" iff its normalized
# Relevance_Score is at least this value (Req 5.2, 5.4).
RELEVANCE_THRESHOLD: Decimal = Decimal("0.7")

# Saturation constant for the bounded normalization ``s / (s + K)`` (see module
# docstring). With K = 4.0 the threshold ``relevance >= 0.7`` corresponds to a
# raw BM25 score of ``0.7/0.3 * 4.0 ≈ 9.33`` — a strong multi-term lexical match
# against this corpus clears it, while incidental single-term overlaps stay
# below. Tuned against the curated MA patient-rights corpus in rag/corpus/.
SATURATION_K: float = 4.0

# Quantization for the normalized score: 4 decimal places keeps the mapping
# stable and reproducible while leaving ample resolution around the 0.7 cut.
_SCORE_QUANT = Decimal("0.0001")


class SearchResultLike(Protocol):
    """Structural type for a BM25 search result (see ``rag.knowledge_base``)."""

    id: str
    text: str
    source: str
    score: float


class KnowledgeBaseLike(Protocol):
    """Structural type for the injected knowledge base.

    Only :meth:`search` is required. Accepting the KB by protocol keeps the node
    testable with a stub (e.g. one whose ``search`` raises, to exercise Req 5.5)
    and lets the pipeline inject a single shared, pre-built index.
    """

    def search(self, query: str, k: int = ...) -> list[SearchResultLike]: ...


def _humanize_finding_type(finding_type: FindingType) -> str:
    """Render a FindingType as query-friendly words (e.g. "duplicate charge")."""
    return finding_type.value.replace("_", " ")


def build_query(bill: ExtractedBill, auditor_findings: list[Finding]) -> str:
    """Build the retrieval query from the bill and auditor Findings (Req 5.1).

    Concatenates provider name, per-line descriptions and billing codes, and
    each auditor Finding's humanized type and description into a single
    whitespace-joined string. Ordering is deterministic (bill order, then
    finding order). See the module docstring for the rationale.

    Args:
        bill: The structured bill.
        auditor_findings: The Findings already produced by the Auditor.

    Returns:
        A plain-text query string (possibly empty if the bill is empty and there
        are no findings).
    """
    parts: list[str] = []

    if bill.provider_name:
        parts.append(bill.provider_name)

    for line in bill.line_items:
        if line.description:
            parts.append(line.description)
        if line.billing_code:
            parts.append(line.billing_code)

    for finding in auditor_findings:
        parts.append(_humanize_finding_type(finding.type))
        if finding.description:
            parts.append(finding.description)

    return " ".join(part.strip() for part in parts if part and part.strip())


def normalize_score(raw_score: float) -> Decimal:
    """Normalize a raw BM25 score to a ``Relevance_Score`` in [0, 1].

    Applies the bounded saturating map ``s / (s + SATURATION_K)`` for ``s > 0``
    and returns ``0.0`` for a non-positive or non-finite score. The result is
    monotonic in ``raw_score``, lies in ``[0, 1)``, and is quantized to four
    decimal places for reproducibility. See the module docstring for why this
    scheme is preferred over relative/max normalization.

    Args:
        raw_score: The raw BM25 score (non-negative; higher is more relevant).

    Returns:
        The normalized relevance as a :class:`~decimal.Decimal` in [0, 1].
    """
    # Guard non-finite / non-positive scores -> no relevance.
    if not (raw_score == raw_score) or raw_score in (float("inf"), float("-inf")):
        return Decimal("0")
    if raw_score <= 0.0:
        return Decimal("0")

    relevance = raw_score / (raw_score + SATURATION_K)
    return Decimal(str(relevance)).quantize(_SCORE_QUANT, rounding=ROUND_HALF_UP)


def is_relevant(relevance_score: Decimal) -> bool:
    """Return whether a normalized relevance clears the threshold (Req 5.2, 5.4).

    The single, pure threshold decision: ``relevance_score >= 0.7`` (inclusive).
    A passage exactly at ``0.7`` IS relevant.

    Args:
        relevance_score: A normalized relevance in [0, 1].

    Returns:
        ``True`` iff ``relevance_score >= 0.7``.
    """
    return relevance_score >= RELEVANCE_THRESHOLD


def _rights_finding_for(passage: Passage) -> Finding:
    """Construct a rights-issue Finding citing ``passage`` (Req 5.2, 5.3).

    The confidence is set equal to the passage's relevance score, which is in
    [0, 1) by construction, satisfying the [0, 1] constraint (Req 5.3). The
    Finding cites the passage via ``passage_ids`` and carries no line-item
    references (the rights issue is grounded in the reference passage, not a
    specific line); the Skeptic's evidence rule (Req 6.3) is satisfied by the
    passage reference.
    """
    return Finding(
        id=f"rights_issue:{passage.id}",
        type=FindingType.RIGHTS_ISSUE,
        description=(
            f"A potential patient-rights issue is supported by reference "
            f"passage '{passage.id}' from {passage.source}: {passage.text}"
        ),
        confidence=passage.relevance_score,
        passage_ids=[passage.id],
    )


def _retrieve(kb: KnowledgeBaseLike, query: str) -> list[SearchResultLike]:
    """Run ``kb.search`` under the retrieval time budget (Req 5.1, 5.5).

    Executes the search on a worker thread and waits at most
    :data:`RETRIEVAL_TIMEOUT_S`. A timeout or any exception propagates to the
    caller, which maps it to the "unavailable" branch. BM25 over a small corpus
    returns well within the budget; the timeout only guards a pathological hang.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(kb.search, query, MAX_PASSAGES)
        # Raises concurrent.futures.TimeoutError on budget overrun; any
        # exception raised inside kb.search re-raises here.
        return future.result(timeout=RETRIEVAL_TIMEOUT_S)


def check_rights(
    bill: ExtractedBill,
    auditor_findings: list[Finding],
    kb: KnowledgeBaseLike,
) -> tuple[list[Finding], RightsStatus]:
    """Retrieve rights passages and produce rights-issue Findings (Req 5).

    Pure-ish testable core: given a bill, the auditor Findings and a knowledge
    base, it returns ``(rights_findings, status)``. It does NOT mutate the
    auditor Findings and does NOT include them in the returned list — the caller
    (the node entry point) appends the rights Findings to the existing set,
    preserving the auditor Findings (Req 5.5).

    Behavior:
    * Builds a query (:func:`build_query`), retrieves up to 10 passages within
      the 15s budget (:func:`_retrieve`), normalizes each raw score
      (:func:`normalize_score`), and records a rights-issue Finding for each
      passage that :func:`is_relevant` (Req 5.2); below-threshold passages are
      omitted (Req 5.4).
    * On ANY failure, empty query, empty result, or timeout: returns
      ``([], RightsStatus.UNAVAILABLE)`` — no rights Findings (Req 5.5).

    Args:
        bill: The structured bill.
        auditor_findings: The Findings already produced by the Auditor.
        kb: The BM25 knowledge base (injected).

    Returns:
        ``(rights_findings, status)`` where ``status`` is
        :data:`RightsStatus.COMPLETED` on a successful retrieval (even if it
        yields zero relevant passages) or :data:`RightsStatus.UNAVAILABLE` on
        failure/empty/timeout.
    """
    query = build_query(bill, auditor_findings)
    if not query.strip():
        # Nothing to retrieve against -> treat as an unavailable rights check so
        # no rights Findings are fabricated and auditor Findings are preserved.
        return [], RightsStatus.UNAVAILABLE

    try:
        results = _retrieve(kb, query)
    except Exception:
        # Failure or timeout (Req 5.5): no rights Findings, unavailable status.
        return [], RightsStatus.UNAVAILABLE

    if not results:
        # Empty retrieval (Req 5.5).
        return [], RightsStatus.UNAVAILABLE

    rights_findings: list[Finding] = []
    for result in results:
        relevance = normalize_score(result.score)
        if not is_relevant(relevance):
            continue  # below threshold -> omit (Req 5.4)
        passage = Passage(
            id=result.id,
            text=result.text,
            source=result.source,
            relevance_score=relevance,
        )
        rights_findings.append(_rights_finding_for(passage))

    return rights_findings, RightsStatus.COMPLETED


def run_rights_checker(state: Any, kb: KnowledgeBaseLike) -> Any:
    """Node entry point: update ``PipelineState`` with rights Findings (Req 5).

    Runs :func:`check_rights` against the state's bill and existing (auditor)
    Findings, then:

    * APPENDS the resulting rights Findings to ``state.findings`` (auditor
      Findings are preserved and never removed, Req 5.5), and
    * sets ``state.rights_status`` to the returned status.

    This matches the graph's node convention ``(state, ...) -> state`` so task
    12.3 can wire it between the Auditor and Writer with no change to this
    contract. It assumes the Extractor/Auditor ran first (``state.bill`` set); if
    ``state.bill`` is ``None`` it records no rights Findings and marks the rights
    check unavailable, preserving whatever Findings are present.

    Args:
        state: The shared ``PipelineState`` (mutated in place).
        kb: The BM25 knowledge base (injected).

    Returns:
        The same ``state`` instance.
    """
    bill: Optional[ExtractedBill] = getattr(state, "bill", None)
    auditor_findings: list[Finding] = list(getattr(state, "findings", []) or [])

    if bill is None:
        # No bill to query from: preserve existing Findings, mark unavailable.
        state.rights_status = RightsStatus.UNAVAILABLE
        return state

    rights_findings, status = check_rights(bill, auditor_findings, kb)

    # Append rights Findings to the existing (auditor) set — never remove them.
    state.findings = auditor_findings + rights_findings
    state.rights_status = status
    return state
