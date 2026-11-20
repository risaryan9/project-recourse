import pytest

from recourse.security.pii import RedactionKind, luhn_valid, mask_last4, redact_text

# Standard test card numbers (Luhn-valid, not real cards).
VISA_TEST = "4111111111111111"
AMEX_TEST = "378282246310005"


def test_luhn() -> None:
    assert luhn_valid(VISA_TEST)
    assert luhn_valid(AMEX_TEST)
    assert not luhn_valid("4111111111111112")


def test_mask_last4() -> None:
    assert mask_last4("1234567890") == "XXXXXX7890"
    assert mask_last4("1234") == "1234"


@pytest.mark.parametrize(
    "text",
    [
        f"Card {VISA_TEST} was charged",
        "Card 4111 1111 1111 1111 was charged",
        "Card 4111-1111-1111-1111 was charged",
        "Amex 3782 822463 10005 used",
    ],
)
def test_card_numbers_masked_to_last4(text: str) -> None:
    out, kinds = redact_text(text)
    assert kinds == {RedactionKind.CARD_NUMBER}
    assert "1111 1111" not in out and "41111111" not in out and "822463" not in out
    assert out.count("X") >= 11


def test_non_luhn_long_number_kept() -> None:
    out, kinds = redact_text("Order 4111111111111112")
    assert out == "Order 4111111111111112"
    assert kinds == set()


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Your OTP is 482913. Do not share it.", "482913"),
        ("OTP: 4829", "4829"),
        ("one-time password 771204", "771204"),
        ("CVV 123", "123"),
        ("my UPI PIN is 9921", "9921"),
        ("MPIN-445566", "445566"),
        ("password: hunter2!x", "hunter2!x"),
        ("my password is S3cret@bank", "S3cret@bank"),
    ],
)
def test_secrets_removed_entirely(text: str, secret: str) -> None:
    out, kinds = redact_text(text)
    assert secret not in out
    assert "[REDACTED]" in out
    assert kinds == {RedactionKind.SECRET_CODE}


@pytest.mark.parametrize(
    "text",
    [
        "Never share your OTP with anyone.",
        "OTP for txn of Rs 5000 sent",
        "Enter your UPI PIN to continue",
        "Password reset link sent",
    ],
)
def test_secret_keywords_without_a_secret_left_alone(text: str) -> None:
    assert redact_text(text) == (text, set())


def test_account_number_masked_but_already_masked_left_alone() -> None:
    out, kinds = redact_text("A/c no. 123456789012 debited")
    assert out == "A/c no. XXXXXXXX9012 debited"
    assert kinds == {RedactionKind.ACCOUNT_NUMBER}
    assert redact_text("A/c XX1234 debited by Rs 500") == ("A/c XX1234 debited by Rs 500", set())


def test_bare_12_digit_utr_is_not_redacted() -> None:
    text = "UPI Ref No 412345678901"
    assert redact_text(text) == (text, set())


def test_labelled_aadhaar_masked() -> None:
    out, kinds = redact_text("Aadhaar: 2345 6789 0123")
    assert out == "Aadhaar: XXXX XXXX 0123"
    assert kinds == {RedactionKind.AADHAAR}


def test_pan_removed() -> None:
    out, kinds = redact_text("PAN ABCDE1234F on file")
    assert "ABCDE1234F" not in out
    assert kinds == {RedactionKind.PAN}


def test_redaction_is_idempotent() -> None:
    text = f"OTP 482913, A/c 123456789012, card {VISA_TEST}, PAN ABCDE1234F, Aadhaar 2345 6789 0123"
    once, _ = redact_text(text)
    twice, kinds = redact_text(once)
    assert twice == once
    assert kinds == set()


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("net banking password Secret@123. UPI Ref 631609876543", "Secret@123"),
        ("pwd abcd9 and more", "abcd9"),
        ("password Tr1cky", "Tr1cky"),
    ],
)
def test_password_without_a_colon_is_redacted(text: str, secret: str) -> None:
    # Found by the eval attack suite: "password Secret@123" leaked before.
    out, kinds = redact_text(text)
    assert secret not in out and RedactionKind.SECRET_CODE in kinds


@pytest.mark.parametrize(
    "text",
    [
        "Never share your password with anyone.",
        "Change your password regularly.",
        "Your password must have 8 characters.",
    ],
)
def test_everyday_sentences_about_passwords_are_kept(text: str) -> None:
    out, kinds = redact_text(text)
    assert out == text and not kinds
