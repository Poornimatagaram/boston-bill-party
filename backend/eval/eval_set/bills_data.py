"""Canonical definition of the ten synthetic Eval_Set bills (Req 12).

This single module is the source of truth for both the generated PDFs and
``manifest.json``: ``generate.py`` renders each bill's text to a PDF and writes
the manifest from the same ``BILLS`` structure, so the two can never drift.

Each bill is defined by a provider/account header, an ordered list of line
items, a stated total, and the list of planted errors. The line-item text uses
a fixed, machine-parseable one-line-per-item format so the deterministic eval
structuring path (task 16.2) can turn the extracted PDF text back into an
``ExtractedBill`` without an LLM:

    LINE <line_id> | CODE <billing_code_or_NONE> | <description> | QTY <q> | UNIT <u> | AMT <a>

A missing field uses the literal token ``NONE`` (for the code) or ``-`` (for a
numeric field). The stated total is on a ``TOTAL: <amount>`` line, or
``TOTAL: NONE`` when the bill has no stated total (Req 3.6 case).

Planted-error coverage across the set (the detectable Finding types are
``math_error``, ``duplicate_charge`` and ``suspicious_code``; ``rights_issue`` is
retrieval-based and is intentionally not planted here):

- bill_01: one per-line math error (amount != qty * unit) ..... math_error
- bill_02: one bill-total mismatch ........................... math_error
- bill_03: one duplicate charge (identical code+description) .. duplicate_charge
- bill_04: one unrecognized billing code ..................... suspicious_code
- bill_05: one missing billing code .......................... suspicious_code
- bill_06: absent stated total ............................... math_error
- bill_07: duplicate + unrecognized code (2 errors) .......... duplicate + suspicious
- bill_08: per-line math + total mismatch + missing code (3) . mixed
- bill_09: five mixed planted errors ......................... mixed
- bill_10: ten planted errors (max per bill, Req 12.3) ....... mixed

Recognized billing codes (see ``pipeline/auditor.py``): 99213, 99214, 99283,
80053, 85025, 36415, 71046, J1885, Q9967, G0463. Any code outside this set (or a
missing code) is flagged ``suspicious_code`` by the Auditor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Tokens used in the machine-parseable line format.
CODE_NONE = "NONE"  # billing_code absent
NUM_NONE = "-"  # numeric field absent
SYNTHETIC_BANNER = "*** SYNTHETIC EVALUATION BILL - NOT REAL PATIENT DATA ***"


@dataclass(frozen=True)
class EvalLine:
    """One line item in a synthetic bill."""

    line_id: str
    billing_code: Optional[str]  # None => rendered as CODE_NONE
    description: str
    quantity: Optional[str]  # decimal strings keep rendering exact
    unit_price: Optional[str]
    amount: Optional[str]


@dataclass(frozen=True)
class EvalPlantedError:
    """A planted error recorded in the manifest (Req 12.4)."""

    id: str
    expected_type: str  # a FindingType value, e.g. "math_error"
    line_item_id: Optional[str] = None


@dataclass(frozen=True)
class EvalBillSpec:
    """A full synthetic bill specification (header + lines + planted errors)."""

    id: str
    provider_name: str
    provider_address: str
    account_number: str
    lines: list[EvalLine]
    stated_total: Optional[str]  # None => rendered as "TOTAL: NONE" (Req 3.6)
    planted_errors: list[EvalPlantedError] = field(default_factory=list)


def _num(value: Optional[str]) -> str:
    return NUM_NONE if value is None else value


def _code(value: Optional[str]) -> str:
    return CODE_NONE if value is None else value


def render_lines(bill: EvalBillSpec) -> list[str]:
    """Render a bill spec to the list of text lines written into its PDF."""
    out: list[str] = [SYNTHETIC_BANNER]
    out.append(f"PROVIDER: {bill.provider_name}")
    out.append(f"ADDRESS: {bill.provider_address}")
    out.append(f"ACCOUNT: {bill.account_number}")
    out.append(f"BILL ID: {bill.id}")
    out.append("SYNTHETIC: true")
    out.append("LINE ITEMS:")
    for line in bill.lines:
        out.append(
            f"LINE {line.line_id} | CODE {_code(line.billing_code)} | "
            f"{line.description} | QTY {_num(line.quantity)} | "
            f"UNIT {_num(line.unit_price)} | AMT {_num(line.amount)}"
        )
    total = "NONE" if bill.stated_total is None else bill.stated_total
    out.append(f"TOTAL: {total}")
    return out


# ---------------------------------------------------------------------------
# The ten bills. Amounts are chosen so the Auditor (pipeline/auditor.py) emits
# exactly the planted Finding type(s) for each bill.
# ---------------------------------------------------------------------------

BILLS: list[EvalBillSpec] = [
    # bill_01 - single per-line math error: 2 * 75.00 should be 150.00 but amount
    # is 175.00 (|175 - 150| > 0.01). The stated total matches the (wrong) line
    # amount so no bill-total mismatch is introduced.
    EvalBillSpec(
        id="bill_01",
        provider_name="Charles River Medical Group",
        provider_address="100 Cambridge St, Boston, MA 02114",
        account_number="ACCT-0001",
        lines=[
            EvalLine("L1", "99213", "Office visit established patient", "2", "75.00", "175.00"),
        ],
        stated_total="175.00",
        planted_errors=[
            EvalPlantedError("bill_01-e1", "math_error", "L1"),
        ],
    ),
    # bill_02 - single bill-total mismatch: lines sum to 300.00 but stated total
    # is 350.00. Line math is internally consistent so no per-line math error.
    EvalBillSpec(
        id="bill_02",
        provider_name="Back Bay Health Partners",
        provider_address="200 Boylston St, Boston, MA 02116",
        account_number="ACCT-0002",
        lines=[
            EvalLine("L1", "99214", "Office visit moderate complexity", "1", "200.00", "200.00"),
            EvalLine("L2", "80053", "Comprehensive metabolic panel", "1", "100.00", "100.00"),
        ],
        stated_total="350.00",
        planted_errors=[
            EvalPlantedError("bill_02-e1", "math_error"),
        ],
    ),
    # bill_03 - single duplicate charge: two lines share code 85025 and the exact
    # same description. Totals and line math are consistent.
    EvalBillSpec(
        id="bill_03",
        provider_name="Fenway Family Clinic",
        provider_address="300 Brookline Ave, Boston, MA 02215",
        account_number="ACCT-0003",
        lines=[
            EvalLine("L1", "85025", "Complete blood count with differential", "1", "45.00", "45.00"),
            EvalLine("L2", "85025", "Complete blood count with differential", "1", "45.00", "45.00"),
            EvalLine("L3", "36415", "Routine venipuncture", "1", "20.00", "20.00"),
        ],
        stated_total="110.00",
        planted_errors=[
            EvalPlantedError("bill_03-e1", "duplicate_charge", "L1"),
        ],
    ),
    # bill_04 - single unrecognized billing code: "88888" is not in the
    # recognized set. Line math and total are consistent.
    EvalBillSpec(
        id="bill_04",
        provider_name="Seaport Urgent Care",
        provider_address="400 Seaport Blvd, Boston, MA 02210",
        account_number="ACCT-0004",
        lines=[
            EvalLine("L1", "99283", "Emergency department visit moderate severity", "1", "500.00", "500.00"),
            EvalLine("L2", "88888", "Unlisted specialty procedure", "1", "250.00", "250.00"),
        ],
        stated_total="750.00",
        planted_errors=[
            EvalPlantedError("bill_04-e1", "suspicious_code", "L2"),
        ],
    ),
    # bill_05 - single missing billing code: L2 has no code (NONE). Line math and
    # total are consistent.
    EvalBillSpec(
        id="bill_05",
        provider_name="North End Imaging Center",
        provider_address="500 Hanover St, Boston, MA 02113",
        account_number="ACCT-0005",
        lines=[
            EvalLine("L1", "71046", "Chest X-ray two views", "1", "180.00", "180.00"),
            EvalLine("L2", None, "Radiology interpretation fee", "1", "60.00", "60.00"),
        ],
        stated_total="240.00",
        planted_errors=[
            EvalPlantedError("bill_05-e1", "suspicious_code", "L2"),
        ],
    ),
    # bill_06 - absent stated total (Req 3.6): the Auditor records a math_error
    # noting the stated total is absent. Line math is consistent.
    EvalBillSpec(
        id="bill_06",
        provider_name="Longwood Specialty Associates",
        provider_address="600 Longwood Ave, Boston, MA 02115",
        account_number="ACCT-0006",
        lines=[
            EvalLine("L1", "Q9967", "Low-osmolar contrast material", "2", "90.00", "180.00"),
            EvalLine("L2", "G0463", "Hospital outpatient clinic visit", "1", "220.00", "220.00"),
        ],
        stated_total=None,
        planted_errors=[
            EvalPlantedError("bill_06-e1", "math_error"),
        ],
    ),
    # bill_07 - two planted errors: a duplicate charge (J1885 x2 identical desc)
    # and an unrecognized code ("ABC12"). Totals/line math otherwise consistent.
    EvalBillSpec(
        id="bill_07",
        provider_name="Dorchester Community Health",
        provider_address="700 Dorchester Ave, Boston, MA 02125",
        account_number="ACCT-0007",
        lines=[
            EvalLine("L1", "J1885", "Ketorolac injection 30mg", "1", "35.00", "35.00"),
            EvalLine("L2", "J1885", "Ketorolac injection 30mg", "1", "35.00", "35.00"),
            EvalLine("L3", "ABC12", "Miscellaneous supply charge", "1", "40.00", "40.00"),
        ],
        stated_total="110.00",
        planted_errors=[
            EvalPlantedError("bill_07-e1", "duplicate_charge", "L1"),
            EvalPlantedError("bill_07-e2", "suspicious_code", "L3"),
        ],
    ),
    # bill_08 - three planted errors: a per-line math error (L1: 3 * 50 != 200),
    # a bill-total mismatch (lines sum to 260.00 with L1 corrected... but using
    # stated amounts sum = 200 + 60 = 260; stated total 300 -> mismatch), and a
    # missing billing code (L2 NONE).
    EvalBillSpec(
        id="bill_08",
        provider_name="Roxbury Medical Center",
        provider_address="800 Tremont St, Boston, MA 02120",
        account_number="ACCT-0008",
        lines=[
            EvalLine("L1", "99213", "Office visit established patient", "3", "50.00", "200.00"),
            EvalLine("L2", None, "Facility service fee", "1", "60.00", "60.00"),
        ],
        stated_total="300.00",
        planted_errors=[
            EvalPlantedError("bill_08-e1", "math_error", "L1"),
            EvalPlantedError("bill_08-e2", "math_error"),
            EvalPlantedError("bill_08-e3", "suspicious_code", "L2"),
        ],
    ),
    # bill_09 - five planted errors spanning all detectable categories:
    #   L1: per-line math error (1 * 120 != 150)          -> math_error
    #   L2 & L3: duplicate charge (80053 identical desc)   -> duplicate_charge
    #   L4: unrecognized code "ZZ999"                      -> suspicious_code
    #   L5: missing code (NONE)                            -> suspicious_code
    #   bill-total mismatch (stated 700 vs sum 655)        -> math_error
    EvalBillSpec(
        id="bill_09",
        provider_name="Jamaica Plain Health Collaborative",
        provider_address="900 Centre St, Boston, MA 02130",
        account_number="ACCT-0009",
        lines=[
            EvalLine("L1", "99214", "Office visit moderate complexity", "1", "120.00", "150.00"),
            EvalLine("L2", "80053", "Comprehensive metabolic panel", "1", "100.00", "100.00"),
            EvalLine("L3", "80053", "Comprehensive metabolic panel", "1", "100.00", "100.00"),
            EvalLine("L4", "ZZ999", "Unlisted laboratory test", "1", "85.00", "85.00"),
            EvalLine("L5", None, "Supply handling fee", "1", "220.00", "220.00"),
        ],
        stated_total="700.00",
        planted_errors=[
            EvalPlantedError("bill_09-e1", "math_error", "L1"),
            EvalPlantedError("bill_09-e2", "duplicate_charge", "L2"),
            EvalPlantedError("bill_09-e3", "suspicious_code", "L4"),
            EvalPlantedError("bill_09-e4", "suspicious_code", "L5"),
            EvalPlantedError("bill_09-e5", "math_error"),
        ],
    ),
    # bill_10 - ten planted errors (the maximum per bill, Req 12.3):
    #   L1: per-line math error (2 * 40 != 100)            -> math_error (e1)
    #   L2: per-line math error (1 * 30 != 55)             -> math_error (e2)
    #   L3 & L4: duplicate charge (99213 identical desc)   -> duplicate_charge (e3)
    #   L5 & L6: duplicate charge (85025 identical desc)   -> duplicate_charge (e4)
    #   L7: unrecognized code "XX100"                      -> suspicious_code (e5)
    #   L8: unrecognized code "XX200"                      -> suspicious_code (e6)
    #   L9: missing code (NONE)                            -> suspicious_code (e7)
    #   L10: missing code (NONE)                           -> suspicious_code (e8)
    #   L11: missing amount (AMT -) -> excluded + flagged  -> math_error (e9)
    #   bill-total mismatch (stated 2000 vs computed sum)  -> math_error (e10)
    EvalBillSpec(
        id="bill_10",
        provider_name="South Boston Regional Hospital",
        provider_address="1000 W Broadway, Boston, MA 02127",
        account_number="ACCT-0010",
        lines=[
            EvalLine("L1", "99283", "Emergency department visit", "2", "40.00", "100.00"),
            EvalLine("L2", "36415", "Routine venipuncture", "1", "30.00", "55.00"),
            EvalLine("L3", "99213", "Office visit established patient", "1", "75.00", "75.00"),
            EvalLine("L4", "99213", "Office visit established patient", "1", "75.00", "75.00"),
            EvalLine("L5", "85025", "Complete blood count", "1", "45.00", "45.00"),
            EvalLine("L6", "85025", "Complete blood count", "1", "45.00", "45.00"),
            EvalLine("L7", "XX100", "Unlisted equipment charge", "1", "60.00", "60.00"),
            EvalLine("L8", "XX200", "Unlisted facility charge", "1", "70.00", "70.00"),
            EvalLine("L9", None, "Processing fee", "1", "25.00", "25.00"),
            EvalLine("L10", None, "Administrative fee", "1", "35.00", "35.00"),
            EvalLine("L11", "71046", "Chest X-ray two views", "1", "180.00", None),
        ],
        stated_total="2000.00",
        planted_errors=[
            EvalPlantedError("bill_10-e1", "math_error", "L1"),
            EvalPlantedError("bill_10-e2", "math_error", "L2"),
            EvalPlantedError("bill_10-e3", "duplicate_charge", "L3"),
            EvalPlantedError("bill_10-e4", "duplicate_charge", "L5"),
            EvalPlantedError("bill_10-e5", "suspicious_code", "L7"),
            EvalPlantedError("bill_10-e6", "suspicious_code", "L8"),
            EvalPlantedError("bill_10-e7", "suspicious_code", "L9"),
            EvalPlantedError("bill_10-e8", "suspicious_code", "L10"),
            EvalPlantedError("bill_10-e9", "math_error", "L11"),
            EvalPlantedError("bill_10-e10", "math_error"),
        ],
    ),
]
