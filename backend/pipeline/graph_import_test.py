"""Import-side-effect tests for ``pipeline.graph`` (Req 4.7).

Importing ``pipeline.graph`` must not construct a knowledge base, read any
corpus file, invoke an LLM provider, make a network call, compile the graph,
or create a LangGraph checkpointer.

Note: ``import langgraph.graph`` itself loads ``langgraph.checkpoint`` into
``sys.modules`` transitively, so "no checkpoint module loaded" cannot be
asserted. Instead we check the source (no direct ``langgraph.checkpoint``
import anywhere in ``graph.py``) and that no checkpointer instance exists
after the import.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
GRAPH_SOURCE = BACKEND_DIR / "pipeline" / "graph.py"

_TRACING_VARS = (
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
)

# Runs in a fresh interpreter. Every forbidden side effect is patched to raise
# (and to record that it was attempted) before ``pipeline.graph`` is imported.
_CHILD_SCRIPT = textwrap.dedent(
    """
    import builtins
    import gc
    import json
    import socket
    import sys
    from pathlib import Path

    attempts = []

    def _blocked(what):
        def _raise(*args, **kwargs):
            attempts.append(what)
            raise RuntimeError(f"forbidden at import time: {what}")
        return _raise

    # Network: block every outbound operation. Socket *creation* is not
    # blocked because urllib3 (pulled in transitively by langsmith) creates
    # and binds a local ::1 socket at import to probe IPv6 support; that is
    # not a network call.
    socket.socket.connect = _blocked("socket.connect")
    socket.socket.connect_ex = _blocked("socket.connect_ex")
    socket.socket.sendto = _blocked("socket.sendto")
    socket.create_connection = _blocked("socket.create_connection")
    socket.getaddrinfo = _blocked("socket.getaddrinfo")

    # Knowledge base construction and LLM provider construction.
    import rag.knowledge_base as kb_mod
    kb_mod.KnowledgeBase = _blocked("KnowledgeBase()")
    import llm.provider as provider_mod
    provider_mod.build_provider = _blocked("build_provider()")

    # Corpus reads.
    corpus_dir = (Path("rag") / "corpus").resolve()
    _real_open = builtins.open

    def _guarded_open(file, *args, **kwargs):
        try:
            p = Path(file).resolve()
        except TypeError:
            return _real_open(file, *args, **kwargs)
        if corpus_dir == p or corpus_dir in p.parents:
            attempts.append("corpus read")
            raise RuntimeError("forbidden at import time: corpus read")
        return _real_open(file, *args, **kwargs)

    builtins.open = _guarded_open

    import pipeline.graph as graph

    builtins.open = _real_open

    from langgraph.checkpoint.base import BaseCheckpointSaver

    saver_instances = sum(
        1 for obj in gc.get_objects() if isinstance(obj, BaseCheckpointSaver)
    )
    saver_attrs = sorted(
        name for name, value in vars(graph).items()
        if isinstance(value, BaseCheckpointSaver)
    )

    print(json.dumps({
        "attempts": attempts,
        "compiled_is_none": graph._COMPILED is None,
        "kb_is_none": graph._KNOWLEDGE_BASE is None,
        "saver_instances": saver_instances,
        "saver_attrs": saver_attrs,
    }))
    """
)


def _child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _TRACING_VARS}
    env["PYTHONPATH"] = str(BACKEND_DIR)
    return env


def test_import_has_no_side_effects_and_creates_no_checkpointer() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT],
        cwd=BACKEND_DIR,
        env=_child_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout.strip().splitlines()[-1])

    assert report["attempts"] == []
    assert report["compiled_is_none"] is True
    assert report["kb_is_none"] is True
    assert report["saver_instances"] == 0
    assert report["saver_attrs"] == []


def _checkpoint_imports(tree: ast.AST) -> list[str]:
    """Return every import in ``tree`` that targets ``langgraph.checkpoint*``."""
    hits: list[str] = []
    for node in ast.walk(tree):  # walk covers imports nested in functions
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "langgraph.checkpoint" or alias.name.startswith(
                    "langgraph.checkpoint."
                ):
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mod = node.module
            if mod == "langgraph.checkpoint" or mod.startswith("langgraph.checkpoint."):
                hits.append(mod)
            elif mod == "langgraph":
                hits.extend(
                    f"langgraph.{a.name}" for a in node.names if a.name == "checkpoint"
                )
    return hits


def test_checkpoint_import_detector_catches_all_forms() -> None:
    src = textwrap.dedent(
        """
        import langgraph.checkpoint
        import langgraph.checkpoint.memory as m
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph import checkpoint
        def f():
            from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        import langgraph.graph
        from langgraph.runtime import Runtime
        """
    )
    assert len(_checkpoint_imports(ast.parse(src))) == 5


def test_graph_source_has_no_direct_checkpoint_import() -> None:
    tree = ast.parse(GRAPH_SOURCE.read_text(encoding="utf-8"))
    assert _checkpoint_imports(tree) == []
