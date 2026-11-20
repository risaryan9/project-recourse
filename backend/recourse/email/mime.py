"""Building outgoing emails and reading Gmail threads (pure functions, no network).

Bank replies usually quote the customer's letter underneath, amounts and all. `reply_text`
keeps only what the bank wrote, so the judge never mistakes the customer's own claims for the
bank's answer.
"""

from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from typing import Any

MAX_REPLY_CHARS = 20_000


def build_raw(*, sender: str, to: str, subject: str, body: str) -> str:
    """The base64url RFC 2822 message Gmail's messages.send expects."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body, subtype="plain", charset="utf-8")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


@dataclass(frozen=True)
class ThreadMessage:
    id: str
    sender: str  # lower-cased email address
    received_at: datetime
    subject: str
    text: str  # plain text as written (quotes not yet removed)


def _header(headers: list[dict[str, str]], name: str) -> str:
    return next((h["value"] for h in headers if h.get("name", "").lower() == name.lower()), "")


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _strip_html(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style|blockquote)[^>]*>.*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", markup)
    return html.unescape(re.sub(r"<[^>]+>", " ", markup))


def _body(payload: dict[str, Any]) -> str:
    """The text/plain part if there is one, else the text/html part as text."""
    plain: list[str] = []
    rich: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            rich.append(_strip_html(_decode(data)))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return "\n".join(plain) if plain else "\n".join(rich)


def parse_thread(thread: dict[str, Any]) -> list[ThreadMessage]:
    out = []
    for m in thread.get("messages", []):
        payload = m.get("payload", {})
        headers = payload.get("headers", [])
        sender = parseaddr(_header(headers, "From"))[1].lower()
        when = datetime.fromtimestamp(int(m.get("internalDate", "0")) / 1000, tz=UTC)
        out.append(
            ThreadMessage(
                id=m["id"],
                sender=sender,
                received_at=when,
                subject=_header(headers, "Subject"),
                text=_body(payload),
            )
        )
    return out


def recipients(raw_header: str) -> list[str]:
    return [a.lower() for _, a in getaddresses([raw_header]) if a]


_QUOTE_START = re.compile(
    r"^(on .{0,200}wrote:\s*$|-{2,}\s*original message\s*-{2,}|from:\s.+|>.*)",
    re.IGNORECASE,
)


def reply_text(text: str) -> str:
    """What the sender wrote above the quoted earlier message ("On ... wrote:", "> ...")."""
    kept = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if _QUOTE_START.match(line.strip()):
            break
        kept.append(line)
    out = "\n".join(kept).strip()
    return (out or text.strip())[:MAX_REPLY_CHARS]
