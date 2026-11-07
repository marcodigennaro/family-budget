"""Tests for monthly_summary. All data below is fake."""

import pandas as pd
import pytest

from family_budget.summary import (
    SUMMARY_COLUMNS,
    exceptional_summary,
    monthly_summary,
)


def tx(day, amount, type_, is_fixed=False, category="x"):
    return {
        "category": category,
        "date": pd.Timestamp(day),
        "amount": amount,
        "type": type_,
        "is_fixed": is_fixed,
    }


@pytest.fixture
def summary():
    df = pd.DataFrame(
        [
            tx("2025-09-02", 3000.00, "income"),
            tx("2025-09-01", -877.95, "spending", is_fixed=True),
            tx("2025-09-12", -76.05, "spending", is_fixed=True),
            tx("2025-09-23", -61.89, "spending"),
            tx("2025-09-30", 39.37, "spending"),  # refund lowers spending
            tx("2025-09-03", -204.16, "saving", is_fixed=True),
            tx("2025-09-25", -1000.00, "transfer"),
            tx("2025-09-26", 2000.00, "transfer"),
            tx("2025-10-01", -877.95, "spending", is_fixed=True),
            tx("2025-09-15", -200000.00, "exceptional", category="flat"),
        ]
    )
    return monthly_summary(df)


def test_columns_and_months(summary):
    assert list(summary.columns) == SUMMARY_COLUMNS
    assert list(summary.index) == ["2025-09", "2025-10"]
    assert summary.index.name == "month"


def test_income(summary):
    assert summary.loc["2025-09", "income"] == pytest.approx(3000.00)


def test_fixed_spending_is_positive(summary):
    assert summary.loc["2025-09", "fixed_spending"] == pytest.approx(954.00)


def test_variable_spending_includes_refund(summary):
    assert summary.loc["2025-09", "variable_spending"] == pytest.approx(22.52)


def test_saving_is_separate_from_spending(summary):
    assert summary.loc["2025-09", "saving"] == pytest.approx(204.16)


def test_transfers_are_ignored(summary):
    # net would be off by 1000 if transfers were counted
    assert summary.loc["2025-09", "net"] == pytest.approx(1819.32)


def test_month_without_income(summary):
    assert summary.loc["2025-10", "income"] == 0
    assert summary.loc["2025-10", "net"] == pytest.approx(-877.95)


def test_empty_table():
    empty = pd.DataFrame(columns=["date", "amount", "type", "is_fixed"])
    out = monthly_summary(empty)
    assert out.empty and list(out.columns) == SUMMARY_COLUMNS


def test_exceptional_rows_are_ignored(summary):
    # same net as before: the -200000 flat payment is not in the monthly totals
    assert summary.loc["2025-09", "net"] == pytest.approx(1819.32)


def test_exceptional_summary():
    df = pd.DataFrame(
        [
            tx("2019-04-03", 28124.37, "exceptional", category="flat A"),
            tx("2019-05-07", 185250.00, "exceptional", category="flat A"),
            tx("2019-06-27", -210872.14, "exceptional", category="flat A"),
            tx("2021-01-10", -5000.00, "exceptional", category="company"),
            tx("2019-05-01", -50.00, "spending"),
        ]
    )
    out = exceptional_summary(df)
    assert list(out.index) == ["flat A", "company"]
    assert out.loc["flat A", "money_in"] == pytest.approx(213374.37)
    assert out.loc["flat A", "money_out"] == pytest.approx(210872.14)
    assert out.loc["flat A", "net"] == pytest.approx(2502.23)
    assert out.loc["company", "money_out"] == pytest.approx(5000.00)


def test_exceptional_summary_without_exceptional_rows():
    df = pd.DataFrame([tx("2019-05-01", -50.00, "spending")])
    assert exceptional_summary(df).empty
