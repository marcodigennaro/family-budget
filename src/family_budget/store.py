"""Phase 5: keep the processed table in a database (Azure SQL; SQLite in tests).

One table, `transactions`, one row per transaction, row_id as primary key.
The whole table is saved on every upload: a new rule can re-categorise old
rows, so every row must be updatable, and at this size (thousands of rows)
replacing everything inside one database transaction is simple and safe.

The SQL is written to run unchanged on both SQLite and Azure SQL:
[brackets] around names and `?` placeholders work in both.
"""

from __future__ import annotations

import os
import re
import sys

import pandas as pd

from family_budget.normalize import COLUMNS

# column -> SQL type; written in Azure SQL's dialect, SQLite accepts the names
SCHEMA = {
    "row_id": "NVARCHAR(80) NOT NULL PRIMARY KEY",
    "date": "DATE NOT NULL",
    "amount": "DECIMAL(12, 2) NOT NULL",
    "account": "NVARCHAR(40)",
    "bank": "NVARCHAR(10)",
    "transaction_type": "NVARCHAR(20)",
    "counterparty": "NVARCHAR(200)",
    "counterparty_iban": "NVARCHAR(40)",
    "owner": "NVARCHAR(100)",
    "text": "NVARCHAR(2000)",  # longest in the real data: 473 characters
    "category": "NVARCHAR(100)",
    "type": "NVARCHAR(20)",
    "is_fixed": "BIT",
    "property": "NVARCHAR(100)",
    "level1": "NVARCHAR(100)",
    "level2": "NVARCHAR(100)",
    "trip": "NVARCHAR(100)",
}
TABLE_COLUMNS = list(SCHEMA)
assert TABLE_COLUMNS[: len(COLUMNS)] == COLUMNS  # same order as normalize

_NAMES = ", ".join(f"[{c}]" for c in TABLE_COLUMNS)
_INSERT = (
    f"INSERT INTO [transactions] ({_NAMES}) "
    f"VALUES ({', '.join('?' for _ in TABLE_COLUMNS)})"
)


def create_schema(conn) -> None:
    """Create the table if it is not there yet (safe to call every time)."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM [transactions] WHERE 1 = 0")
        return
    except Exception:  # the table does not exist yet (error type differs per driver)
        conn.rollback()
    columns = ", ".join(f"[{name}] {kind}" for name, kind in SCHEMA.items())
    cur.execute(f"CREATE TABLE [transactions] ({columns})")
    conn.commit()


def _value(column: str, value):
    """pandas value -> what both database drivers accept."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if column == "date":
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    if column == "amount":
        return round(float(value), 2)
    if column == "is_fixed":
        return int(bool(value))
    return str(value)


def save_to_db(conn, df: pd.DataFrame) -> int:
    """Replace the stored table with `df`. Returns how many row_ids are new.

    Columns missing from df (e.g. trip when there is no trips.csv) are NULL.
    All or nothing: if anything fails, the previous table stays as it was.
    """
    cur = conn.cursor()
    before = {row[0] for row in cur.execute("SELECT [row_id] FROM [transactions]")}
    full = df.reindex(columns=TABLE_COLUMNS)
    rows = [
        tuple(_value(col, val) for col, val in zip(TABLE_COLUMNS, record, strict=True))
        for record in full.itertuples(index=False, name=None)
    ]
    try:
        cur.execute("DELETE FROM [transactions]")
        cur.executemany(_INSERT, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(set(df["row_id"]) - before)


def load_from_db(conn) -> pd.DataFrame:
    """The stored table, with the same types as the CSV in processed/."""
    cur = conn.cursor()
    cur.execute(f"SELECT {_NAMES} FROM [transactions] ORDER BY [date], [row_id]")
    records = [tuple(row) for row in cur.fetchall()]
    df = pd.DataFrame.from_records(records, columns=TABLE_COLUMNS)
    df["date"] = pd.to_datetime(df["date"])
    df["amount"] = df["amount"].astype(float)
    df["is_fixed"] = df["is_fixed"].astype(bool)
    return df


# --- Azure SQL ----------------------------------------------------------------------


def connect(server: str | None = None, database: str | None = None):
    """Open a connection to Azure SQL with Microsoft Entra ID, no password.

    ActiveDirectoryDefault = your `az login` on the Mac, the app's managed
    identity on Azure (AZURE_CLIENT_ID says which one). Server and database
    default to the SQL_SERVER and SQL_DATABASE settings.
    """
    import mssql_python  # only needed when a real database is used

    server = server or os.environ["SQL_SERVER"]
    database = database or os.environ["SQL_DATABASE"]
    return mssql_python.connect(
        f"Server=tcp:{server}.database.windows.net,1433;Database={database};"
        "Encrypt=yes;TrustServerCertificate=no;Authentication=ActiveDirectoryDefault"
    )


def schema_sql() -> str:
    """T-SQL that creates the table if it is missing (for the portal query editor)."""
    columns = ",\n    ".join(f"[{name}] {kind}" for name, kind in SCHEMA.items())
    return (
        "IF OBJECT_ID('transactions') IS NULL\n"
        f"CREATE TABLE [transactions] (\n    {columns}\n);"
    )


def grant_sql(identity: str) -> str:
    """T-SQL that lets a managed identity read, write and create tables."""
    if not re.fullmatch(r"[A-Za-z0-9-]+", identity):  # a name, never SQL
        raise ValueError(f"not an identity name: {identity!r}")
    lines = [
        f"IF USER_ID('{identity}') IS NULL",
        f"    CREATE USER [{identity}] FROM EXTERNAL PROVIDER;",
    ]
    lines += [
        f"ALTER ROLE {role} ADD MEMBER [{identity}];"
        for role in ("db_datareader", "db_datawriter", "db_ddladmin")
    ]
    return "\n".join(lines)


def grant(conn, identity: str) -> None:
    """Run grant_sql (needs the SQL driver and an admin login)."""
    conn.cursor().execute(grant_sql(identity))
    conn.commit()


if __name__ == "__main__":
    # python -m family_budget.store print-sql IDENTITY     (no driver needed)
    # python -m family_budget.store SERVER DATABASE schema|grant IDENTITY|count
    if sys.argv[1] == "print-sql":
        print(schema_sql() + "\n\n" + grant_sql(sys.argv[2]))
        sys.exit()
    server, database, command, *rest = sys.argv[1:]
    with connect(server, database) as connection:
        if command == "schema":
            create_schema(connection)
        elif command == "grant":
            grant(connection, rest[0])
        elif command == "count":
            create_schema(connection)
            print(len(load_from_db(connection)), "rows in transactions")
        else:
            sys.exit(f"unknown command {command!r}")
