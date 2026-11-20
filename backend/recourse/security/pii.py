"""Redact sensitive data from text before it is stored or shown.

What we redact (CLAUDE.md §5):

* **Card numbers**: 13-19 digits (optionally grouped by spaces/dashes) that pass the Luhn check.
  Masked to the last 4 digits.
* **Secret codes**: a 3-8 digit OTP, CVV/CVC, PIN / UPI PIN or passcode that follows its
  keyword, and any token after "password:" / "password is". Replaced entirely; we never keep
  any character of a secret.
* **Account numbers**: 5-18 digits after an account label ("A/c", "Acct", "Account no.").
  Masked to the last 4 digits. Bank SMS usually show "A/c XX1234" already; that is left alone.
* **Aadhaar numbers**: 12 digits after an Aadhaar/UID label. Masked to the last 4 digits.
  Bare 12-digit numbers are NOT treated as Aadhaar, because UPI UTRs are also 12 digits.
* **PAN**: the AAAAA9999A pattern. Replaced entirely.

The rules are deliberately conservative: over-redacting a little is better than storing a secret.
`redact_text` is pure (no I/O), so it is cheap to call on every string we keep.
"""

from __future__ import annotations

import re
from enum import StrEnum


class RedactionKind(StrEnum):
    CARD_NUMBER = "CARD_NUMBER"
    SECRET_CODE = "SECRET_CODE"  # OTP, CVV, PIN, UPI PIN, password
    ACCOUNT_NUMBER = "ACCOUNT_NUMBER"
    AADHAAR = "AADHAAR"
    PAN = "PAN"


SECRET_PLACEHOLDER = "[REDACTED]"

# Contiguous 13-19 digits, or 4-4-4-(1..7) / Amex 4-6-5 groups separated by a space or dash.
_CARD_RE = re.compile(
    r"(?<![\d-])(?:\d{13,19}|\d{4}([ -])\d{4}\1\d{4}\1\d{1,7}|\d{4}([ -])\d{6}\2\d{5})(?![\d-])"
)
# Keyword, then up to 10 non-digit characters (": ", " is ", " - "), then the code itself.
_SECRET_RE = re.compile(
    r"(?i)\b(otp|one[- ]time[- ]password|cvv2?|cvc|upi[- ]?pin|m?pin|passcode)\b"
    r"([^\d\n]{0,10}?)(\d{3,8})(?!\d)"
)
# Passwords can be any token, so mask either an explicit "password: x" / "password is x" form,
# or "password x" where x looks like a password (it has a digit or a symbol). The second form
# keeps everyday sentences ("never share your password with anyone") intact.
_PASSWORD_RE = re.compile(
    r"(?i)\b(password|passwd|pwd)"
    r"(\s*(?:[:=]|\bis\b)\s*(?=\S{4,64})|\s+(?=\S*[\d@#$%^&*!?_~+=]\S*(?:\s|$|[.,;]))(?=\S{4,64}))"
    r"(\S{4,64}?)(?=[.,;]?(?:\s|$))"
)
_ACCOUNT_RE = re.compile(
    r"(?i)\b(a/c|acct|account)(\s*(?:no\.?|number|num)?[\s:.#-]{0,5})([xX*]*)(\d{5,18})\b"
)
_AADHAAR_RE = re.compile(
    r"(?i)\b(aadhaar|aadhar|uid)(\b[^\d\n]{0,20}?)(\d{4})[ -]?(\d{4})[ -]?(\d{4})\b"
)
_PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")


def luhn_valid(digits: str) -> bool:
    """Luhn checksum used by payment card numbers."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def mask_last4(digits: str) -> str:
    """'1234567890' -> 'XXXXXX7890'."""
    return "X" * max(len(digits) - 4, 0) + digits[-4:]


def redact_text(text: str) -> tuple[str, set[RedactionKind]]:
    """Return `text` with sensitive data masked, and the kinds of data that were found."""
    found: set[RedactionKind] = set()

    # Secrets first, so an OTP that happens to look like a number is never kept in any form.
    def _secret(m: re.Match[str]) -> str:
        found.add(RedactionKind.SECRET_CODE)
        return f"{m.group(1)}{m.group(2)}{SECRET_PLACEHOLDER}"

    text = _SECRET_RE.sub(_secret, text)
    text = _PASSWORD_RE.sub(_secret, text)

    def _card(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if not luhn_valid(digits):
            return m.group(0)
        found.add(RedactionKind.CARD_NUMBER)
        return mask_last4(digits)

    text = _CARD_RE.sub(_card, text)

    def _account(m: re.Match[str]) -> str:
        found.add(RedactionKind.ACCOUNT_NUMBER)
        digits = m.group(4)
        return f"{m.group(1)}{m.group(2)}{'X' * (len(m.group(3)) + len(digits) - 4)}{digits[-4:]}"

    text = _ACCOUNT_RE.sub(_account, text)

    def _aadhaar(m: re.Match[str]) -> str:
        found.add(RedactionKind.AADHAAR)
        return f"{m.group(1)}{m.group(2)}XXXX XXXX {m.group(5)}"

    text = _AADHAAR_RE.sub(_aadhaar, text)

    def _pan(m: re.Match[str]) -> str:
        found.add(RedactionKind.PAN)
        return SECRET_PLACEHOLDER

    text = _PAN_RE.sub(_pan, text)
    return text, found
