"""Load a Belfius account-history CSV export into a pandas DataFrame."""

from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd

ENCODING = "cp1252"  # Belfius exports are Windows-1252, not UTF-8
SEP = ";"
HEADER_START = "Compte;Date de comptabilisation"

EXPECTED_COLUMNS = [
    "Compte",
    "Date de comptabilisation",
    "Numéro d'extrait",
    "Numéro de transaction",
    "Compte contrepartie",
    "Nom contrepartie contient",
    "Rue et numéro",
    "Code postal et ville",
    "Transaction",
    "Date valeur",
    "Montant",
    "Devise",
    "BIC",
    "Code pays",
    "Communications",
]
DATE_COLUMNS = ["Date de comptabilisation", "Date valeur"]


def _parse_amount(text: str) -> float:
    """'1.835,18 EUR' -> 1835.18"""
    number = text.split()[0]
    return float(number.replace(".", "").replace(",", "."))


def _parse_metadata(lines: list[str]) -> dict:
    """Read the 'key;value' lines above the table (filters, last balance)."""
    meta = {}
    for line in lines:
        key, _, value = line.partition(SEP)
        key, value = key.strip(), value.strip()
        if key:
            meta[key] = value or None
    if meta.get("Dernier solde"):
        meta["Dernier solde"] = _parse_amount(meta["Dernier solde"])
    if meta.get("Date/heure du dernier solde"):
        meta["Date/heure du dernier solde"] = pd.to_datetime(
            meta["Date/heure du dernier solde"], format="%d/%m/%Y %H:%M:%S"
        )
    return meta


def load_belfius_csv(path: str | Path | bytes) -> pd.DataFrame:
    """Return the transactions as a DataFrame.

    `path` is a file path, or the file's bytes (as Azure hands it over).
    - Montant -> float (Belgian format: '.' thousands, ',' decimals)
    - Dates -> datetime64 (DD/MM/YYYY)
    - Every other column is kept as text, so leading zeros survive
    - The header block (filters, last balance) is in df.attrs["metadata"]
    """
    if isinstance(path, bytes):
        text = path.decode(ENCODING)
    else:
        text = Path(path).read_text(encoding=ENCODING)
    lines = text.splitlines()

    header_idx = next(
        (i for i, line in enumerate(lines) if line.startswith(HEADER_START)), None
    )
    if header_idx is None:
        where = "the file" if isinstance(path, bytes) else path
        raise ValueError(f"No Belfius transaction header found in {where}")

    df = pd.read_csv(
        io.StringIO("\n".join(lines[header_idx:])),
        sep=SEP,
        dtype=str,
        keep_default_na=False,
        na_values=[""],
    )

    missing = set(EXPECTED_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")

    df["Montant"] = pd.to_numeric(
        df["Montant"]
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False),
        errors="raise",
    )
    for col in DATE_COLUMNS:
        df[col] = pd.to_datetime(df[col], format="%d/%m/%Y")

    df.attrs["metadata"] = _parse_metadata(lines[:header_idx])
    return df


_HOLDER = re.compile(r"CARTE.*?\d{4} - (.+?)(?: REF\. :| COURS DE CHANGE|$)")


def extract_card_holder(transaction):
    """Cardholder name from a Belfius card payment, else None."""
    if not isinstance(transaction, str):
        return None
    match = _HOLDER.search(" ".join(transaction.split()))
    return match.group(1) if match else None


if __name__ == "__main__":
    import sys

    data = load_belfius_csv(sys.argv[1])
    print(data.shape)
    print(data.attrs["metadata"])
    print(data.head())
