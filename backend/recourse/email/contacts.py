"""The bank contacts file (`bank_contacts.yaml`): who a complaint goes to, and which domains a
bank's replies may come from. Data only from official bank websites; see the file header."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTACTS_FILE = Path(__file__).with_name("bank_contacts.yaml")

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_STOPWORDS = {"ltd", "limited", "the", "co", "plc"}


def _check_email(v: str | None) -> str | None:
    if v is not None and not _EMAIL_RE.match(v):
        raise ValueError(f"not an email address: {v!r}")
    return v


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NodalOfficer(_Model):
    name: str | None = None
    role: str | None = None
    email: str | None = None
    phone: str | None = None

    _email = field_validator("email")(_check_email)


class BankContact(_Model):
    id: str = Field(pattern=r"^[a-z0-9_]{2,32}$")
    name: str
    aliases: list[str] = Field(min_length=1)
    grievance_email: str | None = None
    nodal_officer: NodalOfficer | None = None
    complaint_form_url: str | None = None
    sender_domains: list[str] = Field(default_factory=list)
    sender_domains_basis: Literal["stated", "contact_emails", "none"] = "none"
    source_urls: list[str] = Field(min_length=1)
    verified: bool = False
    notes: str | None = None

    _email = field_validator("grievance_email")(_check_email)

    @field_validator("source_urls", "complaint_form_url")
    @classmethod
    def _https(cls, v: list[str] | str | None) -> list[str] | str | None:
        for url in [v] if isinstance(v, str) else v or []:
            if not url.startswith("https://"):
                raise ValueError(f"URL must be https: {url!r}")
        return v


class BankContacts(_Model):
    version: int
    banks: list[BankContact]

    def get(self, bank_id: str) -> BankContact | None:
        return next((b for b in self.banks if b.id == bank_id), None)

    def match(self, bank_name: str | None) -> BankContact | None:
        """The bank an extracted name refers to, or None if unknown or ambiguous.

        Matches whole words: the longest alias that appears in the name wins. A name that
        matches aliases of two different banks is ambiguous (None): the user picks instead.
        """
        if not bank_name:
            return None
        words = _normalise(bank_name)
        hits: dict[str, int] = {}
        for bank in self.banks:
            for alias in bank.aliases:
                a = _normalise(alias)
                if a and f" {a} " in f" {words} ":
                    hits[bank.id] = max(hits.get(bank.id, 0), len(a))
        if not hits:
            return None
        ranked = sorted(hits.items(), key=lambda kv: kv[1], reverse=True)
        # Another bank matched almost as specifically: ambiguous.
        if len(ranked) > 1 and ranked[1][1] >= ranked[0][1] * 0.8:
            return None
        return self.get(ranked[0][0])


def _normalise(name: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", name.lower())
    return " ".join(t for t in tokens if t not in _STOPWORDS)


@lru_cache
def load_bank_contacts(path: Path = CONTACTS_FILE) -> BankContacts:
    with path.open(encoding="utf-8") as f:
        contacts = BankContacts.model_validate(yaml.safe_load(f))
    ids = [b.id for b in contacts.banks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate bank id in the contacts file")
    return contacts
