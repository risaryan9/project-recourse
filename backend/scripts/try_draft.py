"""Run the DraftGraph once against the real LLM providers on a built-in synthetic case (uses API
keys from .env; costs about one or two cents per run). No real data.

    uv run python scripts/try_draft.py            # failed UPI transfer
    uv run python scripts/try_draft.py --fraud    # unauthorised transaction, commercial bank
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime
from typing import Any

from recourse.agents.draft_graph import DraftStatus, run_draft
from recourse.cases.assessment import CaseDetails, assess
from recourse.evidence.models import IST, Evidence
from recourse.llm import get_llm
from recourse.rules.models import BankType

SYNTHETIC: dict[str, Any] = {
    "case_type": "failed_transaction",
    "scenario": "BENEFICIARY_NOT_CREDITED",
    "channel": "UPI",
    "account_debited": True,
    "amount_inr": "2450",
    "utr": "631845207719",
    "payer_bank": "Example Bank",
    "payee_name": "Sample Traders",
    "payee_vpa": "sample.traders@examplebank",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fraud", action="store_true", help="draft a fraud complaint instead")
    parser.add_argument("-v", "--verbose", action="store_true", help="show LLM call logs")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR)

    now = datetime.now(IST)
    facts = dict(SYNTHETIC, txn_timestamp=now.replace(day=max(now.day - 4, 1)).isoformat())
    details = CaseDetails()
    if args.fraud:
        facts.update(case_type="fraud", scenario=None)
        details = CaseDetails(bank_type=BankType.COMMERCIAL_BANK)
    data: dict[str, Any] = {"source_kind": "SMS", "extracted_by": "synthetic"}
    for name, value in facts.items():
        if value is not None:
            data[name] = {"value": value, "confidence": 1.0, "origin": "USER"}
    evidence = Evidence.model_validate(data)
    entitlement = assess(evidence, details, as_of=now.date())

    result = asyncio.run(
        run_draft(get_llm(), entitlement, evidence, case_tag="RC-DEMO", letter_date=now.date())
    )
    print(
        f"\nStatus: {result.status.value}  attempts: {result.attempts}  "
        f"models: {', '.join(result.models_used)}  rules verified: {result.rules_verified}"
    )
    if result.status is DraftStatus.FAILED:
        print("Verify errors (last attempt):")
        for e in result.errors:
            print(f"  - {e}")
        return
    print(f"\nSubject: {result.subject}\n\n{result.body}\n\n-- {result.disclaimer}")


if __name__ == "__main__":
    main()
