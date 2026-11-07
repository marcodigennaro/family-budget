"""Step 3 of the pipeline: give every transaction a category.

Think of it as a left join between the transactions and the rule files:
every transaction is kept, and each one picks up the result columns
(category, type, is_fixed) of the FIRST rule that matches it.

The order of the rules is therefore part of the logic. __main__ passes
exceptional.csv first, then known_expenses.csv in file order, so put
specific rules above general ones (PARTENA direct debit before PARTENA).
"""

from __future__ import annotations

import pandas as pd

from family_budget.rules import Rule, matches

UNCATEGORISED = "Uncategorised"


def classify(df: pd.DataFrame, rules: list[Rule]) -> pd.DataFrame:
    """Return a copy of df with new columns: category, type, is_fixed, property.

    Every row is kept (same length, same order). A row that no rule fits
    becomes 'Uncategorised', counted as income if money came in and as
    spending if it went out, so the monthly totals still add up.
    """
    labels = []
    for row in df.to_dict("records"):
        # first rule that fits; None if none does
        rule = next((r for r in rules if matches(row, r)), None)
        if rule:
            labels.append((rule.category, rule.type, rule.is_fixed, rule.property))
        else:
            kind = "income" if row["amount"] > 0 else "spending"
            labels.append((UNCATEGORISED, kind, False, None))

    out = df.copy()
    out["category"] = [label[0] for label in labels]
    out["type"] = [label[1] for label in labels]
    out["is_fixed"] = pd.Series(
        [label[2] for label in labels], index=df.index, dtype=bool
    )
    out["property"] = pd.Series(
        [label[3] for label in labels], index=df.index, dtype=object
    )
    return out
