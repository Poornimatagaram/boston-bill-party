"""Generate the ten synthetic Eval_Set PDFs and ``manifest.json`` (Req 12).

Run from the backend directory with the project's virtualenv:

    .venv/bin/python -m eval.eval_set.generate

This renders each bill in :data:`eval.eval_set.bills_data.BILLS` to a
text-bearing PDF (``bill_01.pdf`` ... ``bill_10.pdf``) using the dependency-free
writer in :mod:`eval.eval_set._pdfgen`, and writes ``manifest.json`` from the
same specs so the PDFs and the manifest stay in sync. The committed PDFs and
manifest are the deliverables; this script only needs to be re-run if the bill
definitions change.

The manifest records, for each of the ten bills: ``id``, ``pdf_path`` (relative
to this directory), ``synthetic: true`` (Req 12.1 / 12.2), and the list of
``planted_errors`` -- each with its ``id``, ``expected_type``, and
``line_item_id`` (Req 12.4).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict

from eval.eval_set import _pdfgen
from eval.eval_set.bills_data import BILLS, render_lines

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST_PATH = os.path.join(HERE, "manifest.json")


def _planted_error_dict(err) -> dict:
    """Serialize a planted error to its manifest form (omit null line_item_id)."""
    data = {"id": err.id, "expected_type": err.expected_type}
    if err.line_item_id is not None:
        data["line_item_id"] = err.line_item_id
    return data


def main() -> None:
    manifest_bills: list[dict] = []
    for bill in BILLS:
        pdf_name = f"{bill.id}.pdf"
        pdf_path = os.path.join(HERE, pdf_name)
        lines = render_lines(bill)
        pdf_bytes = _pdfgen.build_pdf_bytes(lines)
        with open(pdf_path, "wb") as fh:
            fh.write(pdf_bytes)

        manifest_bills.append(
            {
                "id": bill.id,
                "synthetic": True,  # Req 12.1 / 12.2
                "pdf_path": pdf_name,  # relative to this directory
                "planted_errors": [
                    _planted_error_dict(err) for err in bill.planted_errors
                ],
            }
        )

    manifest = {
        "description": (
            "Synthetic Eval_Set: ten bills with planted billing errors (Req 12). "
            "All data is synthetic; no real patient data."
        ),
        "synthetic": True,
        "bills": manifest_bills,
    }
    with open(MANIFEST_PATH, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")

    print(f"Wrote {len(manifest_bills)} PDFs and manifest.json to {HERE}")


if __name__ == "__main__":
    main()
