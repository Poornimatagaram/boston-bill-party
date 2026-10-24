"""BM25 knowledge base for the Rights_Checker (Req 5.1).

Loads a small curated JSON corpus of Massachusetts patient-rights reference
passages from ``rag/corpus/*.json``, tokenizes each passage, and builds a
BM25 lexical index with ``rank-bm25`` (``BM25Okapi``). There is no embedding
model and no vector store -- retrieval is pure lexical keyword scoring, which
is lightweight, deterministic, and requires no network or model download.

Corpus format
-------------
Each ``*.json`` file under ``rag/corpus/`` is an object with:

    {
      "source": "<human-readable source label>",
      "passages": [
        {"id": "<unique id>", "text": "<passage text>"},
        ...
      ]
    }

A passage's ``source`` defaults to the file-level ``source`` but may be
overridden per passage with a ``"source"`` key.

Tokenization
------------
Deterministic and simple: lowercase the text, then split on any run of
non-alphanumeric characters (``[a-z0-9]+``). No stemming or stop-word removal.
The same tokenizer is applied to corpus passages at index build time and to
queries at search time so scoring is consistent.

Return contract (for task 12.2)
-------------------------------
:meth:`KnowledgeBase.search` returns a list of :class:`SearchResult`, each
carrying the passage ``id``, ``text``, ``source`` and the **raw** BM25
``score`` (a non-negative float; higher is more relevant). Results are ordered
by descending raw score and capped at ``k``.

Score normalization to the [0, 1] ``relevance_score`` used by ``Passage`` is
intentionally **not** done here -- per the design, the Rights_Checker
(task 12.2) owns normalization and threshold semantics. :class:`SearchResult`
exposes everything 12.2 needs to normalize and to construct ``Passage``
objects (``id``/``text``/``source``). A convenience
:meth:`SearchResult.to_passage` is provided for callers that already hold a
normalized score.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Optional

from rank_bm25 import BM25Okapi

from models import Passage

# Default corpus directory: the ``corpus/`` folder next to this module.
_CORPUS_DIR = Path(__file__).resolve().parent / "corpus"

# Tokenizer: runs of alphanumeric characters, lowercased.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Tokenize ``text`` deterministically for BM25.

    Lowercases the input and extracts maximal alphanumeric runs. No stemming
    or stop-word filtering. The identical function is used for both corpus
    passages and queries so scoring is consistent and reproducible.

    Args:
        text: Arbitrary input text.

    Returns:
        The list of lowercase alphanumeric tokens, in order.
    """
    return _TOKEN_RE.findall(text.lower())


@dataclass(frozen=True)
class SearchResult:
    """A single BM25 retrieval result.

    Attributes:
        id: The passage's unique id (stable across runs).
        text: The passage text.
        source: The passage's source label.
        score: The raw BM25 score (non-negative float; higher is more
            relevant). Not normalized -- task 12.2 normalizes to [0, 1].
    """

    id: str
    text: str
    source: str
    score: float

    def to_passage(self, relevance_score: Decimal) -> Passage:
        """Build a :class:`Passage` from this result and a normalized score.

        Provided as a convenience for the Rights_Checker, which computes the
        [0, 1] ``relevance_score`` from the raw BM25 ``score`` and then wants a
        validated ``Passage``.

        Args:
            relevance_score: The normalized relevance in [0, 1].

        Returns:
            A ``Passage`` carrying this result's id/text/source and the given
            normalized relevance score.
        """
        return Passage(
            id=self.id,
            text=self.text,
            source=self.source,
            relevance_score=relevance_score,
        )


@dataclass(frozen=True)
class _CorpusEntry:
    """An indexed corpus passage with its pre-computed tokens."""

    id: str
    text: str
    source: str
    tokens: list[str]


class KnowledgeBase:
    """A BM25 lexical index over the MA patient-rights JSON corpus.

    Build once and reuse: construction loads and tokenizes every passage and
    builds the ``BM25Okapi`` index up front. Loading is cheap (a handful of
    small JSON files) and performs no network or model access.
    """

    def __init__(self, corpus_dir: Optional[Path] = None) -> None:
        """Load the corpus and build the BM25 index.

        Args:
            corpus_dir: Directory containing ``*.json`` corpus files. Defaults
                to the ``corpus/`` folder next to this module.

        Raises:
            FileNotFoundError: If ``corpus_dir`` does not exist.
            ValueError: If the corpus directory contains no usable passages, or
                if any passage is missing a non-empty ``id`` or ``text``, or if
                two passages share the same ``id``.
        """
        self._corpus_dir = Path(corpus_dir) if corpus_dir is not None else _CORPUS_DIR
        if not self._corpus_dir.is_dir():
            raise FileNotFoundError(f"Corpus directory not found: {self._corpus_dir}")

        self._entries: list[_CorpusEntry] = self._load_corpus(self._corpus_dir)
        if not self._entries:
            raise ValueError(f"No passages found in corpus directory: {self._corpus_dir}")

        # BM25Okapi requires a non-empty token list per document. Passages are
        # natural-language text, so tokens are always present after validation.
        self._bm25 = BM25Okapi([entry.tokens for entry in self._entries])

    @staticmethod
    def _load_corpus(corpus_dir: Path) -> list[_CorpusEntry]:
        """Load and validate every passage from ``corpus_dir/*.json``.

        Files are processed in sorted filename order and passages in document
        order so the index is deterministic.
        """
        entries: list[_CorpusEntry] = []
        seen_ids: set[str] = set()

        for json_path in sorted(corpus_dir.glob("*.json")):
            with json_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)

            file_source = str(data.get("source", json_path.stem))
            passages = data.get("passages", [])
            if not isinstance(passages, list):
                raise ValueError(f"'passages' must be a list in {json_path.name}")

            for raw in passages:
                passage_id = str(raw.get("id", "")).strip()
                text = str(raw.get("text", "")).strip()
                if not passage_id:
                    raise ValueError(f"Passage missing 'id' in {json_path.name}")
                if not text:
                    raise ValueError(
                        f"Passage '{passage_id}' missing 'text' in {json_path.name}"
                    )
                if passage_id in seen_ids:
                    raise ValueError(f"Duplicate passage id '{passage_id}'")
                seen_ids.add(passage_id)

                source = str(raw.get("source", file_source))
                entries.append(
                    _CorpusEntry(
                        id=passage_id,
                        text=text,
                        source=source,
                        tokens=tokenize(text),
                    )
                )

        return entries

    def __len__(self) -> int:
        """Return the number of indexed passages."""
        return len(self._entries)

    @property
    def passage_ids(self) -> list[str]:
        """The ids of all indexed passages, in index order."""
        return [entry.id for entry in self._entries]

    def search(self, query: str, k: int = 10) -> list[SearchResult]:
        """Return up to ``k`` passages ranked by descending raw BM25 score.

        The query is tokenized with the same :func:`tokenize` used for the
        corpus. Ordering is deterministic: results are sorted by descending
        score, with ties broken by ascending corpus index so repeated calls
        with the same query always return the same order.

        An empty or all-non-alphanumeric query (no tokens) yields an empty
        list -- BM25 has no terms to score against.

        Args:
            query: The free-text query.
            k: Maximum number of results to return. Values ``<= 0`` yield an
                empty list; larger-than-corpus values are clamped to the
                corpus size.

        Returns:
            A list of :class:`SearchResult` of length ``min(k, corpus_size)``
            (or fewer for an empty query), ordered by descending raw BM25
            score. Scores are raw (not normalized); task 12.2 normalizes them.
        """
        if k <= 0:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scores = self._bm25.get_scores(query_tokens)

        # Sort by descending score; break ties by ascending index for a stable,
        # deterministic order independent of the underlying sort.
        order = sorted(range(len(self._entries)), key=lambda i: (-scores[i], i))

        results: list[SearchResult] = []
        for idx in order[:k]:
            entry = self._entries[idx]
            results.append(
                SearchResult(
                    id=entry.id,
                    text=entry.text,
                    source=entry.source,
                    score=float(scores[idx]),
                )
            )
        return results
