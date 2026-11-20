"""`EmailChannel`: how an approved complaint reaches the bank (CLAUDE.md §3 "Email channel").

Two implementations share one interface and one send policy:

* `ManualChannel` (`manual.py`): never sends. It hands the user the recipient, subject, body
  and a `mailto:` link; the user sends it from their own mail app and confirms (`mark_sent`).
  It must always work, with or without Gmail.
* `GmailChannel` (later): sends through the user's own Gmail after the same approval.

**Send policy** (`resolve_recipient`), applied before any channel is used:

* Sandbox on (the default): the recipient is always `SANDBOX_RECIPIENT`. Nothing goes to a bank.
* Sandbox off: the recipient is the bank's grievance address from the contacts file, and only
  if that entry is `verified` **and** every rule the letter cites is verified (ADR-010).
  Otherwise the send is blocked with the reasons, and nothing is handed out.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from recourse.email.contacts import BankContact


class ChannelKind(StrEnum):
    MANUAL = "MANUAL"
    GMAIL = "GMAIL"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Recipient(_Model):
    """The policy's answer: where the email may go, or why it may not go anywhere."""

    address: str | None
    sandbox: bool
    blocked_reasons: list[str] = []

    @property
    def blocked(self) -> bool:
        return self.address is None


def resolve_recipient(
    *,
    sandbox: bool,
    sandbox_recipient: str | None,
    contact: BankContact | None,
    rules_verified: bool,
) -> Recipient:
    if sandbox:
        if not sandbox_recipient:
            return Recipient(
                address=None,
                sandbox=True,
                blocked_reasons=["Sandbox mode is on but SANDBOX_RECIPIENT is not set."],
            )
        return Recipient(address=sandbox_recipient, sandbox=True)

    reasons: list[str] = []
    if contact is None:
        reasons.append("The bank is not known; choose your bank from the list.")
    else:
        if contact.grievance_email is None:
            reasons.append(f"No complaint email is on file for {contact.name}.")
        if not contact.verified:
            reasons.append(f"The contact details for {contact.name} are not verified yet.")
    if not rules_verified:
        reasons.append("The rules this letter cites are not verified yet.")
    if reasons:
        return Recipient(address=None, sandbox=False, blocked_reasons=reasons)
    assert contact is not None and contact.grievance_email is not None
    return Recipient(address=contact.grievance_email, sandbox=False)


class OutgoingEmail(_Model):
    to: str
    subject: str
    body: str
    sandbox: bool


class SendOutcome(_Model):
    """What a channel did. ManualChannel: `sent=False` and a `mailto` for the user."""

    channel: ChannelKind
    sent: bool
    mailto: str | None = None
    message_id: str | None = None
    thread_id: str | None = None


class EmailChannel(Protocol):
    kind: ChannelKind

    async def send(self, email: OutgoingEmail) -> SendOutcome: ...
