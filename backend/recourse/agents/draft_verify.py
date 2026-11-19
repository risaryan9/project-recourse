"""The DraftGraph's mandatory `verify` step: pure Python, no LLM.

It checks the LLM's *template* (the letter with `{{slot}}` placeholders) against the slot table
built from the rule engine's `Entitlement`. A draft is only accepted if every check passes:

1. **Known placeholders only.** Every `{{name}}` exists in the slot table; nothing malformed.
2. **Required placeholders used.** Every money line, the total, the key deadline, the citations
   behind them, the transaction reference, the case tag (in the subject) and the sign-off name.
3. **No numbers typed by the LLM.** Outside placeholders there are no digits, no currency
   markers (₹, Rs, INR, rupees), no number words next to a unit ("three working days",
   "fifty thousand") and no web links. So every amount, date, duration, clause number and
   URL in the final letter came from the rule engine or the confirmed evidence.
4. **Safety.** No request for an OTP, PIN, CVV or password; no remote-access apps; no
   mention of Recourse (the letter is the customer's own).
5. **Shape.** Subject and body are non-empty and within length limits.
6. **Rendered amounts match.** After the placeholders are filled in, every ₹ amount in the
   letter is exactly one of the case's amounts (a second, independent check of rule 3).

Each problem is a short, specific message that is sent back to the LLM for the next attempt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from recourse.agents.draft_slots import (
    SLOT_LIKE_RE,
    SlotTable,
    render,
    strip_slots,
    used_slots,
)

MAX_SUBJECT_CHARS = 200
MAX_BODY_CHARS = 6_000

_NUMBER_WORDS = (
    "zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    "fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    "eighty|ninety|hundred|thousand|lakh|lakhs|crore|crores|half|double|twice"
)
_UNITS = (
    r"days?|working|calendar|business|weeks?|months?|years?|hours?|rupees?|paise|percent|"
    r"per\s*cent|%"
)
_CHECKS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\d"), "contains a digit typed directly ({match!r}); use a placeholder"),
    (
        re.compile(r"₹|\brs\.?(?=\s|$)|\binr\b|\brupees?\b|\blakhs?\b|\bcrores?\b", re.I),
        "contains a currency amount or marker typed directly ({match!r}); use a money placeholder",
    ),
    (
        re.compile(
            rf"\b(?:{_NUMBER_WORDS})\b(?:[\s-]+(?:{_NUMBER_WORDS}))*[\s-]+(?:{_UNITS})\b", re.I
        ),
        "contains a number written in words ({match!r}); use a placeholder or leave it out",
    ),
    (
        re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(?:com|in|org|net|gov)\b", re.I),
        "contains a web address or domain ({match!r}); do not include links",
    ),
    (
        re.compile(
            r"\b(otp|one[- ]time[- ]password|cvv|cvc|upi[- ]?pin|m?pin|password|passcode)\b",
            re.I,
        ),
        "mentions a secret credential ({match!r}); never mention OTPs, PINs, CVVs or passwords",
    ),
    (
        re.compile(r"\b(anydesk|teamviewer|quick ?support|screen[- ]?shar\w*)\b", re.I),
        "mentions a remote-access app ({match!r})",
    ),
    (
        re.compile(r"\brecourse\b", re.I),
        "mentions Recourse ({match!r}); the letter must read as the customer's own",
    ),
]


@dataclass(frozen=True)
class VerifyResult:
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def verify(subject: str, body: str, table: SlotTable) -> VerifyResult:
    """Check one draft template. Returns every problem found (empty list = accepted)."""
    errors: list[str] = []

    # 5. Shape
    if not subject.strip():
        errors.append("subject is empty")
    if not body.strip():
        errors.append("body is empty")
    if len(subject) > MAX_SUBJECT_CHARS:
        errors.append(f"subject is longer than {MAX_SUBJECT_CHARS} characters")
    if len(body) > MAX_BODY_CHARS:
        errors.append(f"body is longer than {MAX_BODY_CHARS} characters")
    if "\n" in subject.strip():
        errors.append("subject must be a single line")

    # 1. Known placeholders only
    used = used_slots(subject) + used_slots(body)
    for name in dict.fromkeys(used):
        if name not in table.slots:
            errors.append(f"unknown placeholder {{{{{name}}}}}; use only the listed placeholders")
    for part_name, part in (("subject", subject), ("body", body)):
        leftover = SLOT_LIKE_RE.findall(strip_slots(part))
        if leftover:
            errors.append(f"{part_name} has a malformed placeholder near {leftover[0]!r}")

    # 2. Required placeholders used
    if "case.tag" not in used_slots(subject):
        errors.append("subject must contain {{case.tag}}")
    for name in table.required:
        if name != "case.tag" and name not in used:
            errors.append(f"required placeholder {{{{{name}}}}} is not used")

    # 3 + 4. Free text typed by the LLM
    for part_name, part in (("subject", subject), ("body", body)):
        free = strip_slots(part)
        for pattern, message in _CHECKS:
            m = pattern.search(free)
            if m:
                errors.append(f"{part_name} " + message.format(match=m.group(0)))

    # 6. Rendered amounts match (only if the placeholders are all valid)
    if not any("placeholder" in e for e in errors):
        allowed = {s.value for s in table.slots.values() if s.value.startswith("₹")}
        for part in (subject, body):
            for amount in _RUPEE_RE.findall(render(part, table)):
                if amount not in allowed:
                    errors.append(f"rendered letter contains an unexpected amount {amount!r}")

    return VerifyResult(errors=list(dict.fromkeys(errors)))


_RUPEE_RE = re.compile(r"₹[\d,]+(?:\.\d{2})?")
