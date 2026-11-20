"""Extraction suite: real IntakeGraph runs on synthetic SMS, descriptions and screenshots,
scored field by field against hand-written answers."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from recourse.agents.intake_graph import IntakeInput, run_intake
from recourse.evidence.models import IST
from recourse.llm.base import Attachment

from suites.common import NOW, ROOT, RecordingLLM, bounded, load

FIELDS = [
    "case_type",
    "channel",
    "amount_inr",
    "txn_date",
    "txn_time",
    "reference",
    "payer_bank",
    "payer_account_last4",
    "scenario",
]


def _val(ev: Any, name: str) -> Any:
    return getattr(ev, name).value


def score(expected: dict[str, Any], ev: Any) -> dict[str, bool]:
    ts = _val(ev, "txn_timestamp")
    local = ts.astimezone(IST) if ts is not None else None
    got_time = local.strftime("%H:%M") if local is not None and ev.txn_time_known else None
    refs = {str(_val(ev, "utr") or ""), str(_val(ev, "other_reference") or "")}
    bank = str(_val(ev, "payer_bank") or "").lower()
    amount = _val(ev, "amount_inr")
    result = {
        "case_type": str(_val(ev, "case_type") or "") == expected["case_type"],
        "channel": str(_val(ev, "channel") or "") == expected["channel"],
        "amount_inr": amount is not None
        and Decimal(str(amount)) == Decimal(expected["amount_inr"]),
        "txn_date": local is not None and local.date().isoformat() == expected["txn_date"],
        # A time not shown must not be invented (None == None counts as correct).
        "txn_time": got_time == expected["txn_time"],
        "reference": expected["reference"] in refs,
        "payer_bank": expected["payer_bank"].lower() in bank,
        # For card payments the last 4 digits belong in card_last4: accept either field.
        "payer_account_last4": expected["payer_account_last4"]
        in {str(_val(ev, "payer_account_last4") or ""), str(_val(ev, "card_last4") or "")},
    }
    if expected["case_type"] == "failed_transaction":
        result["scenario"] = str(_val(ev, "scenario") or "") == expected["scenario"]
    return result


async def run(llm: Any) -> dict[str, Any]:
    cases = load("extraction.jsonl")

    async def one(case: dict[str, Any]) -> dict[str, Any]:
        rec = RecordingLLM(llm)
        attachments = []
        if "image" in case:
            attachments = [
                Attachment(media_type="image/png", data=(ROOT / case["image"]).read_bytes())
            ]
        try:
            result = await run_intake(
                rec, IntakeInput(text=case.get("text"), attachments=attachments), now=NOW
            )
        except Exception as e:  # report, don't stop the suite
            return {"id": case["id"], "kind": case["kind"], "error": f"{type(e).__name__}: {e}"}
        if result.evidence is None:
            return {
                "id": case["id"],
                "kind": case["kind"],
                "error": f"no evidence ({result.status})",
            }
        s = score(case["expected"], result.evidence)
        flagged = set(result.check.needs_user_confirmation) if result.check else set()
        wrong = [f for f, ok in s.items() if not ok]
        return {
            "id": case["id"],
            "kind": case["kind"],
            "scores": s,
            "wrong": wrong,
            # A wrong field the user is asked to check is a caught error, not a silent one.
            "wrong_but_flagged": [f for f in wrong if _evidence_name(f) in flagged],
            "models": sorted(set(rec.served)),
        }

    rows = await bounded(cases, one, limit=4)
    per_field: dict[str, list[bool]] = defaultdict(list)
    per_kind: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        for f, ok in r.get("scores", {}).items():
            per_field[f].append(ok)
            per_kind[r["kind"]].append(ok)
    total = [ok for v in per_field.values() for ok in v]
    return {
        "cases": len(rows),
        "errors": [r for r in rows if "error" in r],
        "field_accuracy": {f: _pct(per_field[f]) for f in FIELDS if per_field[f]},
        "kind_accuracy": {k: _pct(v) for k, v in per_kind.items()},
        "overall": _pct(total),
        "fully_correct_cases": sum(1 for r in rows if r.get("scores") and not r["wrong"]),
        "wrong_fields": sum(len(r.get("wrong", [])) for r in rows),
        "wrong_but_flagged": sum(len(r.get("wrong_but_flagged", [])) for r in rows),
        "models": sorted({m for r in rows for m in r.get("models", [])}),
        "rows": rows,
    }


def _evidence_name(field: str) -> str:
    return {"txn_date": "txn_timestamp", "txn_time": "txn_timestamp", "reference": "utr"}.get(
        field, field
    )


def _pct(xs: list[bool]) -> float:
    return round(100 * sum(xs) / len(xs), 1) if xs else 0.0
