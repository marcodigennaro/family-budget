"""A fake data folder, the same shape as your real one:  python -m family_budget.fake

    data/
      BNP/BE00000000000001/CSV_2024.csv ...   current account, one export a year
      BNP/BE00000000000009/CSV_2024.csv ...   savings account
      BELFIUS/BE00000000000002/Belfius_2024.csv ...
                                       one folder per account, the banks' real formats
      known_expenses.csv, exceptional.csv, trips.csv, categories.ttl
      processed/                       python -m family_budget writes here
      FAKE_DATA.txt                    marks the folder as generated

Two people, two banks: Marco on BNP (salary, rent, mortgage, bills, cards, and
a savings account that gets 500 a month) and "A. EXAMPLE" on Belfius (salary,
childcare, cards). One trip to Italy and one to Portugal a year, a kitchen
renovation, a flat you rent and one you let. Exports overlap by a month, as
real ones do, and a few payments are refused. About 5% of the rows match no
rule, so there is something left to classify.

Every name, account and amount is invented. Accounts are all BE00...; the same
seed always gives the same files, so tests and screenshots are repeatable.
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from family_budget.belfius import EXPECTED_COLUMNS as BELFIUS_COLUMNS
from family_budget.bnp import EXPECTED_COLUMNS as BNP_COLUMNS

MARKER = "FAKE_DATA.txt"

MARCO = "BE00000000000001"  # BNP
PARTNER = "BE00 0000 0000 0002"  # Belfius writes accounts with spaces
SAVINGS = "BE00000000000009"
EMPLOYER = "BE00000000000010"
LANDLORD = "BE00000000000011"
TENANT = "BE00000000000012"
KITCHEN = "BE00000000000013"
OTHER_EMPLOYER = "BE00 0000 0000 0014"


# --- rules and the category tree (what the transactions below are) ---------------

RULES = f"""\
transaction_type,counterparty_iban,keyword,amount,category,type,is_fixed,direction,property
# FAKE rules for the fake exports made by python -m family_budget.fake
# income
,,MY COMPANY SRL,,salary,income,false,in,
,,OTHER EMPLOYER NV,,salary,income,false,in,
# housing: one word, two properties; direction says which way the money goes
,,LOYER,,rent,spending,true,out,Example flat
,,LOYER,,rent,income,true,in,Example studio
loan,,,,mortgage,spending,true,,Example studio
,,BRICO,,house works,spending,false,,
# bills
direct_debit,,ENERGY EXAMPLE,,utilities,spending,true,,
direct_debit,,TELECOM EXAMPLE,,telecom,spending,true,,
direct_debit,,PARTENA,,health insurance,spending,true,,
direct_debit,,CRECHE EXAMPLE,,childcare,spending,true,,
fee,,,,bank fees,spending,true,,
# everyday spending
,,DELHAIZE,,groceries,spending,false,,
,,CARREFOUR,,groceries,spending,false,,
,,COLRUYT,,groceries,spending,false,,
,,LIDL,,groceries,spending,false,,
,,CONAD,,groceries,spending,false,,
,,PINGO DOCE,,groceries,spending,false,,
,,PIZZERIA,,eating out,spending,false,,
,,BRASSERIE,,eating out,spending,false,,
,,RISTORANTE,,eating out,spending,false,,
,,PASTELARIA,,eating out,spending,false,,
,,PHARMACIE,,health,spending,false,,
,,STIB,,public transport,spending,false,,
,,SNCB,,public transport,spending,false,,
,,RYANAIR,,flights,spending,false,,
cash,,,,cash,spending,false,,
,,INTERETS,,interest,income,false,in,
# money moving between your own accounts is not spending (seen from both sides)
,{SAVINGS},,,own accounts,transfer,false,,
,{MARCO},,,own accounts,transfer,false,,
"""

EXCEPTIONAL = """\
date,transaction_type,counterparty_iban,keyword,amount,category,type,is_fixed,direction,property
# FAKE one-off project: these win over the general rules (BRICO -> house works)
,,,CUISINES EXAMPLE,,kitchen renovation,exceptional,false,,Example studio
{brico_day},,,BRICO,{brico_amount},kitchen renovation,exceptional,false,,Example studio
"""

TREE = {  # group -> categories; a group can also be a category itself (health)
    "income": ["salary", "interest"],
    "housing": ["rent", "mortgage", "house works", "kitchen renovation"],
    "bills": ["utilities", "telecom", "bank fees"],
    "health": ["health insurance"],
    "food": ["groceries", "eating out"],
    "transport": ["public transport", "flights"],
    "family": ["childcare"],
    "money": ["cash", "own accounts"],
}


def categories_ttl() -> str:
    def node(label: str) -> str:
        return "fb:" + label.replace(" ", "_")

    lines = [
        "# FAKE category tree (SKOS) for the fake data. Same shape as yours:",
        "# each category's English prefLabel equals the text in the rules.",
        "",
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "@prefix fb:   <https://budget.example/category/> .",
        "",
        'fb:scheme a skos:ConceptScheme ; skos:prefLabel "fake categories"@en .',
        "",
    ]
    for group, children in TREE.items():
        lines.append(
            f'{node(group)} a skos:Concept ; skos:prefLabel "{group}"@en ; '
            "skos:topConceptOf fb:scheme ."
        )
        for child in children:
            lines.append(
                f'{node(child)} a skos:Concept ; skos:prefLabel "{child}"@en ; '
                f"skos:broader {node(group)} ."
            )
        lines.append("")
    return "\n".join(lines)


# --- the transactions -------------------------------------------------------------


@dataclass
class Payment:
    day: date
    amount: float
    kind: str  # card, transfer, direct_debit, loan, cash, fee
    name: str  # shop or counterparty
    iban: str = ""
    text: str = ""  # communication
    country: str = ""  # for card payments abroad: ITALIE / IT ...
    refused: bool = False


SHOPS = {  # name -> (min, max) amount
    "DELHAIZE BRUXELLES": (15, 120),
    "CARREFOUR EXAMPLE": (10, 90),
    "COLRUYT EXAMPLE": (20, 140),
    "LIDL EXAMPLE": (8, 60),
}
EATING_OUT = {"PIZZERIA ESEMPIO": (18, 60), "BRASSERIE EXAMPLE": (25, 90)}
UNKNOWN = ["MARCHE DU MIDI", "LIBRAIRIE EXAMPLE", "SHOP 42", "PAYPAL *EXAMPLE"]
TRIP_SHOPS = {
    "Italy": ("ITALIE", "IT", ["CONAD ROMA", "RISTORANTE ESEMPIO", "PIZZERIA NAPOLI"]),
    "Portugal": ("PORTUGAL", "PT", ["PINGO DOCE LISBOA", "PASTELARIA EXEMPLO"]),
}


def _months(start: str, count: int) -> list[date]:
    year, month = map(int, start.split("-"))
    firsts = []
    for _ in range(count):
        firsts.append(date(year, month, 1))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return firsts


def _day(first: date, rng: random.Random, low: int = 1, high: int = 28) -> date:
    return first.replace(day=rng.randint(low, high))


def _money(rng: random.Random, low: float, high: float) -> float:
    return round(rng.uniform(low, high), 2)


def _trips(firsts: list[date]) -> list[tuple[str, date, date, str]]:
    trips = []
    for year in sorted({d.year for d in firsts}):
        for country, start, days in [
            ("Portugal", date(year, 4, 12), 6),
            ("Italy", date(year, 7, 10), 12),
        ]:
            if firsts[0] <= start and start + timedelta(days) < _end(firsts):
                trips.append(
                    (f"{country} {year}", start, start + timedelta(days), country)
                )
    return trips


def _end(firsts: list[date]) -> date:
    last = firsts[-1]
    following = _months(f"{last:%Y-%m}", 2)[1]
    return following - timedelta(days=1)


def _marco(first: date, rng: random.Random) -> list[Payment]:
    month = f"{first:%m/%Y}"
    out = [
        Payment(
            first.replace(day=25),
            _money(rng, 3150, 3350),
            "transfer",
            "MY COMPANY SRL",
            EMPLOYER,
            f"SALAIRE {month}",
        ),
        Payment(
            first.replace(day=1),
            -950.00,
            "transfer",
            "LANDLORD EXAMPLE",
            LANDLORD,
            f"LOYER {month} EXAMPLE FLAT",
        ),
        Payment(
            first.replace(day=3),
            720.00,
            "transfer",
            "TENANT EXAMPLE",
            TENANT,
            f"LOYER STUDIO {month}",
        ),
        Payment(
            first.replace(day=2),
            -640.15,
            "loan",
            "",
            "",
            "REMBOURSEMENT CREDIT HYPOTHECAIRE 000/0000",
        ),
        Payment(
            first.replace(day=5),
            -36.40,
            "direct_debit",
            "PARTENA EXAMPLE",
            "BE00000000000020",
            "COTISATION MUTUELLE",
        ),
        Payment(
            first.replace(day=8),
            -_money(rng, 80, 160),
            "direct_debit",
            "ENERGY EXAMPLE",
            "BE00000000000021",
            "ACOMPTE GAZ ELEC",
        ),
        Payment(
            first.replace(day=12),
            -45.00,
            "direct_debit",
            "TELECOM EXAMPLE",
            "BE00000000000022",
            "FACTURE INTERNET MOBILE",
        ),
        Payment(
            first.replace(day=15),
            -500.00,
            "transfer",
            "OWN SAVINGS",
            SAVINGS,
            "EPARGNE",
        ),
        Payment(first.replace(day=28), -2.50, "fee", "", "", "FRAIS DE GESTION"),
        Payment(
            _day(first, rng), -float(rng.choice([50, 100])), "cash", "", "", "BRUXELLES"
        ),
    ]
    for _ in range(rng.randint(6, 9)):
        name, (low, high) = rng.choice(list(SHOPS.items()))
        out.append(Payment(_day(first, rng), -_money(rng, low, high), "card", name))
    for _ in range(rng.randint(2, 4)):
        name, (low, high) = rng.choice(list(EATING_OUT.items()))
        out.append(Payment(_day(first, rng), -_money(rng, low, high), "card", name))
    for _ in range(2):
        name = rng.choice(["STIB MIVB", "SNCB NMBS"])
        out.append(Payment(_day(first, rng), -_money(rng, 2.1, 24), "card", name))
    if rng.random() < 0.3:
        out.append(
            Payment(_day(first, rng), -_money(rng, 12, 80), "card", "BRICO EXAMPLE")
        )
    for _ in range(rng.randint(0, 2)):
        out.append(
            Payment(_day(first, rng), -_money(rng, 5, 70), "card", rng.choice(UNKNOWN))
        )
    if first.month in (1, 7):
        out.append(
            Payment(
                _day(first, rng),
                -_money(rng, 30, 90),
                "card",
                "SHOP EXAMPLE",
                refused=True,
            )
        )
    if first.month == 3:
        out.append(Payment(_day(first, rng), -_money(rng, 120, 260), "card", "RYANAIR"))
    return out


def _partner(first: date, rng: random.Random) -> list[Payment]:
    out = [
        Payment(
            first.replace(day=26),
            _money(rng, 2350, 2500),
            "transfer",
            "OTHER EMPLOYER NV",
            OTHER_EMPLOYER,
            "SALAIRE",
        ),
        Payment(
            first.replace(day=4),
            -210.00,
            "direct_debit",
            "CRECHE EXAMPLE",
            "BE00 0000 0000 0023",
            "PARTICIPATION PARENTALE",
        ),
    ]
    for _ in range(rng.randint(4, 6)):
        name, (low, high) = rng.choice(list(SHOPS.items()))
        out.append(Payment(_day(first, rng), -_money(rng, low, high), "card", name))
    if rng.random() < 0.6:
        out.append(
            Payment(_day(first, rng), -_money(rng, 6, 40), "card", "PHARMACIE EXAMPLE")
        )
    if rng.random() < 0.4:
        out.append(
            Payment(_day(first, rng), -_money(rng, 5, 50), "card", rng.choice(UNKNOWN))
        )
    return out


def _abroad(trips, rng: random.Random) -> tuple[list[Payment], list[Payment]]:
    marco, partner = [], []
    for _, start, end, country in trips:
        word, code, shops = TRIP_SHOPS[country]
        days = (end - start).days
        for _ in range(days):
            day = start + timedelta(rng.randint(0, days))
            who = marco if rng.random() < 0.6 else partner
            where = word if who is marco else code
            who.append(
                Payment(
                    day, -_money(rng, 8, 85), "card", rng.choice(shops), country=where
                )
            )
    return marco, partner


def _kitchen(
    firsts: list[date], rng: random.Random
) -> tuple[list[Payment], date, float]:
    middle = firsts[len(firsts) // 2]
    payments = [
        Payment(
            middle.replace(day=10),
            -4000.00,
            "transfer",
            "CUISINES EXAMPLE SA",
            KITCHEN,
            "ACOMPTE CUISINE",
        ),
        Payment(
            _months(f"{middle:%Y-%m}", 2)[1].replace(day=10),
            -6250.00,
            "transfer",
            "CUISINES EXAMPLE SA",
            KITCHEN,
            "SOLDE CUISINE",
        ),
    ]
    brico = Payment(
        middle.replace(day=20), -_money(rng, 600, 900), "card", "BRICO EXAMPLE"
    )
    return [*payments, brico], brico.day, brico.amount


# --- the two export formats -------------------------------------------------------


def _be_amount(value: float, thousands: bool = False) -> str:
    text = f"{value:,.2f}" if thousands else f"{value:.2f}"
    return text.replace(",", " ").replace(".", ",").replace(" ", ".")


BNP_TYPES = {
    "card": "Paiement par carte",
    "transfer": "Virement en euros",
    "direct_debit": "Domiciliation",
    "loan": "Remboursement crédit hypothécaire",
    "cash": "Retrait d'espèces",
    "fee": "Frais",
    "interest": "Intérêts",
}


def _savings(firsts: list[date]) -> list[Payment]:
    """The savings account: the monthly 500 from the current account, and interest."""
    out = [
        Payment(
            first.replace(day=15), 500.00, "transfer", "MARCO EXAMPLE", MARCO, "EPARGNE"
        )
        for first in firsts
    ]
    out += [
        Payment(
            first.replace(day=31),
            round(5 + 0.004 * 500 * i, 2),
            "interest",
            "",
            "",
            f"INTERETS {first.year}",
        )
        for i, first in enumerate(firsts, start=1)
        if first.month == 12
    ]
    return out


def _bnp_rows(payments: list[Payment], account: str) -> list[list[str]]:
    rows, counters = [], {}
    for p in sorted(payments, key=lambda p: (p.day, p.name, p.amount)):
        counters[p.day.year] = counters.get(p.day.year, 0) + 1
        sequence = f"{p.day.year}-{counters[p.day.year]:05d}"
        day = f"{p.day:%d/%m/%Y}"
        details = ""
        if p.kind == "card":
            place = f" {p.country}" if p.country else ""
            details = (
                f"PAIEMENT AVEC LA CARTE DE DEBIT NUMERO 0000 00XX XXXX 0000 "
                f"{p.name}{place} {day}"
            )
        elif p.kind == "cash":
            details = f"RETRAIT D'ESPECES CARTE 0000 00XX XXXX 0000 {p.text} {day}"
        card_or_cash = p.kind in ("card", "cash")
        rows.append(
            [
                sequence,
                day,
                day,
                _be_amount(p.amount),
                "EUR",
                account,
                BNP_TYPES[p.kind],
                "" if card_or_cash else p.iban,
                "" if card_or_cash else p.name,
                "" if card_or_cash else p.text,
                details,
                "Refusé" if p.refused else "Accepté",
                "Solde insuffisant" if p.refused else "",
            ]
        )
    return rows


def _belfius_rows(payments: list[Payment]) -> list[list[str]]:
    rows, numbers = [], {}
    for p in sorted(payments, key=lambda p: (p.day, p.name, p.amount)):
        statement = p.day.year * 100 + p.day.month  # one statement a month
        numbers[statement] = numbers.get(statement, 0) + 1
        day = f"{p.day:%d/%m/%Y}"
        if p.kind == "card":
            code = p.country or "BE"
            transaction = (
                f"ACHAT BANCONTACT AVEC CARTE N° 0000 00XX XXXX 0002 - "
                f"A. EXAMPLE REF. : {numbers[statement]:06d} {p.name} {code}"
            )
            counterparty, iban, text = p.name, "", ""
        elif p.kind == "direct_debit":
            transaction = f"DOMICILIATION EUROPEENNE {p.name}"
            counterparty, iban, text = p.name, p.iban, p.text
        else:
            transaction = f"VIREMENT EUROPEEN DE {p.name}"
            counterparty, iban, text = p.name, p.iban, p.text
        rows.append(
            [
                PARTNER,
                day,
                str(statement),
                f"{numbers[statement]:03d}",
                iban,
                counterparty,
                "",
                "",
                transaction,
                day,
                _be_amount(p.amount, thousands=True),
                "EUR",
                "",
                "BE",
                text,
            ]
        )
    return rows


def _bnp_file(rows: list[list[str]]) -> bytes:
    lines = [";".join(BNP_COLUMNS)] + [";".join(r) for r in reversed(rows)]
    return ("\n".join(lines) + "\n").encode("utf-8-sig")


def _belfius_file(rows: list[list[str]], balance: float, at: date) -> bytes:
    preamble = [
        "Date de comptabilisation à partir de;",
        "Date de comptabilisation jusqu'au;",
        "Montant à partir de;",
        "Montant jusqu'à;",
        "Numéro d'extrait à partir de;",
        "Numéro d'extrait jusqu'au;",
        "Communication;",
        "Nom contrepartie contient;",
        "Compte contrepartie;",
        f"Dernier solde;{_be_amount(balance, thousands=True)} EUR",
        f"Date/heure du dernier solde;{at:%d/%m/%Y} 10:00:00",
        ";",
    ]
    lines = (
        preamble + [";".join(BELFIUS_COLUMNS)] + [";".join(r) for r in reversed(rows)]
    )
    return ("\n".join(lines) + "\n").encode("cp1252")


def _by_export(rows: list[list[str]], date_column: int) -> dict[int, list[list[str]]]:
    """One export per year, each running one month into the next (an overlap)."""

    def when(row):
        d, m, y = map(int, row[date_column].split("/"))
        return date(y, m, d)

    years = sorted({when(r).year for r in rows})
    return {
        year: [r for r in rows if date(year, 1, 1) <= when(r) <= date(year + 1, 1, 31)]
        for year in years
    }


# --- the folder ---------------------------------------------------------------------


def generate(
    folder: Path, start: str = "2023-01", months: int = 36, seed: int = 42
) -> Path:
    """Write a fake data folder. Refuses a folder with files not made here."""
    folder = Path(folder)
    if folder.exists() and any(folder.iterdir()) and not (folder / MARKER).exists():
        raise FileExistsError(
            f"{folder} has files that are not fake data: not touching it"
        )
    if folder.exists():
        shutil.rmtree(folder)
    rng = random.Random(seed)
    firsts = _months(start, months)
    trips = _trips(firsts)

    marco = [p for first in firsts for p in _marco(first, rng)]
    partner = [p for first in firsts for p in _partner(first, rng)]
    abroad_marco, abroad_partner = _abroad(trips, rng)
    kitchen, brico_day, brico_amount = _kitchen(firsts, rng)
    marco += abroad_marco + kitchen
    partner += abroad_partner

    bnp = {MARCO: marco, SAVINGS: _savings(firsts)}
    for account, payments in bnp.items():
        (folder / "BNP" / account).mkdir(parents=True)
        for year, rows in _by_export(_bnp_rows(payments, account), 1).items():
            (folder / "BNP" / account / f"CSV_{year}.csv").write_bytes(_bnp_file(rows))
    belfius = folder / "BELFIUS" / PARTNER.replace(" ", "")
    belfius.mkdir(parents=True)
    (folder / "processed").mkdir()
    for year, rows in _by_export(_belfius_rows(partner), date_column=1).items():
        balance = _money(rng, 900, 4000)
        (belfius / f"Belfius_{year}.csv").write_bytes(
            _belfius_file(rows, balance, min(date(year + 1, 1, 31), _end(firsts)))
        )

    (folder / "known_expenses.csv").write_text(RULES, encoding="utf-8")
    (folder / "exceptional.csv").write_text(
        EXCEPTIONAL.format(brico_day=brico_day.isoformat(), brico_amount=brico_amount),
        encoding="utf-8",
    )
    trips_csv = ["name,start,end,country"] + [
        f"{name},{s.isoformat()},{e.isoformat()},{country}"
        for name, s, e, country in trips
    ]
    (folder / "trips.csv").write_text("\n".join(trips_csv) + "\n", encoding="utf-8")
    (folder / "categories.ttl").write_text(categories_ttl(), encoding="utf-8")
    (folder / MARKER).write_text(
        f"Fake data made by python -m family_budget.fake (seed {seed}, from {start}, "
        f"{months} months).\nEverything here is invented and is overwritten when "
        "you run it again.\n",
        encoding="utf-8",
    )
    return folder


def main(argv: list[str] | None = None) -> int:
    from family_budget.config import BASE_DIR

    parser = argparse.ArgumentParser(
        prog="python -m family_budget.fake",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("folder", nargs="?", default=str(BASE_DIR / "data"))
    parser.add_argument("--start", default="2023-01", help="first month, YYYY-MM")
    parser.add_argument("--months", type=int, default=36)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    try:
        folder = generate(Path(args.folder), args.start, args.months, args.seed)
    except FileExistsError as error:
        print(error, file=sys.stderr)
        return 1
    files = sorted(p.relative_to(folder) for p in folder.rglob("*") if p.is_file())
    print(f"Fake data in {folder}:")
    for path in files:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
