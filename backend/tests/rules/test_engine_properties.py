"""Cross-cutting properties of the rule engine."""

import ast
from datetime import date
from decimal import Decimal
from pathlib import Path

from recourse.rules.engine import assess_escalation, assess_fraud
from recourse.rules.models import (
    BankType,
    DeadlineLine,
    EscalationFacts,
    Fault,
    FraudDetails,
    FraudFacts,
    MoneyLine,
)

RULES_DIR = Path(__file__).resolve().parents[2] / "recourse" / "rules"

FORBIDDEN_CALLS = {"now", "today", "utcnow", "time", "random", "uuid4"}
FORBIDDEN_IMPORTS = {
    "random",
    "time",
    "uuid",
    "socket",
    "requests",
    "httpx",
    "urllib",
    "anthropic",
    "openai",
    "langgraph",
    "temporalio",
}


def test_rule_engine_is_pure() -> None:
    """No clock, randomness, network or LLM access anywhere in recourse/rules."""
    for path in RULES_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in FORBIDDEN_IMPORTS, (path, alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in FORBIDDEN_IMPORTS, (path, node.module)
            elif isinstance(node, ast.Attribute):
                assert node.attr not in FORBIDDEN_CALLS, (path, node.attr)


def test_fraud_result_feeds_escalation() -> None:
    """A Regime B fraud case hands its 45-day bank timeline to the escalation calculation."""
    fraud = assess_fraud(
        FraudFacts(
            amount_inr=Decimal("20000"),
            txn_date=date(2027, 3, 1),
            bank_type=BankType.COMMERCIAL_BANK,
            fault=Fault.THIRD_PARTY,
            reported_to_bank_date=date(2027, 3, 2),
            as_of=date(2027, 3, 3),
        )
    )
    assert isinstance(fraud.details, FraudDetails)
    escalation = assess_escalation(
        EscalationFacts(
            entity_type=BankType.COMMERCIAL_BANK,
            bank_received_complaint_date=date(2027, 3, 2),
            regulatory_reply_days=fraud.details.regulatory_reply_days,
            regulatory_reply_citation=fraud.details.regulatory_reply_citation,
            as_of=date(2027, 3, 3),
        )
    )
    # 2 Mar + 45 = 16 Apr; eligible 17 Apr; 16 Apr + 90 = 15 Jul.
    assert escalation.eligible_from == date(2027, 4, 17)
    assert escalation.filing_deadline == date(2027, 7, 15)
    assert any(c.para == "para 76Q" for c in escalation.citations)


def test_every_output_line_is_cited() -> None:
    fraud = assess_fraud(
        FraudFacts(
            amount_inr=Decimal("20000"),
            txn_date=date(2027, 3, 1),
            bank_type=BankType.COMMERCIAL_BANK,
            fault=Fault.CUSTOMER,
            reported_to_bank_date=date(2027, 3, 2),
            reported_to_ncrp_date=date(2027, 3, 2),
            previously_received_small_value_compensation=False,
            as_of=date(2027, 3, 3),
        )
    )
    lines: list[MoneyLine | DeadlineLine] = [*fraud.money, *fraud.deadlines]
    assert lines
    for line in lines:
        assert line.citation in fraud.citations
        assert line.citation.quotes
        assert line.citation.url.startswith("https://")
