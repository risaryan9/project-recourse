"""Short, human-friendly case tags for email subjects, e.g. "RC-7F3K".

Crockford base32 alphabet without I, L, O, U, so tags are easy to read aloud and type.
4 characters give about a million tags; uniqueness is enforced by the database
(`cases.case_tag` is UNIQUE), and callers retry on a collision.
"""

from __future__ import annotations

import re
import secrets

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PREFIX = "RC-"
LENGTH = 4
CASE_TAG_RE = re.compile(rf"^{PREFIX}[{ALPHABET}]{{{LENGTH}}}$")


def new_case_tag() -> str:
    # Not for use inside Temporal workflow code (random); call it from activities or the API.
    return PREFIX + "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
