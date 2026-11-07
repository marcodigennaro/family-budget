"""Tests for the HTTP routes. All data below is fake; no server is started."""

import pandas as pd
import pytest

from family_budget.api import load_transactions, route, target_from_url


@pytest.fixture
def df():
    rows = [
        (
            "B1-2025-00001",
            "2025-09-02",
            2500.0,
            "EMPLOYER",
            "salary",
            "income",
            False,
            None,
        ),
        (
            "B1-2025-00002",
            "2025-09-05",
            -61.89,
            "DELHAIZE",
            "groceries",
            "spending",
            False,
            None,
        ),
        (
            "B1-2025-00003",
            "2025-09-06",
            -25.0,
            "PIZZERIA",
            "eating out",
            "spending",
            False,
            "Italy 2025-09",
        ),
        (
            "B1-2025-00004",
            "2025-10-01",
            -877.95,
            None,
            "mortgage",
            "spending",
            True,
            None,
        ),
    ]
    cols = [
        "row_id",
        "date",
        "amount",
        "counterparty",
        "category",
        "type",
        "is_fixed",
        "trip",
    ]
    out = pd.DataFrame(rows, columns=cols)
    out["date"] = pd.to_datetime(out["date"])
    return out


def get(target, df):
    return route("GET", target, df)


# --- the root and unknown paths -------------------------------------------------


def test_root_lists_resources(df):
    status, body = get("/", df)
    assert status == 200 and "/months" in body["resources"]


def test_unknown_path_is_404(df):
    assert get("/accounts", df)[0] == 404


def test_only_get_is_allowed(df):
    assert route("POST", "/months", df)[0] == 405


def test_trailing_slash_is_the_same_resource(df):
    assert get("/months/", df) == get("/months", df)


# --- path parameters ------------------------------------------------------------


def test_month_collection_and_item(df):
    status, months = get("/months", df)
    assert status == 200 and [m["month"] for m in months] == ["2025-09", "2025-10"]
    status, item = get("/months/2025-09", df)
    assert status == 200 and item["income"] == pytest.approx(2500.0)


def test_month_with_bad_format_is_400(df):
    assert get("/months/september", df)[0] == 400


def test_month_without_data_is_404(df):
    assert get("/months/2030-01", df)[0] == 404


def test_one_transaction_by_id(df):
    status, tx = get("/transactions/B1-2025-00002", df)
    assert status == 200 and tx["counterparty"] == "DELHAIZE"
    assert tx["date"] == "2025-09-05"


def test_missing_value_becomes_json_null(df):
    assert get("/transactions/B1-2025-00004", df)[1]["counterparty"] is None


def test_percent_encoded_path_segment(df):
    status, rows = get("/categories/eating%20out/transactions", df)
    assert status == 200 and rows[0]["counterparty"] == "PIZZERIA"


# --- query parameters -----------------------------------------------------------


def test_filter_by_month_and_category(df):
    status, rows = get("/transactions?month=2025-09&category=groceries", df)
    assert status == 200 and [r["row_id"] for r in rows] == ["B1-2025-00002"]


def test_limit_and_newest_first(df):
    rows = get("/transactions?limit=2", df)[1]
    assert [r["date"] for r in rows] == ["2025-10-01", "2025-09-06"]


def test_query_value_with_encoded_space(df):
    rows = get("/transactions?category=eating%20out", df)[1]
    assert len(rows) == 1


def test_unknown_query_parameter_is_400(df):
    assert get("/transactions?colour=red", df)[0] == 400


def test_bad_limit_is_400(df):
    assert get("/transactions?limit=-1", df)[0] == 400


# --- trips ----------------------------------------------------------------------


def test_trip_item(df):
    status, trip = get("/trips/Italy%202025-09", df)
    assert status == 200 and trip["eating out"] == pytest.approx(25.0)


def test_unknown_trip_is_404(df):
    assert get("/trips/Mars%202030", df)[0] == 404


def test_impossible_month_is_400(df):
    assert get("/transactions?month=2025-13", df)[0] == 400
    assert get("/months/2025-00", df)[0] == 400


# --- running on Azure Functions -------------------------------------------------

SAMPLE = (
    "row_id,date,amount,counterparty,category,type,is_fixed,trip\n"
    "B1-1,2025-09-02,2500.0,EMPLOYER,salary,income,False,\n"
    "B1-2,2025-09-05,-61.89,DELHAIZE,groceries,spending,False,\n"
)


def test_load_transactions_from_bytes_or_path(tmp_path):
    path = tmp_path / "transactions.csv"
    path.write_text(SAMPLE, encoding="utf-8")
    from_path = load_transactions(path)
    from_bytes = load_transactions(SAMPLE.encode())
    pd.testing.assert_frame_equal(from_path, from_bytes)
    assert from_bytes["date"].dtype.kind == "M"
    assert from_bytes["is_fixed"].dtype == bool


def test_target_from_azure_url_drops_prefix_and_function_key():
    url = "https://func.azurewebsites.net/api/months/2025-09?code=SECRET"
    assert target_from_url(url) == "/months/2025-09"


def test_target_keeps_other_query_parameters_in_order():
    url = "https://f.net/api/transactions?month=2025-09&code=SECRET&limit=5"
    assert target_from_url(url) == "/transactions?month=2025-09&limit=5"


def test_target_keeps_percent_encoding():
    url = "https://f.net/api/categories/eating%20out/transactions"
    assert target_from_url(url) == "/categories/eating%20out/transactions"


def test_target_of_the_api_root():
    assert target_from_url("https://f.net/api") == "/"
    assert target_from_url("http://localhost:7071/api/?code=x") == "/"


def test_target_with_another_prefix():
    # the dashboard reads its data under /api/web/data/...
    url = "https://f.net/api/web/data/transactions?year=2025"
    assert target_from_url(url, prefix="/api/web/data") == "/transactions?year=2025"


# --- dashboard: more filters and two insights ---------------------------------------


@pytest.fixture
def rich():
    """Fake rows with the columns the ontology and the rules add."""
    out = pd.DataFrame(
        {
            "row_id": ["R1", "R2", "R3", "R4", "R5"],
            "date": pd.to_datetime(
                ["2025-01-05", "2025-01-06", "2025-02-01", "2025-02-02", "2024-12-01"]
            ),
            "amount": [-40.0, -60.0, 400.0, -930.0, -10.0],
            "counterparty": ["SHOP", "CAFE", "TENANT", "OWNER", "SHOP"],
            "category": ["groceries", "eating out", "rent", "rent", "groceries"],
            "type": ["spending", "spending", "income", "spending", "spending"],
            "is_fixed": [False, False, True, True, False],
            "property": [None, None, "Flat A", "Flat A", None],
            "level1": ["food", "food", "housing", "housing", "food"],
            "trip": [None, None, None, None, None],
        }
    )
    return out


def test_filter_by_property_and_group(rich):
    rows = get("/transactions?property=Flat%20A", rich)[1]
    assert sorted(r["row_id"] for r in rows) == ["R3", "R4"]
    rows = get("/transactions?level1=food&year=2025", rich)[1]
    assert sorted(r["row_id"] for r in rows) == ["R1", "R2"]


def test_rows_show_property_and_group(rich):
    row = get("/transactions/R3", rich)[1]
    assert row["property"] == "Flat A" and row["level1"] == "housing"


def test_spending_by_group_for_a_year(rich):
    status, groups = get("/groups?year=2025", rich)
    assert status == 200
    # spending only (rent received is income), positive amounts, biggest first
    assert groups == [
        {"group": "housing", "spending": 930.0, "count": 1},
        {"group": "food", "spending": 100.0, "count": 2},
    ]


def test_groups_with_a_bad_year_is_400(rich):
    assert get("/groups?year=last", rich)[0] == 400


def test_net_result_per_property(rich):
    status, props = get("/properties", rich)
    assert status == 200
    assert props == [
        {"property": "Flat A", "received": 400.0, "paid": 930.0, "net": -530.0}
    ]


def test_insights_work_without_the_optional_columns(df):
    # tables made before the ontology have no level1 or property column
    assert get("/groups", df)[0] == 200
    assert get("/properties", df) == (200, [])
