"""Tests for belfius. Run with:  pytest test_belfius.py -v

Most tests use a small synthetic file with fake data. To also check your
real export, set:  BELFIUS_CSV=/path/to/export.csv
"""

import os
from pathlib import Path

import pandas as pd
import pytest

from family_budget.belfius import EXPECTED_COLUMNS, load_belfius_csv

HEADER = ";".join(EXPECTED_COLUMNS)

PREAMBLE = [
    "Date de comptabilisation à partir de;",
    "Date de comptabilisation jusqu'au;",
    "Montant à partir de;",
    "Montant jusqu'à;",
    "Numéro d'extrait à partir de;",
    "Numéro d'extrait jusqu'au;",
    "Communication;",
    "Nom contrepartie contient;",
    "Compte contrepartie;",
    "Dernier solde;1.835,18 EUR",
    "Date/heure du dernier solde;07/10/2026 10:17:42",
    ";",
]

ROWS = [
    # pending row: no extract / transaction number
    (
        "BE00 0000 0000 0001;07/10/2026;;;BE00 1111 1111 1111;Pharmacie Hélène;;;"
        "VIREMENT, avec virgule;07/10/2026;-16,14;EUR;KREDBEBB;;+++460/8022/57132+++"
    ),
    # thousands separator + positive amount + leading zeros
    (
        "BE00 0000 0000 0001;03/09/2026;00008;064;BE00 2222 2222 2222;Employeur SA;"
        "Rue de l'Église 4;1000 Bruxelles;SALAIRE;02/09/2026;1.234,56;EUR;GEBABEBB;BE;"
        "Salaire"
    ),
    # card payment: no counterparty account, no BIC
    (
        "BE00 0000 0000 0001;12/01/2026;00001;001;;Café Œuvre;;;"
        "PAIEMENT CARTE;11/01/2026;-3,50;EUR;;;Café"
    ),
]


def write_csv(path: Path, preamble=PREAMBLE, header=HEADER, rows=ROWS) -> Path:
    content = "\r\n".join([*preamble, header, *rows])
    path.write_bytes(content.encode("cp1252"))
    return path


@pytest.fixture
def df(tmp_path):
    return load_belfius_csv(write_csv(tmp_path / "export.csv"))


# --- structure -------------------------------------------------------------


def test_columns_and_order(df):
    assert list(df.columns) == EXPECTED_COLUMNS


def test_row_count_skips_preamble(df):
    assert len(df) == len(ROWS)


def test_accepts_str_and_path(tmp_path):
    p = write_csv(tmp_path / "export.csv")
    assert load_belfius_csv(str(p)).equals(load_belfius_csv(p))


# --- encoding --------------------------------------------------------------


def test_accented_characters_decoded(df):
    assert df.loc[0, "Nom contrepartie contient"] == "Pharmacie Hélène"
    assert df.loc[1, "Rue et numéro"] == "Rue de l'Église 4"
    # cp1252-only char
    assert df.loc[2, "Nom contrepartie contient"] == "Café Œuvre"


def test_commas_in_text_do_not_split_fields(df):
    assert df.loc[0, "Transaction"] == "VIREMENT, avec virgule"


# --- amounts ---------------------------------------------------------------


def test_amount_is_float(df):
    assert pd.api.types.is_float_dtype(df["Montant"])


@pytest.mark.parametrize("row, expected", [(0, -16.14), (1, 1234.56), (2, -3.50)])
def test_amount_values(df, row, expected):
    assert df.loc[row, "Montant"] == pytest.approx(expected)


def test_total(df):
    assert df["Montant"].sum() == pytest.approx(-16.14 + 1234.56 - 3.50)


# --- dates -----------------------------------------------------------------


def test_dates_are_datetime(df):
    for col in ("Date de comptabilisation", "Date valeur"):
        assert pd.api.types.is_datetime64_any_dtype(df[col])


def test_dates_are_day_first(df):
    # 03/09/2026 must be 3 September, not 9 March
    assert df.loc[1, "Date de comptabilisation"] == pd.Timestamp(2026, 9, 3)
    assert df.loc[1, "Date valeur"] == pd.Timestamp(2026, 9, 2)


# --- text columns ----------------------------------------------------------


def test_leading_zeros_preserved(df):
    assert df.loc[1, "Numéro d'extrait"] == "00008"
    assert df.loc[1, "Numéro de transaction"] == "064"


def test_empty_fields_are_nan(df):
    assert pd.isna(df.loc[0, "Numéro d'extrait"])
    assert pd.isna(df.loc[2, "Compte contrepartie"])
    assert pd.isna(df.loc[2, "BIC"])


# --- metadata --------------------------------------------------------------


def test_metadata_balance(df):
    meta = df.attrs["metadata"]
    assert meta["Dernier solde"] == pytest.approx(1835.18)
    assert meta["Date/heure du dernier solde"] == pd.Timestamp(2026, 10, 7, 10, 17, 42)


def test_metadata_empty_filters_are_none(df):
    assert df.attrs["metadata"]["Montant à partir de"] is None


# --- edge cases & errors ---------------------------------------------------


def test_no_transactions(tmp_path):
    df = load_belfius_csv(write_csv(tmp_path / "e.csv", rows=[]))
    assert df.empty and list(df.columns) == EXPECTED_COLUMNS


def test_works_without_preamble(tmp_path):
    df = load_belfius_csv(write_csv(tmp_path / "e.csv", preamble=[]))
    assert len(df) == len(ROWS)


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_belfius_csv("does_not_exist.csv")


def test_wrong_format_raises(tmp_path):
    p = tmp_path / "other.csv"
    p.write_text("a;b;c\n1;2;3\n", encoding="cp1252")
    with pytest.raises(ValueError, match="header"):
        load_belfius_csv(p)


def test_bad_amount_raises(tmp_path):
    bad = ROWS[0].replace("-16,14", "abc")
    with pytest.raises(ValueError):
        load_belfius_csv(write_csv(tmp_path / "e.csv", rows=[bad]))


def test_load_from_bytes_gives_the_same_table(tmp_path):
    # on Azure the export arrives as bytes, not as a file on disk
    path = write_csv(tmp_path / "export.csv")
    from_bytes = load_belfius_csv(path.read_bytes())
    pd.testing.assert_frame_equal(from_bytes, load_belfius_csv(path))
    assert from_bytes.attrs["metadata"] == load_belfius_csv(path).attrs["metadata"]


# --- real export (optional) ------------------------------------------------

REAL = os.environ.get("BELFIUS_CSV")


@pytest.mark.skipif(not REAL, reason="set BELFIUS_CSV to test a real export")
def test_real_export():
    df = load_belfius_csv(REAL)
    raw_rows = Path(REAL).read_text(encoding="cp1252").splitlines()
    header_idx = next(i for i, k in enumerate(raw_rows) if k.startswith("Compte;"))
    assert len(df) == len([line for line in raw_rows[header_idx + 1 :] if line.strip()])
    assert list(df.columns) == EXPECTED_COLUMNS
    assert df["Montant"].notna().all()
    assert df["Date de comptabilisation"].notna().all()
    assert (df["Devise"] == "EUR").all()
    assert isinstance(df.attrs["metadata"]["Dernier solde"], float)
