"""Step 5 of the pipeline: totals for the dashboard.

The `type` set by classify decides where a transaction is counted:
spending (fixed or variable), saving, income; transfer and exceptional
are left out of the monthly totals. Exceptional rows get their own
summary, one line per project.
"""

from __future__ import annotations

import pandas as pd

SUMMARY_COLUMNS = ["income", "fixed_spending", "variable_spending", "saving", "net"]


def monthly_summary(df: pd.DataFrame) -> pd.DataFrame:
    """One row per month (index 'YYYY-MM'). Transfers and exceptional rows are left out.

    Spending and saving are shown as positive amounts (money out).
    Refunds inside a spending category lower that spending.
    net = income - fixed_spending - variable_spending - saving.
    """
    if df.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS, index=pd.Index([], name="month"))

    df = df[~df["type"].isin(["transfer", "exceptional"])]
    month = df["date"].dt.strftime("%Y-%m")
    spending = df["type"] == "spending"

    parts = {
        "income": df["amount"].where(df["type"] == "income", 0),
        "fixed_spending": -df["amount"].where(spending & df["is_fixed"], 0),
        "variable_spending": -df["amount"].where(spending & ~df["is_fixed"], 0),
        "saving": -df["amount"].where(df["type"] == "saving", 0),
    }
    out = pd.DataFrame(parts).groupby(month.rename("month")).sum()
    out["net"] = (
        out["income"] - out["fixed_spending"] - out["variable_spending"] - out["saving"]
    )
    return out[SUMMARY_COLUMNS].round(2)


def exceptional_summary(df: pd.DataFrame) -> pd.DataFrame:
    """One row per exceptional category (e.g. a flat purchase): money in, out, net."""
    df = df[df["type"] == "exceptional"]
    grouped = df.groupby("category")["amount"]
    out = pd.DataFrame(
        {
            "first_date": df.groupby("category")["date"].min(),
            "money_in": grouped.apply(lambda s: s[s > 0].sum()),
            "money_out": grouped.apply(lambda s: -s[s < 0].sum()),
            "net": grouped.sum(),
        }
    )
    money = ["money_in", "money_out", "net"]
    out[money] = out[money].round(2)
    return out.sort_values("first_date")


TRIP_GROUPS = [
    "flights",
    "accommodation",
    "transport",
    "taxi",
    "cash",
    "eating out",
    "groceries",
]


def trip_summary(df: pd.DataFrame) -> pd.DataFrame:
    """One row per trip: first day, total spent and the main categories.

    Amounts are positive (money out). Everything outside TRIP_GROUPS is "other".
    Needs the `trip` column added by trips.tag_trips.
    """
    columns = ["first_date", *TRIP_GROUPS, "other", "total"]
    if "trip" not in df or df["trip"].isna().all():
        return pd.DataFrame(columns=columns, index=pd.Index([], name="trip"))
    df = df[df["trip"].notna()]
    group = df["category"].where(df["category"].isin(TRIP_GROUPS), "other")
    out = (-df["amount"]).groupby([df["trip"], group]).sum().unstack(fill_value=0)
    out = out.reindex(columns=[*TRIP_GROUPS, "other"], fill_value=0)
    out["total"] = out.sum(axis=1)
    out = out.round(2)
    out["first_date"] = df.groupby("trip")["date"].min()
    out.index.name = "trip"
    return out[columns].sort_values("first_date")
