"""Build the synthetic extraction dataset: SMS texts, descriptions and rendered screenshots.

Everything here is invented: banks are real names (the product's subject) but every amount,
reference, account digit, name and UPI ID is made up. Expected answers are written by hand in
CASES, never taken from model output.

Run from backend/:  uv run python ../evals/make_synthetic.py
Writes evals/datasets/extraction.jsonl and data/synthetic/screens/*.png
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SCREENS = ROOT / "data" / "synthetic" / "screens"
DATASET = ROOT / "evals" / "datasets" / "extraction.jsonl"

FONT_DIRS = [Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/truetype/noto")]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = (
        ["DejaVuSans-Bold.ttf", "NotoSans-Bold.ttf"]
        if bold
        else ["DejaVuSans.ttf", "NotoSans-Regular.ttf"]
    )
    for d in FONT_DIRS:
        for n in names:
            if (d / n).exists():
                return ImageFont.truetype(str(d / n), size)
    raise SystemExit("Need DejaVu Sans or Noto Sans (they have the ₹ glyph).")


# Each case: the input the user gives, and the facts a correct extraction returns.
# expected keys: case_type, scenario, channel, amount_inr, txn_date, txn_time (or None),
# reference, payer_bank, payer_account_last4
CASES: list[dict] = [
    # --- SMS text ------------------------------------------------------------------------------
    dict(
        id="sms-01",
        kind="sms",
        text="HDFC Bank: Rs 2,450.00 debited from A/c XX1234 via UPI on 14-11-26 18:05 to ROHAN KITCHEN (rohankitchen@okicici), UPI Ref No 631845207719. Transaction FAILED. Amount not credited to beneficiary.",
        expected=dict(
            case_type="failed_transaction",
            scenario="BENEFICIARY_NOT_CREDITED",
            channel="UPI",
            amount_inr="2450.00",
            txn_date="2026-11-14",
            txn_time="18:05",
            reference="631845207719",
            payer_bank="HDFC",
            payer_account_last4="1234",
        ),
    ),
    dict(
        id="sms-02",
        kind="sms",
        text="Dear Customer, your A/c XXXXXX7781 is debited for INR 1,200.00 on 16/11/2026 at ATM ID S1NW0123, Andheri. Cash not dispensed. RRN 632011458873. If not done by you, call 1800-XXX. -ICICI Bank",
        expected=dict(
            case_type="failed_transaction",
            scenario="CASH_NOT_DISPENSED",
            channel="ATM",
            amount_inr="1200.00",
            txn_date="2026-11-16",
            txn_time=None,
            reference="632011458873",
            payer_bank="ICICI",
            payer_account_last4="7781",
        ),
    ),
    dict(
        id="sms-03",
        kind="sms",
        text="SBI: Rs.5000.00 transferred from A/c **4410 to A/c **9921 via IMPS on 12Nov26 09:42. IMPS Ref 631609876543. Status: FAILED. Beneficiary not credited.",
        expected=dict(
            case_type="failed_transaction",
            scenario="BENEFICIARY_NOT_CREDITED",
            channel="IMPS",
            amount_inr="5000.00",
            txn_date="2026-11-12",
            txn_time="09:42",
            reference="631609876543",
            payer_bank="SBI",
            payer_account_last4="4410",
        ),
    ),
    dict(
        id="sms-04",
        kind="sms",
        text="Axis Bank: Rs 18,500 debited from your a/c XX4410 on 19-11-2026 21:38 by UPI to PAYMATE SERVICES. UPI Ref 632377190455. Not you? Call 18004195959 to block.",
        expected=dict(
            case_type="fraud",
            scenario=None,
            channel="UPI",
            amount_inr="18500.00",
            txn_date="2026-11-19",
            txn_time="21:38",
            reference="632377190455",
            payer_bank="Axis",
            payer_account_last4="4410",
        ),
    ),
    dict(
        id="sms-05",
        kind="sms",
        text="Kotak Bank: INR 799.00 spent on Card XX3302 at FRESHMART on 10-Nov-26 at 19:12. Txn declined by merchant but amount debited. Ref 631402220198.",
        expected=dict(
            case_type="failed_transaction",
            scenario="MERCHANT_NOT_CONFIRMED",
            channel="CARD_POS",
            amount_inr="799.00",
            txn_date="2026-11-10",
            txn_time="19:12",
            reference="631402220198",
            payer_bank="Kotak",
            payer_account_last4="3302",
        ),
    ),
    dict(
        id="sms-06",
        kind="sms",
        text="PNB: Your A/c XX6655 debited Rs. 3,000.00 on 13/11/26 for UPI txn to meerastores@oksbi. UTR 631712345670. Txn status pending; beneficiary not credited.",
        expected=dict(
            case_type="failed_transaction",
            scenario="BENEFICIARY_NOT_CREDITED",
            channel="UPI",
            amount_inr="3000.00",
            txn_date="2026-11-13",
            txn_time=None,
            reference="631712345670",
            payer_bank="PNB",
            payer_account_last4="6655",
        ),
    ),
    dict(
        id="sms-07",
        kind="sms",
        text="Bank of Baroda: Rs 2,000.00 withdrawn at ATM BOBN0417 on 15-11-2026 11:20 from A/c ...8890. Cash not dispensed? RRN 631911002345.",
        expected=dict(
            case_type="failed_transaction",
            scenario="CASH_NOT_DISPENSED",
            channel="ATM",
            amount_inr="2000.00",
            txn_date="2026-11-15",
            txn_time="11:20",
            reference="631911002345",
            payer_bank="Baroda",
            payer_account_last4="8890",
        ),
    ),
    dict(
        id="sms-08",
        kind="sms",
        text="Canara Bank: Your a/c XX2201 is debited by Rs.45,000.00 on 18-11-26 02:14 via IMPS to an unknown beneficiary. IMPS Ref 632202445566. If not initiated by you, report immediately.",
        expected=dict(
            case_type="fraud",
            scenario=None,
            channel="IMPS",
            amount_inr="45000.00",
            txn_date="2026-11-18",
            txn_time="02:14",
            reference="632202445566",
            payer_bank="Canara",
            payer_account_last4="2201",
        ),
    ),
    dict(
        id="sms-09",
        kind="sms",
        text="Union Bank: A/c *5150 Debited INR 1,499.00 on 11-11-2026 by NACH towards GYMPRO MEMBERSHIP. Mandate was cancelled on 01-10-2026. Ref NACH631500667788.",
        expected=dict(
            case_type="failed_transaction",
            scenario="MANDATE_REVOKED_DEBIT",
            channel="NACH",
            amount_inr="1499.00",
            txn_date="2026-11-11",
            txn_time=None,
            reference="NACH631500667788",
            payer_bank="Union",
            payer_account_last4="5150",
        ),
    ),
    dict(
        id="sms-10",
        kind="sms",
        text="IndusInd Bank: Rs 650.00 debited from A/c XX7012 on 17/11/26 20:45 for UPI payment to swiggy@icici. UPI Ref 632100123987. Payment failed at merchant; amount debited.",
        expected=dict(
            case_type="failed_transaction",
            scenario="MERCHANT_NOT_CONFIRMED",
            channel="UPI",
            amount_inr="650.00",
            txn_date="2026-11-17",
            txn_time="20:45",
            reference="632100123987",
            payer_bank="IndusInd",
            payer_account_last4="7012",
        ),
    ),
    dict(
        id="sms-11",
        kind="sms",
        text="HDFC Bank: OTP 482913 for txn of Rs 9,999.00 on card XX4421. Then: Rs 9,999.00 debited on card XX4421 at ONLINE-GADGETS on 16-11-26 23:02. Not you? SMS BLOCK 4421 to 7308080808. Ref 632001998877.",
        expected=dict(
            case_type="fraud",
            scenario=None,
            channel="CARD_NOT_PRESENT",
            amount_inr="9999.00",
            txn_date="2026-11-16",
            txn_time="23:02",
            reference="632001998877",
            payer_bank="HDFC",
            payer_account_last4="4421",
        ),
    ),
    dict(
        id="sms-12",
        kind="sms",
        text="ICICI Bank: Rs 7,500.00 debited from A/c XX3399 via UPI on 09-11-26 13:30. UPI Ref No 631313131313. Transaction failed - beneficiary bank not reachable.",
        expected=dict(
            case_type="failed_transaction",
            scenario="BENEFICIARY_NOT_CREDITED",
            channel="UPI",
            amount_inr="7500.00",
            txn_date="2026-11-09",
            txn_time="13:30",
            reference="631313131313",
            payer_bank="ICICI",
            payer_account_last4="3399",
        ),
    ),
    # --- The customer's own words ------------------------------------------------------------
    dict(
        id="desc-01",
        kind="description",
        text="I tried to send 4000 rupees to my brother on UPI from my SBI account ending 5521 on 8 November 2026 around 7 pm. The money left my account but he never got it. The UPI reference is 631208765432.",
        expected=dict(
            case_type="failed_transaction",
            scenario="BENEFICIARY_NOT_CREDITED",
            channel="UPI",
            amount_inr="4000.00",
            txn_date="2026-11-08",
            txn_time=None,
            reference="631208765432",
            payer_bank="SBI",
            payer_account_last4="5521",
        ),
    ),
    dict(
        id="desc-02",
        kind="description",
        text="Yesterday (19 Nov 2026) I went to an Axis Bank ATM and asked for 3,500. The machine made noise but gave no cash, and my Axis account (last digits 0098) was debited. The slip said RRN 632311447788.",
        expected=dict(
            case_type="failed_transaction",
            scenario="CASH_NOT_DISPENSED",
            channel="ATM",
            amount_inr="3500.00",
            txn_date="2026-11-19",
            txn_time=None,
            reference="632311447788",
            payer_bank="Axis",
            payer_account_last4="0098",
        ),
    ),
    dict(
        id="desc-03",
        kind="description",
        text="Someone took Rs 12,000 from my Kotak savings account ending 6612 on 18 Nov 2026 at 10:15 pm through UPI. I never shared my PIN or any OTP. Reference number 632288990011.",
        expected=dict(
            case_type="fraud",
            scenario=None,
            channel="UPI",
            amount_inr="12000.00",
            txn_date="2026-11-18",
            txn_time="22:15",
            reference="632288990011",
            payer_bank="Kotak",
            payer_account_last4="6612",
        ),
    ),
    dict(
        id="desc-04",
        kind="description",
        text="My PNB account XX3141 was charged 1,250 rupees for a card payment at a petrol pump on 14/11/2026 but the machine showed 'transaction declined' and I paid again in cash. Card ref 631812340987.",
        expected=dict(
            case_type="failed_transaction",
            scenario="MERCHANT_NOT_CONFIRMED",
            channel="CARD_POS",
            amount_inr="1250.00",
            txn_date="2026-11-14",
            txn_time=None,
            reference="631812340987",
            payer_bank="PNB",
            payer_account_last4="3141",
        ),
    ),
    dict(
        id="desc-05",
        kind="description",
        text="I sent Rs 2,200 by IMPS from my Canara Bank account ending 7744 to my landlord on 15 November 2026 at 10:05. It shows failed but the money has not come back. IMPS reference 631910050505.",
        expected=dict(
            case_type="failed_transaction",
            scenario="BENEFICIARY_NOT_CREDITED",
            channel="IMPS",
            amount_inr="2200.00",
            txn_date="2026-11-15",
            txn_time="10:05",
            reference="631910050505",
            payer_bank="Canara",
            payer_account_last4="7744",
        ),
    ),
    # --- Rendered screenshots ----------------------------------------------------------------
    dict(
        id="img-01",
        kind="sms_screen",
        sender="AX-HDFCBK",
        text="Rs 3,200.00 debited from A/c XX5521 via UPI on 15-11-26 20:14 to MEERA STORES (meerastores@oksbi). UPI Ref No 632115094471. Transaction FAILED. Amount not credited to beneficiary.",
        expected=dict(
            case_type="failed_transaction",
            scenario="BENEFICIARY_NOT_CREDITED",
            channel="UPI",
            amount_inr="3200.00",
            txn_date="2026-11-15",
            txn_time="20:14",
            reference="632115094471",
            payer_bank="HDFC",
            payer_account_last4="5521",
        ),
    ),
    dict(
        id="img-02",
        kind="sms_screen",
        sender="VM-ICICIB",
        text="ICICI Bank Acct XX7781 debited for Rs 2,500.00 on 17-Nov-26; ATM ID S1NW0456. Cash not dispensed. RRN 632211334455.",
        expected=dict(
            case_type="failed_transaction",
            scenario="CASH_NOT_DISPENSED",
            channel="ATM",
            amount_inr="2500.00",
            txn_date="2026-11-17",
            txn_time=None,
            reference="632211334455",
            payer_bank="ICICI",
            payer_account_last4="7781",
        ),
    ),
    dict(
        id="img-03",
        kind="sms_screen",
        sender="JD-SBIINB",
        text="Dear SBI UPI user, A/c X0042 debited by 25000.0 on date 19Nov26 trf to PRIZE CLAIM DESK Refno 632390817263. If not u? call 1800111109. -SBI",
        expected=dict(
            case_type="fraud",
            scenario=None,
            channel="UPI",
            amount_inr="25000.00",
            txn_date="2026-11-19",
            txn_time=None,
            reference="632390817263",
            payer_bank="SBI",
            payer_account_last4="0042",
        ),
    ),
    dict(
        id="img-04",
        kind="app_receipt",
        app="UPI app",
        status="Payment failed",
        amount="₹1,850",
        to="Anita Sharma",
        to_vpa="anita.s@okhdfcbank",
        when="16 Nov 2026, 08:31 PM",
        ref_label="UPI transaction ID",
        ref="632008811220",
        from_bank="Bank of Baroda - 4471",
        note="Money debited from your account will be refunded if not credited.",
        expected=dict(
            case_type="failed_transaction",
            scenario="BENEFICIARY_NOT_CREDITED",
            channel="UPI",
            amount_inr="1850.00",
            txn_date="2026-11-16",
            txn_time="20:31",
            reference="632008811220",
            payer_bank="Baroda",
            payer_account_last4="4471",
        ),
    ),
    dict(
        id="img-05",
        kind="app_receipt",
        app="UPI app",
        status="Payment pending",
        amount="₹560",
        to="Chai Point",
        to_vpa="chaipoint@ybl",
        when="18 Nov 2026, 09:12 AM",
        ref_label="UTR",
        ref="632209123456",
        from_bank="Kotak Mahindra Bank - 0921",
        note="Merchant did not confirm the payment. Amount debited.",
        expected=dict(
            case_type="failed_transaction",
            scenario="MERCHANT_NOT_CONFIRMED",
            channel="UPI",
            amount_inr="560.00",
            txn_date="2026-11-18",
            txn_time="09:12",
            reference="632209123456",
            payer_bank="Kotak",
            payer_account_last4="0921",
        ),
    ),
    dict(
        id="img-06",
        kind="app_receipt",
        app="UPI app",
        status="Payment failed",
        amount="₹9,000",
        to="Ravi Electricals",
        to_vpa="ravielec@okaxis",
        when="12 Nov 2026, 04:47 PM",
        ref_label="UPI Ref No",
        ref="631616470001",
        from_bank="Union Bank of India - 3380",
        note="Debited from your account. Not credited to Ravi Electricals.",
        expected=dict(
            case_type="failed_transaction",
            scenario="BENEFICIARY_NOT_CREDITED",
            channel="UPI",
            amount_inr="9000.00",
            txn_date="2026-11-12",
            txn_time="16:47",
            reference="631616470001",
            payer_bank="Union",
            payer_account_last4="3380",
        ),
    ),
    dict(
        id="img-07",
        kind="sms_screen",
        sender="AD-CANBNK",
        text="Your a/c XXX6060 is debited for Rs.4,750.00 on 14-11-2026 07:58 via IMPS. IMPS Ref No 631807585858. Txn failed at beneficiary bank. -Canara Bank",
        expected=dict(
            case_type="failed_transaction",
            scenario="BENEFICIARY_NOT_CREDITED",
            channel="IMPS",
            amount_inr="4750.00",
            txn_date="2026-11-14",
            txn_time="07:58",
            reference="631807585858",
            payer_bank="Canara",
            payer_account_last4="6060",
        ),
    ),
    dict(
        id="img-08",
        kind="app_receipt",
        app="UPI app",
        status="Payment failed",
        amount="₹15,000",
        to="Unknown",
        to_vpa="claimrefund22@paytm",
        when="19 Nov 2026, 11:52 PM",
        ref_label="UPI transaction ID",
        ref="632399990012",
        from_bank="IndusInd Bank - 8123",
        note="Collect request approved. I did not intend to pay this person.",
        expected=dict(
            case_type="fraud",
            scenario=None,
            channel="UPI",
            amount_inr="15000.00",
            txn_date="2026-11-19",
            txn_time="23:52",
            reference="632399990012",
            payer_bank="IndusInd",
            payer_account_last4="8123",
        ),
    ),
]


def render_sms(case: dict, path: Path) -> None:
    W, H = 720, 1100
    im = Image.new("RGB", (W, H), (245, 246, 248))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 120], fill=(255, 255, 255))
    d.text((40, 42), case["sender"], font=font(34, bold=True), fill=(20, 20, 24))
    d.text((40, 160), "Today", font=font(24), fill=(120, 124, 132))
    lines = textwrap.wrap(case["text"], width=34)
    f = font(30)
    box_h = 40 + len(lines) * 44
    d.rounded_rectangle([30, 210, W - 90, 210 + box_h], radius=28, fill=(228, 230, 235))
    for i, line in enumerate(lines):
        d.text((56, 230 + i * 44), line, font=f, fill=(25, 25, 30))
    im.save(path)


def render_receipt(case: dict, path: Path) -> None:
    W, H = 720, 1280
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    failed = "failed" in case["status"].lower()
    d.rectangle([0, 0, W, 360], fill=(214, 64, 64) if failed else (232, 160, 52))
    d.text((W // 2, 90), case["app"], font=font(28), fill=(255, 255, 255), anchor="mm")
    d.text(
        (W // 2, 190), case["amount"], font=font(72, bold=True), fill=(255, 255, 255), anchor="mm"
    )
    d.text(
        (W // 2, 280), case["status"], font=font(34, bold=True), fill=(255, 255, 255), anchor="mm"
    )
    rows = [
        ("To", case["to"]),
        ("", case["to_vpa"]),
        ("From", case["from_bank"]),
        ("Date", case["when"]),
        (case["ref_label"], case["ref"]),
    ]
    y = 420
    for k, v in rows:
        if k:
            d.text((50, y), k, font=font(26), fill=(120, 124, 132))
            y += 40
        d.text((50, y), v, font=font(32), fill=(20, 20, 24))
        y += 70
    for i, line in enumerate(textwrap.wrap(case["note"], width=40)):
        d.text((50, y + 20 + i * 40), line, font=font(26), fill=(90, 94, 102))
    im.save(path)


def main() -> None:
    SCREENS.mkdir(parents=True, exist_ok=True)
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    with DATASET.open("w") as out:
        for c in CASES:
            row = {"id": c["id"], "kind": c["kind"], "expected": c["expected"]}
            if c["kind"] in ("sms", "description"):
                row["text"] = c["text"]
            else:
                path = SCREENS / f"{c['id']}.png"
                (render_sms if c["kind"] == "sms_screen" else render_receipt)(c, path)
                row["image"] = str(path.relative_to(ROOT))
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{len(CASES)} cases -> {DATASET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
