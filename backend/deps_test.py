"""Dependency rules for the LangGraph migration.

Validates: Requirements 1.1, 1.2, 1.4, 1.5, 1.6

- Every LangGraph-family package (name starts with ``langgraph``) is declared
  exactly once in ``requirements.txt`` and in ``pyproject.toml``
  ``[project].dependencies``, with an exact ``==`` pin, no range operators,
  no extras, and an identical version in both files.
- The declared LangGraph-family set equals the set of distributions that
  ``pipeline/graph.py`` imports directly (including imports nested inside
  functions): ``langgraph.checkpoint*`` maps to ``langgraph-checkpoint`` and
  any other ``langgraph`` / ``langgraph.*`` module maps to ``langgraph``.
- Every pre-migration pin is still present at its existing version.

Paths are resolved relative to this file so the test works from any cwd.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
REQUIREMENTS_TXT = BACKEND_DIR / "requirements.txt"
PYPROJECT_TOML = BACKEND_DIR / "pyproject.toml"
GRAPH_PY = BACKEND_DIR / "pipeline" / "graph.py"

# Pins that existed before the migration; they must not change (Req 1.5).
PRE_MIGRATION_RUNTIME_PINS = {
    "fastapi": ("", "0.162.0"),
    "uvicorn": ("[standard]", "0.54.0"),
    "pydantic": ("", "2.14.1"),
    "pdfplumber": ("", "0.11.10"),
    "rank-bm25": ("", "0.2.2"),
    "hypothesis": ("", "6.170.9"),
    "python-multipart": ("", "0.0.32"),
}
PRE_MIGRATION_DEV_PINS = {"pytest": ("", "8.3.4")}

# name, optional [extras], then the rest of the specifier (operators + versions).
_REQ_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*"
    r"(?P<extras>\[[^\]]*\])?\s*"
    r"(?P<spec>[^;#]*)"
)
_EXACT_PIN_RE = re.compile(r"^==\s*(?P<version>[0-9][0-9A-Za-z.+!-]*)$")
_RANGE_TOKENS = (">", "<", "~=", "!=", "=", "*", ",")


def _normalize(name: str) -> str:
    """PEP 503 name normalization."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_requirement(line: str) -> dict:
    match = _REQ_RE.match(line)
    assert match, f"Unparseable requirement line: {line!r}"
    return {
        "raw": line.strip(),
        "name": _normalize(match.group("name")),
        "extras": (match.group("extras") or "").replace(" ", ""),
        "spec": match.group("spec").strip(),
    }


def _requirements_txt_entries() -> list[dict]:
    entries = []
    for line in REQUIREMENTS_TXT.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped or stripped.startswith("-"):
            continue
        entries.append(_parse_requirement(stripped))
    return entries


def _pyproject() -> dict:
    with PYPROJECT_TOML.open("rb") as fh:
        return tomllib.load(fh)


def _pyproject_entries() -> list[dict]:
    deps = _pyproject()["project"]["dependencies"]
    return [_parse_requirement(d) for d in deps]


def _pyproject_dev_entries() -> list[dict]:
    dev = _pyproject()["project"].get("optional-dependencies", {}).get("dev", [])
    return [_parse_requirement(d) for d in dev]


def _langgraph_family(entries: list[dict]) -> list[dict]:
    return [e for e in entries if e["name"].startswith("langgraph")]


def _exact_version(entry: dict) -> str:
    """Return the version of a strict ``==X.Y.Z`` pin; fail on anything else."""
    spec = entry["spec"]
    # "==" is the only allowed operator; strip it before scanning for others.
    rest = spec[2:] if spec.startswith("==") and not spec.startswith("===") else spec
    for token in _RANGE_TOKENS:
        assert token not in rest, f"Range operator {token!r} in {entry['raw']!r}"
    match = _EXACT_PIN_RE.match(spec)
    assert match, f"Not an exact '==' pin: {entry['raw']!r}"
    return match.group("version")


def _imported_langgraph_distributions() -> set[str]:
    """AST-scan graph.py (whole tree, so nested imports count)."""
    tree = ast.parse(GRAPH_PY.read_text(encoding="utf-8"), filename=str(GRAPH_PY))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
    dists: set[str] = set()
    for module in modules:
        if module == "langgraph.checkpoint" or module.startswith("langgraph.checkpoint."):
            dists.add("langgraph-checkpoint")
        elif module == "langgraph" or module.startswith("langgraph."):
            dists.add("langgraph")
    return dists


# --------------------------------------------------------------------------- tests


def test_requirements_txt_langgraph_family_exact_pins_once():
    """Req 1.1, 1.4: each langgraph* package once, exact '==', no extras."""
    family = _langgraph_family(_requirements_txt_entries())
    names = [e["name"] for e in family]
    assert "langgraph" in names
    assert len(names) == len(set(names)), f"Duplicate langgraph-family entries: {names}"
    for entry in family:
        assert entry["extras"] == "", f"Extras not allowed: {entry['raw']!r}"
        _exact_version(entry)


def test_pyproject_langgraph_family_exact_pins_once():
    """Req 1.2, 1.4: each langgraph* package once in [project].dependencies."""
    family = _langgraph_family(_pyproject_entries())
    names = [e["name"] for e in family]
    assert "langgraph" in names
    assert len(names) == len(set(names)), f"Duplicate langgraph-family entries: {names}"
    for entry in family:
        assert entry["extras"] == "", f"Extras not allowed: {entry['raw']!r}"
        _exact_version(entry)


def test_langgraph_family_pins_identical_in_both_files():
    """Req 1.2, 1.4, 1.6: same set of packages and identical versions."""
    req = {e["name"]: _exact_version(e) for e in _langgraph_family(_requirements_txt_entries())}
    pyp = {e["name"]: _exact_version(e) for e in _langgraph_family(_pyproject_entries())}
    assert req == pyp


def test_declared_langgraph_family_equals_graph_imports():
    """Req 1.4, 1.6: declared langgraph* set == set imported by pipeline/graph.py."""
    imported = _imported_langgraph_distributions()
    declared_req = {e["name"] for e in _langgraph_family(_requirements_txt_entries())}
    declared_pyp = {e["name"] for e in _langgraph_family(_pyproject_entries())}
    assert declared_req == imported, (
        f"requirements.txt declares {sorted(declared_req)}, "
        f"pipeline/graph.py imports {sorted(imported)}"
    )
    assert declared_pyp == imported, (
        f"pyproject.toml declares {sorted(declared_pyp)}, "
        f"pipeline/graph.py imports {sorted(imported)}"
    )


def _assert_pins_present(entries: list[dict], expected: dict, where: str) -> None:
    by_name: dict[str, list[dict]] = {}
    for entry in entries:
        by_name.setdefault(entry["name"], []).append(entry)
    for name, (extras, version) in expected.items():
        found = by_name.get(name, [])
        assert len(found) == 1, f"{where}: expected exactly one {name!r}, found {len(found)}"
        entry = found[0]
        assert entry["extras"] == extras, f"{where}: extras changed for {entry['raw']!r}"
        assert _exact_version(entry) == version, f"{where}: version changed for {entry['raw']!r}"


def test_pre_migration_pins_unchanged():
    """Req 1.5: every pre-migration pin present at its existing version."""
    _assert_pins_present(_requirements_txt_entries(), PRE_MIGRATION_RUNTIME_PINS, "requirements.txt")
    _assert_pins_present(_pyproject_entries(), PRE_MIGRATION_RUNTIME_PINS, "pyproject.toml")
    _assert_pins_present(_pyproject_dev_entries(), PRE_MIGRATION_DEV_PINS, "pyproject.toml [dev]")


def test_exact_pin_parser_rejects_ranges_and_accepts_exact():
    """Guard the checker itself so a range pin cannot slip through."""
    import pytest

    assert _exact_version(_parse_requirement("langgraph==1.2.17")) == "1.2.17"
    for bad in (
        "langgraph>=1.2",
        "langgraph~=1.2",
        "langgraph<2",
        "langgraph==1.*",
        "langgraph==1.2.17,<2",
        "langgraph",
        "langgraph!=1.0",
    ):
        with pytest.raises(AssertionError):
            _exact_version(_parse_requirement(bad))
