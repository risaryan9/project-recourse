"""`ManualChannel`: the user sends the email themselves. Nothing leaves Recourse."""

from __future__ import annotations

from urllib.parse import quote

from recourse.email.channel import ChannelKind, OutgoingEmail, SendOutcome


def mailto_link(to: str, subject: str, body: str) -> str:
    """A `mailto:` URI (RFC 6068): every reserved character percent-encoded, CRLF line ends."""
    crlf_body = body.replace("\r\n", "\n").replace("\n", "\r\n")
    query = f"subject={quote(subject, safe='')}&body={quote(crlf_body, safe='')}"
    return f"mailto:{quote(to, safe='@')}?{query}"


class ManualChannel:
    kind = ChannelKind.MANUAL

    async def send(self, email: OutgoingEmail) -> SendOutcome:
        return SendOutcome(
            channel=self.kind,
            sent=False,
            mailto=mailto_link(email.to, email.subject, email.body),
        )
