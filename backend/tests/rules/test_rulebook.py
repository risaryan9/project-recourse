"""Integrity tests for the YAML rulebook itself.

The most important one is `test_every_quote_appears_in_its_source`: every `quotes` entry in the
rulebook must appear verbatim (ignoring whitespace) in the local copy of the cited document in
docs/sources/. If someone edits a number in the YAML without the source supporting it, the quote
no longer matches and CI fails.
"""

import re
from collections.abc import Iterator
from datetime import timedelta
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from pypdf import PdfReader

from recourse.rules.loader import load_rulebook
from recourse.rules.models import (
    AccountType,
    BankType,
    Channel,
    FailedScenario,
    Rulebook,
    SourceRef,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCES_DIR = REPO_ROOT / "docs" / "sources"


@pytest.fixture(scope="module")
def book() -> Rulebook:
    return load_rulebook()


def _squash(text: str) -> str:
    """Remove all whitespace. PDF text extraction inserts stray spaces and line breaks."""
    return re.sub(r"\s+", "", text)


@cache
def _source_text(local_file: str) -> str:
    path = SOURCES_DIR / local_file
    if not path.exists():
        pytest.fail(f"source file missing: docs/sources/{local_file}")
    reader = PdfReader(path)
    return _squash("".join(page.extract_text() for page in reader.pages))


def _source_refs(obj: Any) -> Iterator[SourceRef]:
    """Every SourceRef anywhere inside a (Pydantic) rulebook object."""
    if isinstance(obj, SourceRef):
        yield obj
    elif isinstance(obj, list | tuple):
        for item in obj:
            yield from _source_refs(item)
    elif isinstance(obj, dict):
        for item in obj.values():
            yield from _source_refs(item)
    elif isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            yield from _source_refs(getattr(obj, name))


def _quote_cases(book: Rulebook) -> list[tuple[str, str, str]]:
    """(owner id, doc id, quote) for every quote in the rulebook."""
    cases: list[tuple[str, str, str]] = []
    failed_doc = book.failed.regime.doc
    for ref in _source_refs(book.failed):
        cases += [(book.failed.regime.id, failed_doc, q) for q in ref.quotes]
    for regime in book.fraud.regimes:
        for ref in _source_refs(regime):
            cases += [(regime.id, regime.doc, q) for q in ref.quotes]
    scheme = book.ombudsman.scheme
    for ref in _source_refs(scheme):
        cases += [(scheme.id, scheme.doc, q) for q in ref.quotes]
    return cases


_BOOK = load_rulebook()
_QUOTES = _quote_cases(_BOOK)


def test_rulebook_loads_and_has_quotes() -> None:
    assert len(_QUOTES) >= 60


@pytest.mark.parametrize(
    ("owner", "doc_id", "quote"),
    _QUOTES,
    ids=[f"{o}:{q[:40]}" for o, _, q in _QUOTES],
)
def test_every_quote_appears_in_its_source(owner: str, doc_id: str, quote: str) -> None:
    doc = _BOOK.documents[doc_id]
    assert _squash(quote) in _source_text(doc.local_file), (
        f"{owner}: quote not found in {doc.local_file}: {quote!r}"
    )


def test_every_document_has_a_local_copy(book: Rulebook) -> None:
    for doc in book.documents.values():
        assert (SOURCES_DIR / doc.local_file).exists(), doc.local_file
        assert doc.local_file in (SOURCES_DIR / "SOURCES.md").read_text(encoding="utf-8")


def test_failed_rules_cover_every_annex_row(book: Rulebook) -> None:
    keys = {(r.channel, r.scenario) for r in book.failed.rules}
    expected = {
        (Channel.ATM, FailedScenario.CASH_NOT_DISPENSED),
        (Channel.CARD_TO_CARD, FailedScenario.BENEFICIARY_NOT_CREDITED),
        (Channel.CARD_POS, FailedScenario.MERCHANT_NOT_CONFIRMED),
        (Channel.CARD_NOT_PRESENT, FailedScenario.MERCHANT_NOT_CONFIRMED),
        (Channel.IMPS, FailedScenario.BENEFICIARY_NOT_CREDITED),
        (Channel.UPI, FailedScenario.BENEFICIARY_NOT_CREDITED),
        (Channel.UPI, FailedScenario.MERCHANT_NOT_CONFIRMED),
        (Channel.AEPS, FailedScenario.MERCHANT_NOT_CONFIRMED),
        (Channel.AEPS, FailedScenario.BENEFICIARY_NOT_CREDITED),
        (Channel.APBS, FailedScenario.BENEFICIARY_NOT_CREDITED),
        (Channel.NACH, FailedScenario.BENEFICIARY_NOT_CREDITED),
        (Channel.NACH, FailedScenario.MANDATE_REVOKED_DEBIT),
        (Channel.PPI_ON_US, FailedScenario.BENEFICIARY_NOT_CREDITED),
        (Channel.PPI_ON_US, FailedScenario.MERCHANT_NOT_CONFIRMED),
    }
    assert keys == expected


def test_all_failed_rules_pay_100_per_day(book: Rulebook) -> None:
    assert {r.compensation_per_day_inr for r in book.failed.rules} == {100}
    assert {r.tat_days for r in book.failed.rules} == {1, 5}


def test_fraud_regimes_are_contiguous_for_commercial_banks(book: Rulebook) -> None:
    regimes = sorted(
        (r for r in book.fraud.regimes if BankType.COMMERCIAL_BANK in r.covered_bank_types),
        key=lambda r: r.effective_from,
    )
    assert [r.id for r in regimes] == ["FRAUD-A-2017", "FRAUD-A-2025", "FRAUD-B-2027"]
    for earlier, later in zip(regimes, regimes[1:], strict=False):
        assert earlier.effective_to is not None
        assert earlier.effective_to + timedelta(days=1) == later.effective_from
    assert regimes[-1].effective_to is None


def test_table_1_caps_match_the_circular(book: Rulebook) -> None:
    for regime_id in ("FRAUD-A-2017", "FRAUD-A-2025"):
        regime = next(r for r in book.fraud.regimes if r.id == regime_id)
        assert regime.liability_caps is not None
        caps = regime.liability_caps
        assert caps.cap_for(AccountType.BSBD) == 5000
        assert caps.cap_for(AccountType.SAVINGS) == 10000
        assert caps.cap_for(AccountType.CREDIT_CARD_UP_TO_5_LAKH) == 10000
        assert caps.cap_for(AccountType.CREDIT_CARD_ABOVE_5_LAKH) == 25000
        assert caps.cap_for(AccountType.OTHER_CURRENT_CC_OD) == 25000


def test_verified_flags_are_explicit_booleans(book: Rulebook) -> None:
    # Flip to True only after a human checks the rule against the PDF (see docs/decisions.md).
    assert isinstance(book.failed.regime.verified, bool)
    assert all(isinstance(r.verified, bool) for r in book.failed.rules)
    assert all(isinstance(r.verified, bool) for r in book.fraud.regimes)
    assert isinstance(book.ombudsman.scheme.verified, bool)
