"""Tests for classify. All data below is fake."""

import pandas as pd
import pytest

from family_budget.classify import UNCATEGORISED, classify
from family_budget.rules import Rule


def rule(category, type_="spending", is_fixed=False, **match):
    empty = dict(
        transaction_type=None, counterparty_iban=None, keyword=None, amount=None
    )
    return Rule(**{**empty, **match}, category=category, type=type_, is_fixed=is_fixed)


RULES = [
    rule(
        "health insurance",
        is_fixed=True,
        transaction_type="direct_debit",
        keyword="PARTENA",
    ),
    rule("health", keyword="PARTENA"),
    rule("mortgage", is_fixed=True, transaction_type="loan"),
    rule("own accounts", "transfer", counterparty_iban="BE00000000000009"),
    rule("groceries", keyword="DELHAIZE"),
]


def tx(amount, transaction_type="card", counterparty="", iban=None, text=""):
    return {
        "amount": amount,
        "transaction_type": transaction_type,
        "counterparty": counterparty,
        "counterparty_iban": iban,
        "text": text,
    }


@pytest.fixture
def df():
    return pd.DataFrame(
        [
            tx(-14.00, "direct_debit", "PARTENA MUTUALITE LIBRE"),
            tx(39.37, "transfer", "PARTENA - MUTUALITE LIBRE"),
            tx(-877.95, "loan"),
            tx(-1000.00, "transfer", "Marco", "BE00000000000009"),
            tx(-61.89, "card", "DELHAIZE ROODEBE BRUXELLES"),
            tx(-25.25, "transfer", "A Friend"),
            tx(24.00, "transfer", "Another Friend"),
        ]
    )


@pytest.fixture
def out(df):
    return classify(df, RULES)


def test_keeps_every_row_and_order(df, out):
    assert len(out) == len(df)
    assert list(out["amount"]) == list(df["amount"])


def test_adds_the_result_columns(df, out):
    new = ["category", "type", "is_fixed", "property"]
    assert list(out.columns) == [*df.columns, *new]


def test_does_not_change_input(df):
    classify(df, RULES)
    assert "category" not in df.columns


def test_first_matching_rule_wins(out):
    assert out.loc[0, "category"] == "health insurance"
    assert out.loc[1, "category"] == "health"


def test_fixed_flags(out):
    assert out.loc[0, "is_fixed"]
    assert out.loc[2, "is_fixed"]
    assert not out.loc[4, "is_fixed"]


def test_mortgage_and_own_account(out):
    assert out.loc[2, "category"] == "mortgage"
    assert out.loc[3, "type"] == "transfer"


def test_no_match_spending(out):
    assert out.loc[5, "category"] == UNCATEGORISED
    assert out.loc[5, "type"] == "spending"
    assert not out.loc[5, "is_fixed"]


def test_no_match_money_in_is_income(out):
    assert out.loc[6, "category"] == UNCATEGORISED
    assert out.loc[6, "type"] == "income"


def test_no_rules_everything_uncategorised(df):
    assert (classify(df, [])["category"] == UNCATEGORISED).all()


def test_empty_table():
    empty = pd.DataFrame(columns=["amount", "transaction_type", "counterparty"])
    out = classify(empty, RULES)
    assert out.empty and "category" in out.columns


def test_works_with_a_non_default_index(df):
    # after concat / drop_duplicates the index is not 0..n-1
    shuffled = df.set_axis([10, 3, 7, 1, 99, 42, 5])
    out = classify(shuffled, RULES)
    assert out.loc[10, "is_fixed"]  # Partena direct debit: fixed
    assert not out.loc[99, "is_fixed"]  # Delhaize: variable
    assert out.loc[10, "category"] == "health insurance"


def test_property_is_copied_from_the_rule():
    rules = [
        rule("rent", "income", True, keyword="TENANT A", property="Avroy 206"),
        rule("rent", is_fixed=True, keyword="OWNER", property="Avroy 206"),
        rule("groceries", keyword="DELHAIZE"),
    ]
    df = pd.DataFrame(
        [
            tx(400.0, "transfer", "TENANT A"),
            tx(-930.3, "transfer", "OWNER"),
            tx(-12.0, counterparty="DELHAIZE"),
            tx(-5.0, counterparty="UNKNOWN"),
        ]
    )
    out = classify(df, rules)
    assert list(out["category"]) == ["rent", "rent", "groceries", UNCATEGORISED]
    assert list(out["type"])[:2] == ["income", "spending"]
    assert out["property"].tolist() == ["Avroy 206", "Avroy 206", None, None]
