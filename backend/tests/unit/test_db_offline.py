"""Database helpers that need no database."""

import pytest

from recourse.agents.intake_graph import IntakeResult, IntakeStatus
from recourse.db.audit import redact_details
from recourse.db.case_tag import CASE_TAG_RE, new_case_tag
from recourse.db.models import EvidenceRecord


def test_case_tags_match_format() -> None:
    tags = {new_case_tag() for _ in range(200)}
    assert all(CASE_TAG_RE.match(t) for t in tags)
    assert len(tags) > 190  # random, not constant
    assert not any(c in "ILOU" for t in tags for c in t[3:])


def test_redact_details_walks_nested_values() -> None:
    out = redact_details({"a": ["OTP 482913", {"b": "card 4111 1111 1111 1111"}], "n": 3})
    assert "482913" not in str(out)
    assert "4111 1111 1111 1111" not in str(out)
    assert out["n"] == 3


def test_record_event_rejects_bad_event_type() -> None:
    from recourse.db.audit import record_event

    class _Session:
        def add(self, _: object) -> None:
            raise AssertionError("should not be called")

    import uuid

    from recourse.db.models import Actor

    with pytest.raises(ValueError):
        record_event(_Session(), uuid.uuid4(), Actor.SYSTEM, "Case Created")  # type: ignore[arg-type]


def test_evidence_record_needs_evidence() -> None:
    import uuid

    with pytest.raises(ValueError):
        EvidenceRecord.from_intake(
            uuid.uuid4(), IntakeResult(status=IntakeStatus.NOT_PAYMENT_EVIDENCE)
        )
