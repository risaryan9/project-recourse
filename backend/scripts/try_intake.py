"""Run the IntakeGraph once against the real LLM providers (uses API keys from .env; costs a
fraction of a cent per run). Use synthetic data only.

    uv run python scripts/try_intake.py "Rs 500 debited via UPI, ref 123456789012, failed"
    uv run python scripts/try_intake.py --file screenshot.png
    uv run python scripts/try_intake.py "ATM did not give cash" --file receipt.jpg
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime
from pathlib import Path

from recourse.agents.intake_graph import IntakeInput, run_intake
from recourse.evidence.models import IST
from recourse.llm import get_llm
from recourse.llm.base import Attachment

MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("text", nargs="?", help="typed/pasted text (SMS, description, email)")
    parser.add_argument("--file", action="append", default=[], help="image or PDF (repeatable)")
    parser.add_argument("-v", "--verbose", action="store_true", help="show LLM call logs")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR)
    attachments = []
    for name in args.file:
        path = Path(name)
        media_type = MEDIA_TYPES.get(path.suffix.lower())
        if media_type is None:
            parser.error(f"unsupported file type: {path.suffix}")
        attachments.append(Attachment(media_type=media_type, data=path.read_bytes()))

    intake = IntakeInput(text=args.text, attachments=attachments)
    result = asyncio.run(run_intake(get_llm(), intake, now=datetime.now(IST)))

    print(f"\nStatus: {result.status.value}   (models: {', '.join(result.models_used)})")
    if result.reason:
        print(f"Reason: {result.reason}")
    ev = result.evidence
    if ev is None:
        return
    print(f"Source: {ev.source_kind.value}\n")
    print(f"  {'FIELD':22} {'VALUE':32} CONF  QUOTE")
    for name in ev.fact_names():
        fact = ev.fact(name)
        if fact.present:
            mark = "  <- confirm" if name in result.needs_user_confirmation else ""
            quote = (fact.source_text or "")[:40]
            print(f"  {name:22} {str(fact.value):32} {fact.confidence:.2f}  {quote}{mark}")
    missing = [n for n in result.needs_user_confirmation if not ev.fact(n).present]
    if missing:
        print(f"\n  Missing, ask the user: {', '.join(missing)}")
    if result.check:
        for issue in result.check.issues:
            print(f"  [{issue.severity.value}] {issue.field}: {issue.message}")
    if ev.redactions:
        print(f"\n  ⚠ Removed sensitive data: {', '.join(r.value for r in ev.redactions)}")
    for note in ev.notes:
        print(f"  note: {note}")


if __name__ == "__main__":
    main()
