"""Tests for the database layer, on SQLite in memory. All data below is fake.

The same SQL runs on Azure SQL: both drivers accept `?` placeholders.
"""

import sqlite3

import pandas as pd
import pytest

from family_budget.store import (
    TABLE_COLUMNS,
    create_schema,
    grant_sql,
    load_from_db,
    save_to_db,
    schema_sql,
)


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    create_schema(connection)
    yield connection
    connection.close()


def table(*rows, **extra_columns):
    """A table like process_file returns, from (row_id, day, amount, category)."""
    df = pd.DataFrame(
        {
            "row_id": [r[0] for r in rows],
            "date": pd.to_datetime([r[1] for r in rows]),
            "amount": [r[2] for r in rows],
            "account": "BE00000000000001",
            "bank": "bnp",
            "transaction_type": "card",
            "counterparty": "SHOP",
            "counterparty_iban": None,
            "owner": "Alex",
            "text": "PAIEMENT CARTE",
            "category": [r[3] for r in rows],
            "type": "spending",
            "is_fixed": False,
            "property": None,
        }
    )
    for name, values in extra_columns.items():
        df[name] = values
    return df


A = ("B1-2025-00001", "2025-09-01", -12.34, "groceries")
B = ("B1-2025-00002", "2025-09-02", -850.00, "rent")
C = ("B1-2025-00003", "2025-10-03", 2500.00, "salary")


def test_round_trip_gives_the_same_table(conn):
    df = table(A, B)
    save_to_db(conn, df)
    # the database always has the optional columns (levels, trip): compare the rest
    pd.testing.assert_frame_equal(load_from_db(conn)[df.columns], df)


def test_first_save_counts_every_row_as_new(conn):
    assert save_to_db(conn, table(A, B)) == 2


def test_saving_the_same_table_twice_adds_nothing(conn):
    save_to_db(conn, table(A, B))
    assert save_to_db(conn, table(A, B)) == 0
    assert len(load_from_db(conn)) == 2


def test_a_larger_table_adds_only_its_new_rows(conn):
    save_to_db(conn, table(A, B))
    assert save_to_db(conn, table(A, B, C)) == 1
    assert list(load_from_db(conn)["row_id"]) == [A[0], B[0], C[0]]


def test_new_categories_replace_the_old_ones(conn):
    # a new rule re-categorises an old row: the database must follow
    save_to_db(conn, table(A))
    save_to_db(conn, table(("B1-2025-00001", "2025-09-01", -12.34, "eating out")))
    assert load_from_db(conn).loc[0, "category"] == "eating out"


def test_amounts_keep_their_cents_and_dates_have_no_time(conn):
    save_to_db(conn, table(A))
    row = load_from_db(conn).loc[0]
    assert row["amount"] == pytest.approx(-12.34)
    assert row["date"] == pd.Timestamp("2025-09-01")


def test_optional_columns_are_stored_when_present(conn):
    df = table(A, B, level1=["food", "housing"], level2=["groceries", "rent"])
    df["trip"] = ["Italy 2025", None]
    save_to_db(conn, df)
    loaded = load_from_db(conn)
    assert list(loaded["level1"]) == ["food", "housing"]
    assert loaded.loc[0, "trip"] == "Italy 2025"
    assert pd.isna(loaded.loc[1, "trip"])


def test_missing_values_become_null_and_back(conn):
    save_to_db(conn, table(A))
    row = load_from_db(conn).loc[0]
    assert pd.isna(row["counterparty_iban"]) and pd.isna(row["property"])


def test_empty_database_gives_an_empty_table(conn):
    assert load_from_db(conn).empty


# --- SQL to paste into the Azure Portal query editor --------------------------------


def test_schema_sql_creates_every_column_only_if_missing():
    sql = schema_sql()
    assert sql.startswith("IF OBJECT_ID('transactions') IS NULL")
    assert all(f"[{column}]" in sql for column in TABLE_COLUMNS)


def test_grant_sql_for_a_managed_identity():
    sql = grant_sql("id-family-budget")
    assert "CREATE USER [id-family-budget] FROM EXTERNAL PROVIDER" in sql
    for role in ("db_datareader", "db_datawriter", "db_ddladmin"):
        assert f"ALTER ROLE {role} ADD MEMBER [id-family-budget]" in sql


def test_grant_sql_refuses_anything_but_a_name():
    with pytest.raises(ValueError):
        grant_sql("x]; DROP TABLE transactions; --")


def test_create_schema_twice_is_harmless(conn):
    create_schema(conn)
    save_to_db(conn, table(A))
    assert len(load_from_db(conn)) == 1
