"""Bank contacts file, the send policy and ManualChannel. No network. Synthetic data only."""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from recourse.email.channel import ChannelKind, OutgoingEmail, resolve_recipient
from recourse.email.contacts import BankContact, load_bank_contacts
from recourse.email.manual import ManualChannel, mailto_link

CONTACTS = load_bank_contacts()


def contact(**kw: object) -> BankContact:
    data: dict[str, object] = {
        "id": "example",
        "name": "Example Bank",
        "aliases": ["example bank"],
        "grievance_email": "care@example.bank.in",
        "source_urls": ["https://example.bank.in/grievance"],
        "verified": True,
    }
    data.update(kw)
    return BankContact.model_validate(data)


# --- contacts file ------------------------------------------------------------------------------


def test_contacts_file_loads_and_nothing_is_verified_yet() -> None:
    assert len(CONTACTS.banks) >= 10
    # Entries are flipped to verified only by a person checking the live page.
    assert not any(b.verified for b in CONTACTS.banks)
    for b in CONTACTS.banks:
        assert b.source_urls, b.id
        if b.sender_domains:
            assert b.sender_domains_basis != "none", b.id
        if b.grievance_email:
            domain = b.grievance_email.split("@")[1]
            assert domain in b.sender_domains, b.id


@pytest.mark.parametrize(
    ("name", "bank_id"),
    [
        ("HDFC Bank Ltd", "hdfc"),
        ("STATE BANK OF INDIA", "sbi"),
        ("Union Bank of India", "union"),
        ("Bank of Baroda", "bob"),
        ("Punjab National Bank, New Delhi", "pnb"),
        ("Kotak Mahindra Bank Limited", "kotak"),
        ("Example Bank", None),
        ("", None),
        (None, None),
    ],
)
def test_bank_matching(name: str | None, bank_id: str | None) -> None:
    match = CONTACTS.match(name)
    assert (match.id if match else None) == bank_id


def test_a_name_matching_two_banks_is_ambiguous() -> None:
    assert CONTACTS.match("HDFC Bank or ICICI Bank") is None


def test_contacts_reject_bad_data() -> None:
    with pytest.raises(ValueError):
        contact(grievance_email="not-an-email")
    with pytest.raises(ValueError):
        contact(source_urls=["http://example.bank.in"])


# --- send policy --------------------------------------------------------------------------------


def test_sandbox_always_goes_to_the_sandbox_recipient() -> None:
    r = resolve_recipient(
        sandbox=True, sandbox_recipient="me@example.com", contact=contact(), rules_verified=True
    )
    assert r.address == "me@example.com" and r.sandbox and not r.blocked
    r = resolve_recipient(
        sandbox=True, sandbox_recipient="me@example.com", contact=None, rules_verified=False
    )
    assert r.address == "me@example.com"


def test_sandbox_without_a_recipient_is_blocked() -> None:
    r = resolve_recipient(sandbox=True, sandbox_recipient=None, contact=None, rules_verified=True)
    assert r.blocked and "SANDBOX_RECIPIENT" in r.blocked_reasons[0]


def test_real_send_needs_a_verified_contact_and_verified_rules() -> None:
    ok = resolve_recipient(
        sandbox=False, sandbox_recipient=None, contact=contact(), rules_verified=True
    )
    assert ok.address == "care@example.bank.in" and not ok.sandbox

    cases = [
        (None, True, "not known"),
        (contact(verified=False), True, "not verified"),
        (contact(grievance_email=None), True, "No complaint email"),
        (contact(), False, "rules"),
    ]
    for c, rules_ok, reason in cases:
        r = resolve_recipient(
            sandbox=False, sandbox_recipient="me@example.com", contact=c, rules_verified=rules_ok
        )
        assert r.blocked, reason
        assert any(reason in m for m in r.blocked_reasons), r.blocked_reasons


# --- ManualChannel --------------------------------------------------------------------------------


def test_mailto_encodes_everything() -> None:
    body = "Dear Sir/Madam,\nAmount: ₹2,450.00 & more?\n\nYours faithfully,\nAsha"
    link = mailto_link("care@example.bank.in", "[RC-7F3K] Complaint #1", body)
    parts = urlsplit(link)
    assert parts.scheme == "mailto" and unquote(parts.path) == "care@example.bank.in"
    query = parse_qs(parts.query)
    assert query["subject"] == ["[RC-7F3K] Complaint #1"]
    assert query["body"] == [body.replace("\n", "\r\n")]
    assert "\n" not in link and " " not in link


async def test_manual_channel_never_sends() -> None:
    out = await ManualChannel().send(
        OutgoingEmail(to="me@example.com", subject="s", body="b", sandbox=True)
    )
    assert out.channel is ChannelKind.MANUAL
    assert not out.sent and out.message_id is None
    assert out.mailto is not None and out.mailto.startswith("mailto:me@example.com?")
