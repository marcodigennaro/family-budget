"""Run the whole pipeline:  python -m family_budget

1. load:       read every CSV in data/BNP/<account>/ and data/BELFIUS/<account>/
2. normalize:  one English table, duplicates dropped (normalize.py)
3. classify:   exceptional.csv, then known_expenses.csv; first match wins
4. ontology:   level1/level2 from the category tree, data/categories.ttl (ontology.py)
5. trips:      tag spending made during a trip, from data/trips.csv (trips.py)
6. summarize:  monthly totals, exceptional projects, trips (summary.py)
7. save:       transactions and summaries in data/processed/
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from family_budget.belfius import load_belfius_csv
from family_budget.bnp import load_bnp_csv
from family_budget.classify import UNCATEGORISED, classify
from family_budget.config import (
    BELFIUS_DIR,
    BNP_DIR,
    CATEGORIES_PATH,
    EXCEPTIONAL_PATH,
    OUTPUT_DIR,
    RULES_PATH,
    TRIPS_PATH,
)
from family_budget.normalize import normalize_belfius, normalize_bnp
from family_budget.ontology import add_levels, load_tree, missing_categories
from family_budget.rules import load_rules
from family_budget.summary import exceptional_summary, monthly_summary, trip_summary
from family_budget.trips import load_trips, tag_trips

SOURCES = [
    (BNP_DIR, load_bnp_csv, normalize_bnp),
    (BELFIUS_DIR, load_belfius_csv, normalize_belfius),
]


def find_exports(bank_folder: Path) -> list[Path]:
    """Every CSV in the bank's account folders (or directly in the bank folder)."""
    if not bank_folder.is_dir():
        return []
    return sorted(p for p in bank_folder.rglob("*") if p.suffix.lower() == ".csv")


_ACCOUNT = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{8,30}")  # an IBAN, once cleaned up


def account_problem(path: Path, table: pd.DataFrame) -> str | None:
    """A message if the file is in another account's folder, else None.

    Only folders named after an account are checked ("BE00 0000 0000 0001",
    "BE00_0000_0000_0001"); a folder called "joint" or "BNP" is left alone.
    """
    folder = re.sub(r"[^A-Z0-9]", "", path.parent.name.upper())
    if not _ACCOUNT.fullmatch(folder):
        return None
    accounts = set(table["account"].dropna())
    if accounts and accounts != {folder}:
        return f"{path.name} is for {', '.join(sorted(accounts))}, not {folder}"
    return None


def load_all(sources=None) -> pd.DataFrame:
    tables = []
    for folder, load, normalize in sources or SOURCES:
        for path in find_exports(folder):
            try:
                table = normalize(load(path))
            except ValueError as error:
                print(f"  skipped {path.name}: {error}")
                continue
            problem = account_problem(path, table)
            if problem:
                print(f"  WARNING {problem}: move it to the right folder")
            print(f"  {path.relative_to(folder.parent)}: {len(table)} rows")
            tables.append(table)
    if not tables:
        raise SystemExit("No CSV files found. Check the folders in config.py.")
    combined = pd.concat(tables, ignore_index=True)
    # Overlapping exports contain the same transactions: keep each row_id once.
    return combined.drop_duplicates("row_id").sort_values("date", ignore_index=True)


def main() -> None:
    print("Loading exports...")
    df = load_all()
    first, last = df["date"].min(), df["date"].max()
    print(f"\n{len(df)} unique transactions, {first:%d/%m/%Y} to {last:%d/%m/%Y}")

    # Exceptional rows first: they win over the general rules.
    rules = load_rules(RULES_PATH)
    if EXCEPTIONAL_PATH.exists():
        rules = load_rules(EXCEPTIONAL_PATH) + rules
    df = classify(df, rules)
    unknown = df[df["category"] == UNCATEGORISED]
    print(f"Uncategorised: {len(unknown)} rows ({len(unknown) / len(df):.0%})")
    top = unknown.groupby("counterparty")["amount"].agg(["count", "sum"])
    print("Most frequent uncategorised counterparties:")
    print(top.sort_values("count", ascending=False).head(10).round(2))

    if CATEGORIES_PATH.exists():
        tree = load_tree(CATEGORIES_PATH)
        missing = missing_categories(tree, df["category"].unique())
        if missing:
            print(f"\nNot in categories.ttl (used as their own group): {missing}")
        df = add_levels(df, tree)
        spending = df[
            (df["type"] == "spending") & (df["date"] >= last - pd.DateOffset(years=1))
        ]
        print("\nSpending by group, last 12 months:")
        print(
            (-spending.groupby("level1")["amount"].sum())
            .sort_values(ascending=False)
            .round(2)
        )

    if TRIPS_PATH.exists():
        df = tag_trips(df, load_trips(TRIPS_PATH))
        print("\nTrips:")
        print(trip_summary(df))

    print("\nExceptional (not in monthly totals):")
    print(exceptional_summary(df))

    print("\nMonthly summary (last 12 months):")
    print(monthly_summary(df).tail(12))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_DIR / "transactions.csv", index=False)
    monthly_summary(df).to_csv(OUTPUT_DIR / "monthly_summary.csv")
    trip_summary(df).to_csv(OUTPUT_DIR / "trip_summary.csv")
    unknown.sort_values(["counterparty", "date"]).to_csv(
        OUTPUT_DIR / "uncategorised.csv", index=False
    )
    print(f"\nSaved transactions, monthly_summary and uncategorised in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
