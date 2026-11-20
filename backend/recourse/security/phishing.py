"""Deterministic phishing checks for messages that claim to come from a bank (CLAUDE.md §5).

Pure Python, no LLM: a phishing message never needs a model to be caught, and the model never
gets to decide that a dangerous message is safe. Flags a "bank reply" that:

* asks for an OTP, PIN, CVV, password or card details (a bank never does),
* asks the customer to install or open a remote-access app (AnyDesk, TeamViewer, ...),
* asks for a payment ("fee", "deposit", "charges") to release, unlock or process a refund,
* links to a web address outside the bank's known domains and official government sites.

Real bank emails often say "never share your OTP"; a request preceded by a negation is not
flagged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class PhishingSignal(StrEnum):
    SECRET_REQUEST = "SECRET_REQUEST"
    REMOTE_ACCESS_APP = "REMOTE_ACCESS_APP"
    PAY_TO_RELEASE = "PAY_TO_RELEASE"
    UNKNOWN_LINK = "UNKNOWN_LINK"


SIGNAL_TEXT: dict[PhishingSignal, str] = {
    PhishingSignal.SECRET_REQUEST: "It asks for an OTP, PIN, CVV, password or card details.",
    PhishingSignal.REMOTE_ACCESS_APP: "It asks you to use a remote-access app.",
    PhishingSignal.PAY_TO_RELEASE: "It asks you to pay something before you get your money back.",
    PhishingSignal.UNKNOWN_LINK: "It links to a website that is not your bank's.",
}

# Domains that are always fine to link to (official regulators / government portals).
OFFICIAL_DOMAINS = ("rbi.org.in", "npci.org.in", "cybercrime.gov.in", "gov.in")

_SECRET = (
    r"(?:otp|one[\s-]*time[\s-]*password|upi[\s-]*pin|m?pin|atm[\s-]*pin|cvv|cvc|"
    r"password|passcode|card[\s-]*(?:number|details)|expiry[\s-]*date|net[\s-]*banking\s+"
    r"(?:id|credentials|login))"
)
_ASK = (
    r"(?:share|send|provide|give|tell|enter|type|confirm|verify|update|reply\s+with|"
    r"submit|forward|mention|let\s+us\s+know)"
)
_SECRET_REQUEST_RE = re.compile(
    rf"\b{_ASK}\b[^.?!\n]{{0,60}}?\b{_SECRET}\b|\b{_SECRET}\b[^.?!\n]{{0,30}}?\b(?:required|"
    r"needed|to\s+(?:proceed|process|verify|complete))\b",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"\b(?:never|not|don'?t|do\s+not|no\s+one|nobody|won'?t|will\s+not|doesn'?t|does\s+not|"
    r"under\s+no\s+circumstances?)\b[^.?!\n]{0,40}$",
    re.IGNORECASE,
)
_REMOTE_RE = re.compile(
    r"\b(?:any\s*desk|team\s*viewer|quick\s*support|rust\s*desk|air\s*droid|"
    r"ammyy|ultra\s*viewer|splashtop|screen[\s-]*shar(?:e|ing)|remote[\s-]*access\s+app)\b",
    re.IGNORECASE,
)
_PAY_RE = re.compile(
    r"\b(?:pay|deposit|transfer|send)\b[^.?!\n]{0,60}?\b(?:fee|charges?|deposit|amount|"
    r"tax|gst)\b[^.?!\n]{0,60}?\b(?:refund|reversal|release|unlock|process|claim|"
    r"compensation)\b|\b(?:processing|release|unlock(?:ing)?|activation|verification)\s+"
    r"(?:fee|charges?)\b|\brefundable\s+deposit\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(
    r"\b(?:https?://|www\.)([a-z0-9.-]+\.[a-z]{2,})(?:[:/][^\s]*)?"
    r"|\b([a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|in|net|org|co|io|xyz|top|info|link|ly|me|app))"
    r"(?:/[^\s]*)?\b",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[\w.+-]+@([a-z0-9.-]+\.[a-z]{2,})", re.IGNORECASE)


@dataclass(frozen=True)
class PhishingScan:
    signals: list[PhishingSignal] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)  # short matched snippets, for the UI

    @property
    def suspicious(self) -> bool:
        return bool(self.signals)

    @property
    def reasons(self) -> list[str]:
        return [SIGNAL_TEXT[s] for s in self.signals]


def domain_allowed(host: str, allowed: tuple[str, ...] | list[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in (a.lower() for a in allowed))


def scan(text: str, bank_domains: list[str]) -> PhishingScan:
    signals: list[PhishingSignal] = []
    evidence: list[str] = []

    def hit(signal: PhishingSignal, snippet: str) -> None:
        if signal not in signals:
            signals.append(signal)
        evidence.append(snippet.strip()[:80])

    for m in _SECRET_REQUEST_RE.finditer(text):
        before = text[max(0, m.start() - 60) : m.start()]
        if not _NEGATION_RE.search(before) and not _NEGATION_RE.search(m.group(0)):
            hit(PhishingSignal.SECRET_REQUEST, m.group(0))
    for m in _REMOTE_RE.finditer(text):
        before = text[max(0, m.start() - 60) : m.start()]
        if not _NEGATION_RE.search(before):
            hit(PhishingSignal.REMOTE_ACCESS_APP, m.group(0))
    for m in _PAY_RE.finditer(text):
        before = text[max(0, m.start() - 60) : m.start()]
        if not _NEGATION_RE.search(before):
            hit(PhishingSignal.PAY_TO_RELEASE, m.group(0))

    allowed = (*bank_domains, *OFFICIAL_DOMAINS)
    email_hosts = {m.group(1).lower() for m in _EMAIL_RE.finditer(text)}
    for m in _URL_RE.finditer(text):
        host = (m.group(1) or m.group(2) or "").lower()
        if not host or host in email_hosts:
            continue  # an email address's domain is not a link
        if not domain_allowed(host, allowed):
            hit(PhishingSignal.UNKNOWN_LINK, m.group(0))
    return PhishingScan(signals=signals, evidence=evidence)
