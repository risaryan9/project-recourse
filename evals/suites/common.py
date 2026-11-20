"""Shared helpers for the eval suites: paths, LLM clients, evidence from hand-written facts."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel
from recourse.config import Settings, get_settings
from recourse.evidence.models import IST, Evidence
from recourse.llm import LLMClient, get_llm
from recourse.llm.base import LLMRequest, LLMResult
from recourse.llm.openai_client import OpenAILLM
from recourse.llm.prompting import render_prompt

ROOT = Path(__file__).resolve().parents[2]
DATASETS = ROOT / "evals" / "datasets"
REPORTS = ROOT / "evals" / "reports"

# "Today" for every eval run: fixed, so results do not drift with the calendar.
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=IST)

T = TypeVar("T")


def load(name: str) -> list[dict[str, Any]]:
    with (DATASETS / name).open() as f:
        return [json.loads(line) for line in f if line.strip()]


@dataclass
class RecordingLLM:
    """Wraps a client and keeps the exact prompt text each call sent (for the PII checks) and
    the models that answered."""

    inner: LLMClient
    prompts: list[str] = field(default_factory=list)
    served: list[str] = field(default_factory=list)

    async def structured[M: BaseModel](self, request: LLMRequest, schema: type[M]) -> LLMResult[M]:
        self.prompts.append(render_prompt(request).user_text)
        r = await self.inner.structured(request, schema)
        self.served.append(r.served_model)
        return r

    async def text(self, request: LLMRequest) -> LLMResult[str]:
        self.prompts.append(render_prompt(request).user_text)
        r = await self.inner.text(request)
        self.served.append(r.served_model)
        return r


def product_llm(settings: Settings | None = None) -> LLMClient:
    return get_llm(settings or get_settings())


def judge_llm(settings: Settings | None = None) -> tuple[LLMClient, str]:
    """The independent judge: an OpenAI model (OPENAI_JUDGE_MODEL), asked for in the MAIN role."""
    s = settings or get_settings()
    if not s.openai_judge_model:
        raise SystemExit("Set OPENAI_JUDGE_MODEL to run the draft-quality suite.")
    return OpenAILLM(
        s.model_copy(update={"openai_main_model": s.openai_judge_model})
    ), s.openai_judge_model


async def bounded[T](
    items: Iterable[T], fn: Callable[[T], Awaitable[Any]], limit: int = 4
) -> list[Any]:
    sem = asyncio.Semaphore(limit)

    async def one(x: T) -> Any:
        async with sem:
            return await fn(x)

    return await asyncio.gather(*(one(x) for x in items))


FULL_BANK = {
    "HDFC": "HDFC Bank",
    "ICICI": "ICICI Bank",
    "SBI": "State Bank of India",
    "Axis": "Axis Bank",
    "Kotak": "Kotak Mahindra Bank",
    "PNB": "Punjab National Bank",
    "Baroda": "Bank of Baroda",
    "Canara": "Canara Bank",
    "Union": "Union Bank of India",
    "IndusInd": "IndusInd Bank",
}


def evidence_from(expected: dict[str, Any], bank: str | None = None) -> Evidence:
    """User-confirmed Evidence built straight from a case's hand-written expected facts."""
    time = expected.get("txn_time") or "00:00"
    facts: dict[str, Any] = {
        "case_type": expected["case_type"],
        "channel": expected["channel"],
        "amount_inr": expected["amount_inr"],
        "account_debited": True,
        "txn_timestamp": f"{expected['txn_date']}T{time}:00+05:30",
        "payer_bank": bank or FULL_BANK.get(expected["payer_bank"], expected["payer_bank"]),
        "payer_account_last4": expected["payer_account_last4"],
        "is_domestic": True,
    }
    if expected.get("scenario"):
        facts["scenario"] = expected["scenario"]
    ref = expected["reference"]
    facts["utr" if ref.isdigit() and len(ref) == 12 else "other_reference"] = ref
    data: dict[str, Any] = {
        "source_kind": "SMS",
        "extracted_by": "eval",
        "txn_time_known": expected.get("txn_time") is not None,
    }
    for k, v in facts.items():
        data[k] = {"value": v, "confidence": 1.0, "origin": "USER"}
    return Evidence.model_validate(data)


def by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in rows}
