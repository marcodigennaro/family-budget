"""Tests for trips and trip_summary. All data below is fake."""

import pandas as pd
import pytest

from family_budget.summary import trip_summary
from family_budget.trips import load_trips, tag_trips

HEADER = "name,start,end,country"


def write_trips(tmp_path, *lines, header=HEADER):
    path = tmp_path / "trips.csv"
    path.write_text("\n".join([header, *lines]) + "\n", encoding="utf-8")
    return path


def tx(day, amount, text, category="eating out", type_="spending", fixed=False):
    return {
        "date": pd.Timestamp(day),
        "amount": amount,
        "counterparty": "",
        "text": text,
        "category": category,
        "type": type_,
        "is_fixed": fixed,
    }


TRIPS = pd.DataFrame(
    {
        "name": ["Portugal 2023", "South America 2015"],
        "start": pd.to_datetime(["2023-03-01", "2015-10-01"]),
        "end": pd.to_datetime(["2023-03-09", "2015-12-31"]),
        "country": ["Portugal", ""],
    }
)


@pytest.fixture
def tagged():
    df = pd.DataFrame(
        [
            tx("2023-03-03", -20.0, "PAIEMENT CARTE TASCA LISBOA PORTUGAL"),
            tx("2023-03-04", -12.0, "PAIEMENT CARTE DELHAIZE BRUXELLES"),
            tx("2023-03-05", -877.95, "REMBOURSEMENT CREDIT", "mortgage", fixed=True),
            tx("2023-03-06", -500.0, "VIREMENT", "own accounts", "transfer"),
            tx("2023-04-01", -30.0, "PAIEMENT CARTE PORTO PORTUGAL"),
            tx("2015-11-10", -615.32, "6703 04XX XXXX 3008 2", "credit card"),
        ]
    )
    return tag_trips(df, TRIPS)


# --- load_trips ----------------------------------------------------------------


def test_load_dates_and_empty_country(tmp_path):
    path = write_trips(
        tmp_path, "A,2023-03-01,2023-03-09,Portugal", "B,2015-10-01,2015-12-31,"
    )
    trips = load_trips(path)
    assert trips.loc[0, "start"] == pd.Timestamp(2023, 3, 1)
    assert trips.loc[1, "country"] == ""


def test_load_unknown_country_raises(tmp_path):
    with pytest.raises(ValueError, match="Unknown country"):
        load_trips(write_trips(tmp_path, "A,2023-03-01,2023-03-09,Atlantis"))


def test_load_end_before_start_raises(tmp_path):
    with pytest.raises(ValueError, match="ends before"):
        load_trips(write_trips(tmp_path, "A,2023-03-09,2023-03-01,Portugal"))


def test_load_missing_column_raises(tmp_path):
    with pytest.raises(ValueError, match="missing columns"):
        load_trips(write_trips(tmp_path, "A,2023-03-01", header="name,start"))


# --- tag_trips -----------------------------------------------------------------


def test_spending_abroad_in_window_is_tagged(tagged):
    assert tagged.loc[0, "trip"] == "Portugal 2023"


def test_spending_at_home_during_trip_is_not_tagged(tagged):
    assert pd.isna(tagged.loc[1, "trip"])


def test_fixed_costs_and_transfers_are_not_tagged(tagged):
    assert pd.isna(tagged.loc[2, "trip"])
    assert pd.isna(tagged.loc[3, "trip"])


def test_outside_window_is_not_tagged(tagged):
    assert pd.isna(tagged.loc[4, "trip"])


def test_empty_country_tags_everything_in_window(tagged):
    assert tagged.loc[5, "trip"] == "South America 2015"


def test_keeps_rows_and_does_not_change_input():
    df = pd.DataFrame([tx("2023-03-03", -20.0, "LISBOA PORTUGAL")])
    out = tag_trips(df, TRIPS)
    assert len(out) == 1 and "trip" not in df.columns


def test_first_matching_trip_wins():
    trips = pd.concat([TRIPS.iloc[[0]], TRIPS.iloc[[0]].assign(name="Other")])
    out = tag_trips(pd.DataFrame([tx("2023-03-03", -20.0, "PORTUGAL")]), trips)
    assert out.loc[0, "trip"] == "Portugal 2023"


def test_belfius_iso_country_code():
    df = pd.DataFrame(
        [
            tx(
                "2023-03-03",
                -9.0,
                "BANCONTACT - ACHAT - CAFE - 1100 LISBOA PT - 03/03/23",
            )
        ]
    )
    assert tag_trips(df, TRIPS).loc[0, "trip"] == "Portugal 2023"


# --- trip_summary --------------------------------------------------------------


def test_trip_summary_groups_categories():
    df = pd.DataFrame(
        [
            tx("2023-03-01", -150.0, "", "flights"),
            tx("2023-03-02", -60.0, "", "cash"),
            tx("2023-03-03", -25.0, "", "eating out"),
            tx("2023-03-04", -10.0, "", "souvenirs"),
            tx("2023-03-05", -5.0, "", "eating out"),
        ]
    ).assign(trip="Portugal 2023")
    out = trip_summary(df)
    row = out.loc["Portugal 2023"]
    assert row["flights"] == pytest.approx(150.0)
    assert row["cash"] == pytest.approx(60.0)
    assert row["eating out"] == pytest.approx(30.0)
    assert row["other"] == pytest.approx(10.0)
    assert row["total"] == pytest.approx(250.0)


def test_trip_summary_without_trips_is_empty():
    df = pd.DataFrame([tx("2023-03-01", -10.0, "")])
    assert trip_summary(df).empty
