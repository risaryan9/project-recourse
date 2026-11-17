"""Tables, migrations and audit log against a real Postgres (Neon test branch).

Run with `uv run pytest -m db`. Synthetic data only.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from recourse.agents.intake_graph import IntakeStatus, apply_corrections
from recourse.db.audit import record_event
from recourse.db.case_tag import new_case_tag
from recourse.db.models import (
    Actor,
    AuditEvent,
    Base,
    Case,
    CaseStatus,
    EvidenceRecord,
)
from recourse.evidence.models import IST, Evidence, SourceKind
from tests.db.conftest import alembic_config

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=IST)


def sample_evidence() -> Evidence:
    return Evidence.model_validate(
        {
            "source_kind": SourceKind.SMS,
            "extracted_by": "fake-extract",
            "case_type": {"value": "failed_transaction", "confidence": 0.9},
            "scenario": {"value": "BENEFICIARY_NOT_CREDITED", "confidence": 0.9},
            "channel": {"value": "UPI", "confidence": 0.95},
            "account_debited": {"value": True, "confidence": 0.95},
            "amount_inr": {"value": "Rs 2,450.00", "confidence": 0.95},
            "txn_timestamp": {"value": "2026-11-14T18:05:00", "confidence": 0.95},
            "utr": {"value": "631845207719", "confidence": 0.95},
            "payer_bank": {"value": "Example Bank", "confidence": 0.95},
        }
    )


async def new_case(session: AsyncSession) -> Case:
    case = Case(case_tag=new_case_tag())
    session.add(case)
    await session.flush()
    return case


def test_models_match_migrations(db_url: str) -> None:
    """Fails if a model changed without a migration (or the other way round)."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        diff = compare_metadata(
            MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata
        )
    engine.dispose()
    assert diff == []


def test_pgvector_enabled(db_url: str) -> None:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        assert conn.execute(text("select 1 from pg_extension where extname='vector'")).scalar()
    engine.dispose()


async def test_case_defaults(session: AsyncSession) -> None:
    case = await new_case(session)
    await session.commit()
    await session.refresh(case)
    assert case.status is CaseStatus.NEW
    assert case.created_at.tzinfo is not None
    assert case.id is not None


async def test_case_tag_is_unique(session: AsyncSession) -> None:
    session.add_all([Case(case_tag="RC-AAAA"), Case(case_tag="RC-AAAA")])
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_evidence_round_trip(session: AsyncSession) -> None:
    case = await new_case(session)
    result = apply_corrections(sample_evidence(), {}, now=NOW)
    session.add(EvidenceRecord.from_intake(case.id, result))
    await session.commit()

    stored = (await session.scalars(select(EvidenceRecord))).one()
    assert stored.intake_status is IntakeStatus.READY
    assert stored.source_kind is SourceKind.SMS
    assert stored.to_evidence() == result.evidence
    assert stored.check_result["issues"] == []


async def test_audit_events_are_redacted(session: AsyncSession) -> None:
    case = await new_case(session)
    record_event(
        session,
        case.id,
        Actor.USER,
        "case.created",
        {"note": "my OTP is 482913", "fields": ["utr"]},
    )
    await session.commit()

    event = (await session.scalars(select(AuditEvent))).one()
    assert "482913" not in str(event.details)
    assert event.details["fields"] == ["utr"]
    assert event.actor is Actor.USER


async def test_audit_events_cannot_be_edited(session: AsyncSession) -> None:
    case = await new_case(session)
    record_event(session, case.id, Actor.SYSTEM, "case.created")
    await session.commit()

    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(text("UPDATE audit_events SET event_type = 'case.tampered'"))
    await session.rollback()


async def test_deleting_a_case_deletes_its_data(session: AsyncSession) -> None:
    case = await new_case(session)
    session.add(
        EvidenceRecord.from_intake(case.id, apply_corrections(sample_evidence(), {}, now=NOW))
    )
    record_event(session, case.id, Actor.SYSTEM, "case.created")
    await session.commit()

    await session.delete(case)
    await session.commit()

    for table in (EvidenceRecord, AuditEvent):
        assert await session.scalar(select(func.count()).select_from(table)) == 0


def test_downgrade_and_upgrade_again(db_url: str) -> None:
    cfg = alembic_config(db_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
