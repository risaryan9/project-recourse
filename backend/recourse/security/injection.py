"""Deterministic detection of text aimed at an AI model inside untrusted data (CLAUDE.md §5).

Detection only: untrusted text is always wrapped as data (`llm/prompting.py`) and its LLM
output is a fixed set of labels, so an injection cannot act. Detecting it still matters: a
"bank reply" that talks to an AI is not a genuine bank reply, so the workflow takes no
automatic step on it and warns the user.
"""

from __future__ import annotations

import re

_INJECTION_RE = re.compile(
    r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}\b(?:previous|prior|above|earlier|all|"
    r"your|the)\b[^.\n]{0,30}\b(?:instructions?|prompts?|rules?|messages?|context)\b"
    r"|\b(?:system\s+prompt|developer\s+message|you\s+are\s+(?:now\s+)?(?:an?\s+)?"
    r"(?:ai|assistant|language\s+model|chatbot|llm))\b"
    r"|\b(?:as\s+an\s+ai|new\s+instructions?|assistant\s*:|system\s*:)"
    r"|\b(?:mark|set|change|update)\b[^.\n]{0,30}\b(?:case|complaint|status)\b[^.\n]{0,30}"
    r"\b(?:as\s+)?(?:resolved|closed|compliant|approved)\b"
    r"|<\s*/?\s*(?:system|instructions?|untrusted_document|task)\s*>",
    re.IGNORECASE,
)


def find_injection(text: str) -> list[str]:
    """Snippets of `text` that address an AI model or try to change the case's state."""
    return [m.group(0).strip()[:80] for m in _INJECTION_RE.finditer(text)]
