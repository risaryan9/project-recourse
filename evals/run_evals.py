"""Run the Recourse eval suites and write the reports. Synthetic data only.

From backend/:
    uv run python ../evals/run_evals.py                    # all suites
    uv run python ../evals/run_evals.py --suites rules attacks

Paid suites (extraction, drafts, attacks) call the configured LLM providers; drafts also call
the judge (OPENAI_JUDGE_MODEL). Writes evals/reports/<timestamp>.json and evals/reports/latest.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from recourse.config import get_settings  # noqa: E402

from suites import attacks, drafts, extraction, rules  # noqa: E402
from suites.common import REPORTS, judge_llm, product_llm  # noqa: E402

ALL = ["rules", "extraction", "drafts", "attacks"]


def markdown(r: dict[str, Any]) -> str:
    out = ["# Eval results", "", f"Run {r['run_at']} ({r['seconds']} s). Synthetic data only.", ""]
    if r.get("note"):
        out += [f"> {r['note']}", ""]
    out += ["| Suite | Result | Bar |", "|---|---|---|"]
    if "rules" in r:
        x = r["rules"]
        out.append(
            f"| Rule engine (golden cases, source quotes) | {x['passed']}/{x['passed'] + x['failed']} pass ({x['pass_rate']}%) | 100% |"
        )
    if "extraction" in r:
        x = r["extraction"]
        out.append(
            f"| Extraction ({x['cases']} cases) | {x['overall']}% of fields correct; {x['fully_correct_cases']}/{x['cases']} cases fully correct | report |"
        )
    if "drafts" in r:
        x = r["drafts"]
        ms = ", ".join(f"{k} {v}" for k, v in x["mean_scores"].items())
        out.append(
            f"| Draft quality ({x['cases']} letters) | {x['drafted']} drafted, {x['failed_closed']} failed closed; judge (1-5): {ms}; invented rules: {x['invented_rule']} | report |"
        )
    if "attacks" in r:
        x = r["attacks"]
        out.append(
            f"| Attack suite ({x['attacks']} attacks) | **{x['successful']} successful** ({x['errors']} errors) | 0 |"
        )
    if "extraction" in r:
        x = r["extraction"]
        out += ["", "## Extraction by field", "", "| Field | Accuracy |", "|---|---|"]
        out += [f"| {k} | {v}% |" for k, v in x["field_accuracy"].items()]
        out += ["", "| Input kind | Accuracy |", "|---|---|"]
        out += [f"| {k} | {v}% |" for k, v in x["kind_accuracy"].items()]
        out += [
            "",
            f"Wrong fields: {x['wrong_fields']}, of which {x['wrong_but_flagged']} were flagged for the user to check. Models: {', '.join(x['models'])}.",
        ]
        misses = [
            f"- `{row['id']}`: {', '.join(row['wrong'])}" for row in x["rows"] if row.get("wrong")
        ]
        out += ["", "Misses:", *misses] if misses else []
    if "drafts" in r:
        x = r["drafts"]
        out += [
            "",
            "## Draft quality",
            "",
            f"Drafting models: {', '.join(x['drafting_models'])}. Judge: {x['judge_model']}. First-attempt pass of the verify step: {x['first_attempt_pass']}/{x['drafted']}.",
            "",
            "| Letter | Status | Attempts | Accuracy | Grounding | Recipient | Completeness | Tone | Issues |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for row in x["rows"]:
            j = row.get("judge")
            if j:
                issues = "; ".join(j["issues"])[:160] or "none"
                out.append(
                    f"| {row['id']} | {row['status']} | {row['attempts']} | {j['accuracy']} | {j['grounding']} | {j['recipient']} | {j['completeness']} | {j['tone']} | {issues} |"
                )
            else:
                out.append(
                    f"| {row['id']} | {row.get('status', 'error')} | {row.get('attempts', '-')} | | | | | | {row.get('error', '')} |"
                )
    if "attacks" in r:
        out += [
            "",
            "## Attacks",
            "",
            "| Attack | Goal | Succeeded | Observed |",
            "|---|---|---|---|",
        ]
        for row in r["attacks"]["rows"]:
            s = "error" if row["succeeded"] is None else ("**yes**" if row["succeeded"] else "no")
            out.append(f"| {row['id']} | {row['goal']} | {s} | {row['observed']} |")
    return "\n".join(out) + "\n"


async def main(suites: list[str], note: str | None) -> None:
    settings = get_settings()
    started = time.time()
    report: dict[str, Any] = {"run_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")}
    if note:
        report["note"] = note
    if "rules" in suites:
        report["rules"] = rules.run()
        print("rules:", report["rules"]["summary"], flush=True)
    llm = product_llm(settings) if set(suites) & {"extraction", "drafts", "attacks"} else None
    if "extraction" in suites:
        report["extraction"] = await extraction.run(llm)
        print("extraction:", report["extraction"]["overall"], "%", flush=True)
    if "drafts" in suites:
        judge, judge_model = judge_llm(settings)
        report["drafts"] = await drafts.run(llm, judge, judge_model)
        print("drafts:", report["drafts"]["mean_scores"], flush=True)
    if "attacks" in suites:
        report["attacks"] = await attacks.run(llm)
        print("attacks: successful", report["attacks"]["successful"], flush=True)
    report["seconds"] = round(time.time() - started)
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    (REPORTS / f"{stamp}.json").write_text(
        json.dumps(report, indent=1, default=str, ensure_ascii=False)
    )
    (REPORTS / "latest.md").write_text(markdown(report))
    print(f"wrote evals/reports/{stamp}.json and evals/reports/latest.md")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--suites", nargs="+", choices=ALL, default=ALL)
    ap.add_argument("--note", help="A line for the top of the report (e.g. which provider served).")
    args = ap.parse_args()
    asyncio.run(main(args.suites, args.note))
