"""Step 2 of the pipeline: one common, English table for every bank.

Each bank exports different columns, in French. normalize_bnp and
normalize_belfius map them to the same COLUMNS, so classify, the summaries
and later the database only ever see one format. Adding a bank means
writing one more loader and one more normalize_<bank> function.

Rows that are not final yet (refused, or pending without a number) are
dropped: they come back with a number in a later export.
"""

from __future__ import annotations

import pandas as pd

from family_budget.belfius import extract_card_holder
from family_budget.bnp import extract_merchant

# The output of normalize_bnp / normalize_belfius: one row per transaction,
# exactly these columns, in this order.
COLUMNS = [
    "row_id",  # str: account + bank reference, unique; used to drop duplicates
    "date",  # datetime: day the bank executed it
    "amount",  # float: negative = money out, positive = money in
    "account",  # str: your IBAN, no spaces
    "bank",  # str: "bnp" or "belfius"
    "transaction_type",  # str: card, transfer, direct_debit, loan, fee, ...
    "counterparty",  # str | NaN: who you paid / who paid you (shop for cards)
    "counterparty_iban",  # str | NaN: their IBAN, no spaces (none for cards)
    "owner",  # str | NaN: who made it (BNP: Marco; Belfius: cardholder)
    "text",  # str: communication + details, upper case; keywords search here
]

# First match wins, so specific words come before general ones
# ("carte de crédit" before "carte", "retrait ... par carte" before "carte").
TRANSACTION_TYPES = [
    ("CARTE DE CRÉDIT", "credit_card"),
    ("HYPOTH", "loan"),
    ("DOMICILIATION", "direct_debit"),
    ("RETRAIT", "cash"),
    ("FRAIS", "fee"),
    ("SUPPLEMENT", "fee"),
    ("CARTE", "card"),
    ("BANCONTACT", "card"),
    ("MASTERCARD", "card"),
    ("VIREMENT", "transfer"),
    ("VERSEMENT", "transfer"),
    ("WERO", "transfer"),
    ("PAIEMENT VIA", "transfer"),
]


def to_transaction_type(text) -> str:
    """'Paiement par carte' -> 'card', 'VOTRE DOMICILIATION ...' -> 'direct_debit'."""
    upper = str(text).upper()
    for keyword, english in TRANSACTION_TYPES:
        if keyword in upper:
            return english
    return "other"


def _no_spaces(series: pd.Series) -> pd.Series:
    return series.str.replace(" ", "", regex=False)


def _text(*parts: pd.Series) -> pd.Series:
    joined = parts[0].fillna("")
    for part in parts[1:]:
        joined = joined + " " + part.fillna("")
    return joined.str.split().str.join(" ").str.upper()


def normalize_bnp(df: pd.DataFrame, owner: str = "Marco") -> pd.DataFrame:
    booked = df["Nº de séquence"].str.fullmatch(r"\d{4}-\d+", na=False)
    df = df[(df["Statut"] == "Accepté") & booked]  # "2024-" = not booked yet
    account = _no_spaces(df["Numéro de compte"])
    out = pd.DataFrame(
        {
            "row_id": account + "-" + df["Nº de séquence"],
            "date": df["Date d'exécution"],
            "amount": df["Montant"],
            "account": account,
            "bank": "bnp",
            "transaction_type": df["Type de transaction"].map(to_transaction_type),
            "counterparty": df["Nom de la contrepartie"].fillna(
                df["Détails"].map(extract_merchant)
            ),
            "counterparty_iban": _no_spaces(df["Contrepartie"]),
            "owner": owner,
            "text": _text(df["Communication"], df["Détails"]),
        }
    )
    return out[COLUMNS].reset_index(drop=True)


def normalize_belfius(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df["Numéro d'extrait"].notna()]  # pending rows come back later, numbered
    account = _no_spaces(df["Compte"])
    statement = df["Numéro d'extrait"] + "-" + df["Numéro de transaction"]
    out = pd.DataFrame(
        {
            "row_id": account + "-" + statement,
            "date": df["Date de comptabilisation"],
            "amount": df["Montant"],
            "account": account,
            "bank": "belfius",
            "transaction_type": df["Transaction"].map(to_transaction_type),
            "counterparty": df["Nom contrepartie contient"],
            "counterparty_iban": _no_spaces(df["Compte contrepartie"]),
            "owner": df["Transaction"].map(extract_card_holder),
            "text": _text(df["Transaction"], df["Communications"]),
        }
    )
    return out[COLUMNS].reset_index(drop=True)
