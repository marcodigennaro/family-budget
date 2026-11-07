"""Rules: one line of known_expenses.csv or exceptional.csv.

A rule has two halves:
- match fields (transaction_type, counterparty_iban, keyword, amount, date,
  direction): every filled field must fit the transaction, empty means
  "anything";
- result fields (category, type, is_fixed, and optionally property): copied
  onto the transaction. property names the flat or house a payment belongs to
  ("Avroy 206"), so rent paid and rent received can be added up per property.

load_rules reads a file into a list of Rule; matches checks one rule
against one transaction. Choosing between rules is classify's job.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

MATCH_COLUMNS = ["transaction_type", "counterparty_iban", "keyword", "amount"]
RESULT_COLUMNS = ["category", "type", "is_fixed"]
OPTIONAL_MATCH_COLUMNS = ["date", "direction"]  # may be absent from the file
OPTIONAL_RESULT_COLUMNS = ["property"]  # may be absent from the file
DIRECTIONS = {"in", "out"}  # money in (amount > 0) or out (amount < 0)
TYPES = {"spending", "saving", "income", "transfer", "exceptional"}
TRUE_VALUES = {"true", "yes", "1"}
FALSE_VALUES = {"false", "no", "0"}


@dataclass(frozen=True)
class Rule:
    """One line of a rule file. None in a match field means 'match anything'."""

    transaction_type: str | None
    counterparty_iban: str | None
    keyword: str | None
    amount: float | None
    category: str
    type: str
    is_fixed: bool
    date: pd.Timestamp | None = None
    direction: str | None = None
    property: str | None = None


def _clean(value) -> str | None:
    """Empty or missing cell -> None, otherwise trimmed text."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _to_bool(value, line: int) -> bool:
    text = (_clean(value) or "").lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    raise ValueError(f"Line {line}: is_fixed must be true or false, got {value!r}")


def load_rules(path: str | Path) -> list[Rule]:
    """Return the rules in file order. Empty match cells mean 'match anything'.

    Lines starting with # are comments and blank lines are ignored, so the
    file can be split into commented groups. Error messages keep the real
    line number of the file.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False, skip_blank_lines=False)
    df.index = df.index + 2  # line 1 is the header
    first = df.iloc[:, 0]
    is_comment = first.str.lstrip().str.startswith("#")
    is_blank = (df == "").all(axis=1)
    df = df[~is_comment & ~is_blank]

    missing = set(MATCH_COLUMNS + RESULT_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"{Path(path).name} is missing columns: {sorted(missing)}")
    for col in OPTIONAL_MATCH_COLUMNS + OPTIONAL_RESULT_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    rules = []
    for line, row in df.to_dict("index").items():
        keyword = _clean(row["keyword"])
        iban = _clean(row["counterparty_iban"])
        amount = _clean(row["amount"])
        date = _clean(row["date"])
        direction = (_clean(row["direction"]) or "").lower() or None
        rule = Rule(
            transaction_type=_clean(row["transaction_type"]),
            counterparty_iban=iban.replace(" ", "").upper() if iban else None,
            keyword=keyword.upper() if keyword else None,
            amount=float(amount) if amount else None,
            category=_clean(row["category"]) or "",
            type=(_clean(row["type"]) or "").lower(),
            is_fixed=_to_bool(row["is_fixed"], line),
            date=pd.Timestamp(date) if date else None,
            direction=direction,
            property=_clean(row["property"]),
        )
        if not rule.category:
            raise ValueError(f"Line {line}: category is empty")
        if rule.type not in TYPES:
            raise ValueError(f"Line {line}: type must be one of {sorted(TYPES)}")
        if direction and direction not in DIRECTIONS:
            raise ValueError(f"Line {line}: direction must be 'in', 'out' or empty")
        # direction alone is too broad, so it does not count as a match field
        if all(getattr(rule, col) is None for col in [*MATCH_COLUMNS, "date"]):
            raise ValueError(
                f"Line {line}: rule has no match field, it would match all"
            )
        rules.append(rule)
    return rules


def matches(row: Mapping, rule: Rule) -> bool:
    """True when every filled match field of the rule fits the transaction."""
    if rule.transaction_type and row.get("transaction_type") != rule.transaction_type:
        return False
    if (
        rule.counterparty_iban
        and row.get("counterparty_iban") != rule.counterparty_iban
    ):
        return False
    if rule.date is not None and row.get("date") != rule.date:
        return False
    if rule.direction == "in" and not row.get("amount") > 0:
        return False
    if rule.direction == "out" and not row.get("amount") < 0:
        return False
    if rule.amount is not None and abs(row.get("amount") - rule.amount) >= 0.005:
        return False
    if rule.keyword:
        parts = (row.get("counterparty"), row.get("text"))
        haystack = " ".join(p for p in parts if isinstance(p, str)).upper()
        if rule.keyword not in haystack:
            return False
    return True
