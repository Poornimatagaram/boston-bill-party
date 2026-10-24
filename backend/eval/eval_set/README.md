# Synthetic Eval_Set (Req 12)

Ten synthetic medical bills with planted billing errors, used to measure
detection quality (Req 13). **All data here is synthetic — no real patient
data.** Each bill is marked `synthetic: true` in `manifest.json` and carries a
`*** SYNTHETIC EVALUATION BILL ***` banner in its PDF text.

## Contents

- `bill_01.pdf` … `bill_10.pdf` — text-bearing synthetic bill PDFs.
- `manifest.json` — the Eval_Set record: for each bill its `id`, relative
  `pdf_path`, `synthetic: true`, and the list of `planted_errors` (each with a
  unique `id`, an `expected_type` Finding type, and an optional `line_item_id`
  linking it to its location) (Req 12.4).
- `bills_data.py` — the single source of truth for the bill specs.
- `generate.py` — regenerates the PDFs **and** the manifest from `bills_data.py`
  so they never drift. Run: `.venv/bin/python -m eval.eval_set.generate`.
- `_pdfgen.py` — a dependency-free, standard-library PDF writer (no new backend
  dependency was added). It emits valid multi-page PDFs whose text `pdfplumber`
  extracts line-by-line.
- `loader.py` — `load_eval_set()` reads and validates the manifest.

## Planted-error coverage

The Auditor detects three Finding types: `math_error`, `duplicate_charge`, and
`suspicious_code` (`rights_issue` is retrieval-based and is **not** planted
here). The ten bills exercise all three, with 1–10 planted errors each (26 in
total), up to the max of ten on `bill_10` (Req 12.3).

## Validation (loader.py)

`load_eval_set()` enforces:

- exactly ten bills (Req 12.3);
- 1–10 planted errors per bill (Req 12.3, via the `EvalBill` model);
- every planted error has an `expected_type` — a missing one raises an
  `EvalSetError` **naming the bill** (Req 12.5);
- planted-error ids are unique within a bill (Req 12.4);
- every bill is marked `synthetic` (Req 12.1 / 12.2);
- `pdf_path` is resolved to an absolute path that exists.

## Line-item text format (how task 16.2 structures these bills deterministically)

The harness (task 16.2) runs the real pipeline once per bill (Req 13.1). To keep
evaluation deterministic without calling a real LLM, the PDFs embed each line
item as one machine-parseable line:

```
LINE <line_id> | CODE <code|NONE> | <description> | QTY <q|-> | UNIT <u|-> | AMT <a|->
TOTAL: <amount|NONE>
```

A deterministic eval structuring path (an `LLMProvider`-shaped stub the harness
injects into the Extractor, or an equivalent parser) turns this extracted text
straight into an `ExtractedBill`: split each `LINE` row on ` | `, map `NONE`/`-`
to `null`, and read `TOTAL:` as the stated total (`NONE` ⇒ no stated total, the
Req 3.6 case). Because the Extractor already reads the PDF with `pdfplumber` and
only the structuring step uses the provider, the stub keeps the rest of the
pipeline (Sum_Check, Auditor, Skeptic, Writer) fully real.

This planting has been checked end-to-end against `pipeline/auditor.py`: parsing
each PDF and running the Auditor yields exactly the planted Finding type(s) for
every bill, so the planted errors are genuinely detectable. Task 16.2 owns the
pipeline execution, matching (Req 13.4/13.5), and the aggregate `EvalReport`
(Req 13.6).
