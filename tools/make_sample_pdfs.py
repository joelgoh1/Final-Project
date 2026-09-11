"""Generate synthetic unlocked PDF e-statements for the upload demo.

    python tools/make_sample_pdfs.py

The layouts mimic DBS, OCBC and UOB e-statements closely enough to exercise the
parser, including header lines with a name, address and card number so the
redaction step is visible. All data is invented - no real cardholder or account.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fpdf import FPDF

OUT_DIR = Path(__file__).resolve().parent.parent / "samples"

DBS = {
    "filename": "dbs_live_fresh_statement.pdf",
    "header": [
        "DBS Bank Ltd",
        "DBS Live Fresh Card Statement of Account",
        "MR TAN AH KOW",
        "BLK 118 ANG MO KIO AVENUE 4 #11-234",
        "SINGAPORE 560118",
        "CARD NO 4123 4567 8901 2345",
        "Statement Date: 31 Aug 2026     Payment Due: 18 Sep 2026",
        "",
        "TRANSACTION DATE   DESCRIPTION                            AMOUNT (SGD)",
    ],
    "rows": [
        ("02 Aug", "SHOPEE SG PTE LTD SINGAPORE", "128.90"),
        ("03 Aug", "GRABFOOD SINGAPORE", "38.75"),
        ("05 Aug", "NTUC FAIRPRICE FINEST JELITA", "184.35"),
        ("07 Aug", "UNIQLO SOMERSET SINGAPORE", "119.90"),
        ("09 Aug", "DIN TAI FUNG PARAGON", "96.40"),
        ("11 Aug", "SP SERVICES LTD GIRO", "212.60"),
        ("14 Aug", "LAZADA SINGAPORE", "76.40"),
        ("16 Aug", "ORCHID VALLEY FLORIST", "64.00"),
        ("19 Aug", "DON DON DONKI CITY SQUARE", "57.30"),
        ("22 Aug", "HAIDILAO HOTPOT CLARKE QUAY", "142.50"),
        ("24 Aug", "WATSONS ORCHARD GATEWAY", "34.25"),
        ("27 Aug", "DECATHLON SINGAPORE LAB", "87.40"),
        ("29 Aug", "SIMPLYGO BUS/MRT TOP UP", "42.00"),
        ("30 Aug", "WELLNESS STUDIO 88 PTE LTD", "158.00"),
    ],
    "footer": [
        "",
        "PREVIOUS BALANCE                                              0.00",
        "TOTAL NEW BALANCE                                         1,342.75",
        "MINIMUM PAYMENT DUE                                          50.00",
    ],
}

OCBC = {
    "filename": "ocbc_365_statement.pdf",
    "header": [
        "OCBC Bank (Oversea-Chinese Banking Corporation Limited)",
        "OCBC 365 Credit Card Statement",
        "MDM LIM HUI MIN",
        "BLK 27 TELOK BLANGAH RISE #08-91",
        "SINGAPORE 090027",
        "Card Number: 5412-7788-9900-4417",
        "Statement Period: 01 Aug 2026 to 31 Aug 2026",
        "",
        "TRANS DATE  POST DATE   DESCRIPTION                        AMOUNT",
    ],
    "rows": [
        ("01/08/2026", "02/08/2026", "KOUFU CLEMENTI MALL", "11.60"),
        ("03/08/2026", "04/08/2026", "SUSHI TEI HOLLAND VILLAGE", "78.90"),
        ("05/08/2026", "06/08/2026", "COLD STORAGE JELITA", "92.15"),
        ("08/08/2026", "09/08/2026", "STARBUCKS RAFFLES CITY", "14.80"),
        ("12/08/2026", "13/08/2026", "GIANT HYPERMARKET TAMPINES", "81.25"),
        ("15/08/2026", "16/08/2026", "CRYSTAL JADE LA MIAN", "64.30"),
        ("18/08/2026", "19/08/2026", "ZIG BY CDG RIDE", "26.80"),
        ("21/08/2026", "22/08/2026", "SINGTEL MOBILE BILL", "68.90"),
        ("24/08/2026", "25/08/2026", "TIONG BAHRU BAKERY", "18.60"),
        ("26/08/2026", "27/08/2026", "SWEE CHOON TIM SUM", "52.40"),
        ("28/08/2026", "29/08/2026", "PAWFECT GROOMING BAR", "88.00"),
        ("30/08/2026", "31/08/2026", "FOODPANDA SG", "44.15"),
    ],
    "footer": [
        "",
        "PAYMENT RECEIVED - THANK YOU                               120.00 CR",
        "TOTAL AMOUNT DUE                                           641.85",
    ],
}

UOB = {
    "filename": "uob_one_statement.pdf",
    "header": [
        "United Overseas Bank Limited",
        "UOB One Card - Monthly Statement",
        "MR RAJU SUBRAMANIAM",
        "BLK 402 HOUGANG AVENUE 10 #03-77",
        "SINGAPORE 530402",
        "Card No: 4728 1122 3344 5566",
        "Statement Date 31 AUG 2026",
        "",
        "DATE       POST DATE   TRANSACTION DESCRIPTION                 SGD",
    ],
    "rows": [
        ("02 AUG 2026", "03 AUG 2026", "SHENG SIONG SUPERMARKET SG", "63.40"),
        ("04 AUG 2026", "05 AUG 2026", "SIMPLYGO BUS/MRT", "38.50"),
        ("06 AUG 2026", "07 AUG 2026", "ESSO BUKIT TIMAH", "88.20"),
        ("09 AUG 2026", "10 AUG 2026", "GENECO ELECTRICITY", "96.45"),
        ("12 AUG 2026", "13 AUG 2026", "NTUC FAIRPRICE XTRA AMK HUB", "148.70"),
        ("15 AUG 2026", "16 AUG 2026", "GRAB TRANSPORT SINGAPORE", "31.50"),
        ("18 AUG 2026", "19 AUG 2026", "STARHUB BROADBAND", "49.90"),
        ("21 AUG 2026", "22 AUG 2026", "SEASON PARKING TOWN COUNCIL", "110.00"),
        ("24 AUG 2026", "25 AUG 2026", "PASTAMANIA JURONG POINT", "33.60"),
        ("27 AUG 2026", "28 AUG 2026", "COLD STORAGE GREAT WORLD", "74.80"),
    ],
    "footer": [
        "",
        "SUB-TOTAL                                                  735.05",
        "TOTAL AMOUNT DUE                                           735.05",
    ],
}


def _write(statement: dict) -> tuple[Path, int]:
    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.set_font("Courier", size=9)
    lines = list(statement["header"])
    for row in statement["rows"]:
        if len(row) == 3:
            date, merchant, amount = row
            lines.append(f"{date:<18} {merchant:<40} {amount:>12}")
        else:
            date, post, merchant, amount = row
            lines.append(f"{date:<12}{post:<12}{merchant:<36}{amount:>10}")
    lines.extend(statement["footer"])

    for line in lines:
        pdf.cell(0, 5, text=line, new_x="LMARGIN", new_y="NEXT")

    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / statement["filename"]
    pdf.output(str(path))
    return path, len(statement["rows"])


def main() -> int:
    for statement in (DBS, OCBC, UOB):
        path, count = _write(statement)
        print(f"wrote {path.relative_to(OUT_DIR.parent)} ({count} transactions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
