"""Optional step after classify: tag spending made during a trip.

`category` says what the money was for (flights, eating out, cash...).
`trip` says where and when (Portugal 2023, Italy 2025-12...). The two are
independent, so you can look at a whole trip or at one category everywhere.

data/trips.csv has one line per trip window:

    name,start,end,country
    Portugal 2023,2023-03-01,2023-03-09,Portugal
    South America 2015,2015-10-01,2015-12-31,

A spending row gets a trip when its date is inside [start, end] and, if
`country` is filled, its text names that country. An empty country means
"everything in the window" (for trips where the bank text has no detail).
Lines are read top to bottom; the first one that fits wins.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

TRIP_COLUMNS = ["name", "start", "end", "country"]

# How each country appears in the bank text: BNP writes the French name,
# Belfius card payments end with the ISO code (" FR ", " IT ").
COUNTRY_PATTERNS = {
    "Italy": r"\bITALIE\b|\bIT\b",
    "France": r"\bFRANCE\b|\bFR\b",
    "Portugal": r"\bPORTUGAL\b|\bPT\b",
    "Switzerland": r"\bSUISSE\b|\bCH\b",
    "Spain": r"\bESPAGNE\b|\bES\b",
    "Germany": r"\bALLEMAGNE\b|\bDE\b",
    "Austria": r"\bAUTRICHE\b|\bAT\b",
    "UK": r"\bROYAUME-UNI\b|\bGB\b",
    "Thailand": r"\bTHAILANDE\b|\bTH\b",
    "New Zealand": r"\bNOUVELLE[- ]ZELANDE\b|\bNZ\b",
    "South America": (
        r"BRASIL|BRESIL|ARGENTIN|BARILOCHE|\bARS\b|\bBRL\b|CHILE|CHILI"
        r"|PERU|BOLIVI|URUGUAY|PARAGUAY"
    ),
}


def load_trips(path: str | Path) -> pd.DataFrame:
    """Read trips.csv. Dates become datetimes; an empty country stays empty."""
    trips = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = set(TRIP_COLUMNS) - set(trips.columns)
    if missing:
        raise ValueError(f"{Path(path).name} is missing columns: {sorted(missing)}")
    unknown = set(trips["country"]) - set(COUNTRY_PATTERNS) - {""}
    if unknown:
        raise ValueError(f"Unknown country {sorted(unknown)}; add it to trips.py")
    trips["start"] = pd.to_datetime(trips["start"])
    trips["end"] = pd.to_datetime(trips["end"])
    if (trips["end"] < trips["start"]).any():
        raise ValueError("A trip ends before it starts")
    return trips[TRIP_COLUMNS]


def tag_trips(df: pd.DataFrame, trips: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of df with a `trip` column (empty when not on a trip).

    Only variable spending is tagged: rent, bills and other fixed costs keep
    running at home while you travel, and transfers are not spending.
    """
    out = df.copy()
    out["trip"] = pd.Series([pd.NA] * len(out), index=out.index, dtype="string")
    eligible = (out["type"] == "spending") & ~out["is_fixed"].astype(bool)
    text = (out["counterparty"].fillna("") + " " + out["text"].fillna("")).str.upper()
    for trip in trips.itertuples():
        hit = eligible & out["trip"].isna() & out["date"].between(trip.start, trip.end)
        if trip.country:
            hit &= text.str.contains(COUNTRY_PATTERNS[trip.country], regex=True)
        out.loc[hit, "trip"] = trip.name
    return out
