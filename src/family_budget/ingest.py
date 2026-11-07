"""Phase 4: process a bank export that arrives as bytes (e.g. dropped in raw/).

On your Mac the folder says which bank a file comes from (data/BNP/<account>/,
data/BELFIUS/<account>/). In the cloud every export lands in the same inbox,
so the code looks at the beginning of the file instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from rdflib import Graph

from family_budget.belfius import HEADER_START as BELFIUS_HEADER
from family_budget.belfius import load_belfius_csv
from family_budget.bnp import load_bnp_csv
from family_budget.classify import classify
from family_budget.normalize import COLUMNS, normalize_belfius, normalize_bnp
from family_budget.ontology import add_levels, load_tree
from family_budget.rules import Rule, load_rules
from family_budget.trips import load_trips, tag_trips

# Plain-ASCII pieces of each bank's column names, so the check works
# whatever the file's encoding (BNP: UTF-8 with BOM, Belfius: Windows-1252).
BNP_HEADER = "Date valeur;Montant;Devise du compte;"
LINES_TO_CHECK = 30  # Belfius puts about 12 lines above its table


def detect_bank(content: bytes | str) -> str:
    """Return "bnp" or "belfius" from the first lines of an export.

    Raises ValueError for anything else (another bank, a PDF, an empty file).
    """
    if isinstance(content, bytes):
        # replace undecodable bytes: only the ASCII markers matter here
        content = content.decode("utf-8", errors="replace")
    first_lines = content.lstrip("﻿").splitlines()[:LINES_TO_CHECK]

    if first_lines and BNP_HEADER in first_lines[0]:
        return "bnp"
    if any(line.startswith(BELFIUS_HEADER) for line in first_lines):
        return "belfius"
    raise ValueError(
        "not a BNP or Belfius export: no known header in the first "
        f"{LINES_TO_CHECK} lines"
    )


# bank name -> (read the export, turn it into the standard COLUMNS)
READERS = {
    "bnp": (load_bnp_csv, normalize_bnp),
    "belfius": (load_belfius_csv, normalize_belfius),
}


@dataclass
class Config:
    """Everything from config/, already loaded. Only the rules are required."""

    rules: list[Rule]  # exceptional.csv first, then known_expenses.csv
    tree: Graph | None = None  # categories.ttl
    trips: pd.DataFrame | None = field(default=None)  # trips.csv


def load_config(folder: Path) -> Config:
    """Read the files of config/ (downloaded to `folder`).

    known_expenses.csv is required; exceptional.csv, categories.ttl and
    trips.csv are used when present. Same file names as in data/.
    """
    rules = load_rules(folder / "known_expenses.csv")
    if (folder / "exceptional.csv").exists():
        rules = load_rules(folder / "exceptional.csv") + rules  # they win
    tree_path, trips_path = folder / "categories.ttl", folder / "trips.csv"
    return Config(
        rules=rules,
        tree=load_tree(tree_path) if tree_path.exists() else None,
        trips=load_trips(trips_path) if trips_path.exists() else None,
    )


def process_file(
    content: bytes, config: Config, existing: pd.DataFrame | None
) -> tuple[pd.DataFrame, int]:
    """Add one bank export to the table. Returns (new table, rows added).

    Nothing is read or written here: the caller downloads `content` and
    `existing` (processed/transactions.csv) and uploads the result, so this
    runs the same on your Mac, in tests and on Azure.

    The same file twice adds nothing (rows are matched on row_id), and the
    whole table is classified again, so a new rule also fixes older rows.
    """
    load, normalize = READERS[detect_bank(content)]
    new = normalize(load(content))

    old = existing[COLUMNS] if existing is not None else new.iloc[0:0]
    table = pd.concat([old, new], ignore_index=True)
    table = table.drop_duplicates("row_id").sort_values("date", ignore_index=True)
    added = len(table) - len(old)

    table = classify(table, config.rules)
    if config.tree is not None:
        table = add_levels(table, config.tree)
    if config.trips is not None:
        table = tag_trips(table, config.trips)
    return table, added
