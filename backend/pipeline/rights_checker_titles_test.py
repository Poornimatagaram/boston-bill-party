"""Rights findings name passages by readable title, not by raw corpus id."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from models import ExtractedBill, LineItem
from pipeline.rights_checker import check_rights
from rag.knowledge_base import KnowledgeBase, humanize_passage_id


@dataclass
class _UntitledResult:
    """A search result without a ``title`` (like older test stubs)."""

    id: str
    text: str
    source: str
    score: float


class _KB:
    def __init__(self, results):
        self._results = results

    def search(self, query: str, k: int = 10):
        return list(self._results)


def _bill() -> ExtractedBill:
    return ExtractedBill(
        provider_name="Synthetic General Hospital",
        line_items=[
            LineItem(id="l1", description="Office visit", amount=Decimal("100.00")),
        ],
    )


def test_every_corpus_passage_has_a_readable_title():
    kb = KnowledgeBase()
    results = kb.search("duplicate charges billed twice emergency itemized", k=len(kb))
    assert results
    for r in results:
        assert r.title
        assert r.title != r.id


def test_description_uses_title_not_raw_id():
    kb = KnowledgeBase()
    top = kb.search("charged more than once for the same service duplicate", k=1)[0]
    boosted = type(top)(
        id=top.id, text=top.text, source=top.source, score=100.0, title=top.title
    )
    findings, _ = check_rights(_bill(), [], _KB([boosted]))
    assert len(findings) == 1
    desc = findings[0].description
    assert top.title in desc
    assert f"'{top.id}'" not in desc
    # The raw id is still kept for evidence/traceability.
    assert findings[0].passage_ids == [top.id]


def test_untitled_result_falls_back_to_humanized_id():
    result = _UntitledResult(
        id="ma-duplicate-charges", text="Do not bill twice.", source="Ref", score=100.0
    )
    findings, _ = check_rights(_bill(), [], _KB([result]))
    assert "Duplicate charges" in findings[0].description
    assert "ma-duplicate-charges" not in findings[0].description


def test_humanize_passage_id():
    assert humanize_passage_id("ma-duplicate-charges") == "Duplicate charges"
    assert humanize_passage_id("custom_passage") == "Custom passage"
