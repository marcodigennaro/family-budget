"""Load a BNP Paribas Fortis account-history CSV export into a pandas DataFrame."""

from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd

ENCODING = "utf-8-sig"  # UTF-8 with a BOM (﻿) at the start
SEP = ";"

EXPECTED_COLUMNS = [
    "Nº de séquence",
    "Date d'exécution",
    "Date valeur",
    "Montant",
    "Devise du compte",
    "Numéro de compte",
    "Type de transaction",
    "Contrepartie",
    "Nom de la contrepartie",
    "Communication",
    "Détails",
    "Statut",
    "Motif du refus",
]
DATE_COLUMNS = ["Date d'exécution", "Date valeur"]


def parse_amount(text: str) -> float:
    """'-877,95' (new exports) or '-877.95' (old exports) -> -877.95.

    With both separators, the comma is the decimal one: '-1.250,00' -> -1250.0.
    """
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    return float(text)


def load_bnp_csv(path: str | Path | bytes) -> pd.DataFrame:
    """Return the transactions as a DataFrame.

    `path` is a file path, or the file's bytes (as Azure hands it over).
    - Montant -> float (Belgian format: ',' decimals, optional '.' thousands)
    - Dates -> datetime64 (DD/MM/YYYY)
    - Every other column is kept as text; empty cells become NaN
    """
    source = io.BytesIO(path) if isinstance(path, bytes) else path
    df = pd.read_csv(
        source,
        sep=SEP,
        encoding=ENCODING,
        dtype=str,
        keep_default_na=False,
        na_values=[""],
    )

    missing = set(EXPECTED_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Not a BNP export, missing columns: {sorted(missing)}")

    df["Montant"] = df["Montant"].map(parse_amount).astype(float)
    for col in DATE_COLUMNS:
        df[col] = pd.to_datetime(df[col], format="%d/%m/%Y")
    return df


_CARD = re.compile(r"XXXX \d{4} (.+?) \d{2}/\d{2}/\d{4}")


def extract_merchant(description):
    """Shop name from a BNP card-payment description, else None."""
    if not isinstance(description, str):  # NaN or None from empty cells
        return None
    match = _CARD.search(description)
    return match.group(1) if match else None
