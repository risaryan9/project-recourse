"""Load and validate the YAML rulebook.

The rulebook ships inside the package (`recourse/rules/rulebook/*.yaml`). Loading parses it into
strict Pydantic models and then checks cross-references, so a broken rulebook fails at startup
(and in CI), never in the middle of a case.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from recourse.rules.models import (
    FailedTxnBook,
    FraudBook,
    OmbudsmanBook,
    Rulebook,
)

RULEBOOK_PACKAGE = "recourse.rules"
RULEBOOK_DIR = "rulebook"


class RulebookError(ValueError):
    """The rulebook YAML is inconsistent."""


def _read_yaml(name: str) -> Any:
    path = resources.files(RULEBOOK_PACKAGE).joinpath(RULEBOOK_DIR, name)
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _periods_overlap(a_from: date, a_to: date | None, b_from: date, b_to: date | None) -> bool:
    a_end = a_to or date.max
    b_end = b_to or date.max
    return a_from <= b_end and b_from <= a_end


def _validate(book: Rulebook) -> None:
    docs = book.documents

    def need_doc(doc_id: str, where: str) -> None:
        if doc_id not in docs:
            raise RulebookError(f"{where} cites unknown document {doc_id!r}")

    # Failed transactions
    need_doc(book.failed.regime.doc, f"regime {book.failed.regime.id}")
    rule_ids = [r.id for r in book.failed.rules]
    if len(rule_ids) != len(set(rule_ids)):
        raise RulebookError("duplicate failed-transaction rule id")
    keys = [(r.channel, r.scenario) for r in book.failed.rules]
    if len(keys) != len(set(keys)):
        raise RulebookError("two failed-transaction rules share a channel and scenario")

    # Fraud regimes: known docs, unique ids, valid and non-overlapping periods per bank type
    regimes = book.fraud.regimes
    if len({r.id for r in regimes}) != len(regimes):
        raise RulebookError("duplicate fraud regime id")
    for r in regimes:
        need_doc(r.doc, f"fraud regime {r.id}")
        if r.effective_to is not None and r.effective_to < r.effective_from:
            raise RulebookError(f"fraud regime {r.id} ends before it starts")
    for i, a in enumerate(regimes):
        for b in regimes[i + 1 :]:
            shared_types = set(a.covered_bank_types) & set(b.covered_bank_types)
            if shared_types and _periods_overlap(
                a.effective_from, a.effective_to, b.effective_from, b.effective_to
            ):
                raise RulebookError(f"fraud regimes {a.id} and {b.id} overlap")

    # Ombudsman
    need_doc(book.ombudsman.scheme.doc, f"scheme {book.ombudsman.scheme.id}")


def load_rulebook() -> Rulebook:
    """Parse and validate the rulebook from the package data (uncached)."""
    sources = _read_yaml("sources.yaml")
    documents = {d["id"]: d for d in sources["documents"]}
    if len(documents) != len(sources["documents"]):
        raise RulebookError("duplicate document id in sources.yaml")
    book = Rulebook(
        documents=documents,
        failed=FailedTxnBook.model_validate(_read_yaml("failed_transactions.yaml")),
        fraud=FraudBook.model_validate(_read_yaml("fraud_liability.yaml")),
        ombudsman=OmbudsmanBook.model_validate(_read_yaml("ombudsman.yaml")),
    )
    _validate(book)
    return book


@lru_cache(maxsize=1)
def get_rulebook() -> Rulebook:
    """The process-wide rulebook (loaded once)."""
    return load_rulebook()
