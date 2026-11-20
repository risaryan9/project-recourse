"""A transaction time is never invented: a bare date stays "time not known" (no midnight)."""

from __future__ import annotations

from datetime import date

from recourse.agents.draft_slots import build_slots
from recourse.agents.intake_schema import ExtractionOutput, to_evidence
from recourse.cases.assessment import CaseDetails, assess
from recourse.evidence.models import Evidence, SourceKind, has_time
from tests.unit.test_case_logic import evidence
from tests.unit.test_intake_graph import extraction, fact


def ev_from(timestamp: str) -> Evidence:
    out = ExtractionOutput.model_validate(
        extraction(txn_timestamp=fact(timestamp, source="on 16-11-26"))
    )
    return to_evidence(out, source_kind=SourceKind.SMS, extracted_by="test")


def test_bare_date_is_kept_as_time_unknown() -> None:
    ev = ev_from("2026-11-16")
    assert ev.txn_time_known is False
    assert ev.txn_date == date(2026, 11, 16)
    assert ev_from("2026-11-16T18:05:00").txn_time_known is True


def test_letters_never_show_an_invented_time() -> None:
    known = evidence()
    unknown = evidence(txn_timestamp="2026-11-14").model_copy(update={"txn_time_known": False})
    for ev, has_slot in [(known, True), (unknown, False)]:
        table = build_slots(
            assess(ev, CaseDetails(), as_of=date(2026, 11, 18)),
            ev,
            case_tag="RC-7F3K",
            letter_date=date(2026, 11, 18),
        )
        assert ("txn.datetime" in table.slots) is has_slot
        assert "txn.date" in table.slots


def test_user_correction_with_a_time_makes_it_known() -> None:
    unknown = evidence(txn_timestamp="2026-11-14").model_copy(update={"txn_time_known": False})
    fixed = unknown.with_user_value("txn_timestamp", "2026-11-14T09:30:00+05:30")
    assert fixed.txn_time_known is True
    still = unknown.with_user_value("txn_timestamp", "2026-11-14")
    assert still.txn_time_known is False


def test_has_time() -> None:
    assert has_time("2026-11-14T18:05")
    assert not has_time("2026-11-14")
    assert not has_time(None)
