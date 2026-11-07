"""Tests for load_bnp_csv. Run with:  pytest test_bnp_csv.py -v

To also check a real export, set:  BNP_CSV=/path/to/export.csv
"""

import os
from pathlib import Path

import pandas as pd
import pytest

from family_budget.bnp import (
    EXPECTED_COLUMNS,
    extract_merchant,
    load_bnp_csv,
    parse_amount,
)

HEADER = ";".join(EXPECTED_COLUMNS)

ROWS = [
    # commas in Détails: the line that broke the default pd.read_csv
    (
        "2025-00719;03/09/2025;03/09/2025;313,41;EUR;BE00000000000001;"
        "Virement en euros;IE00BANK00000000000000;AIRBNB PAYMENTS LUXEMBOURG S.A.;"
        "Airbnb;VIREMENT DU COMPTE IE00 AIRBNB 4, RUE HENRI SCHNADT, LUXEMBOURG;"
        "Accepté;"
    ),
    # negative amount with thousands separator
    (
        "2025-00720;15/01/2025;14/01/2025;-1.250,00;EUR;BE00000000000001;"
        "Paiement par carte;;Café Élysée;;PAIEMENT CARTE;Accepté;"
    ),
    # refused transaction with a reason
    (
        "2025-00721;28/02/2025;28/02/2025;-42,10;EUR;BE00000000000001;Domiciliation;"
        "BE00000000000002;Proximus;;DOMICILIATION;Refusé;Provision insuffisante"
    ),
]


def write_csv(path: Path, header=HEADER, rows=ROWS, bom=True) -> Path:
    content = "\n".join([header, *rows]) + "\n"
    path.write_bytes(content.encode("utf-8-sig" if bom else "utf-8"))
    return path


@pytest.fixture
def df(tmp_path):
    return load_bnp_csv(write_csv(tmp_path / "bnp.csv"))


# --- structure -------------------------------------------------------------


def test_columns_and_order(df):
    assert list(df.columns) == EXPECTED_COLUMNS


def test_row_count(df):
    assert len(df) == len(ROWS)


def test_bom_removed_from_first_column(df):
    assert df.columns[0] == "Nº de séquence"
    assert not df.columns[0].startswith("﻿")


def test_works_without_bom(tmp_path):
    df = load_bnp_csv(write_csv(tmp_path / "bnp.csv", bom=False))
    assert df.columns[0] == "Nº de séquence"


# --- the original bug ------------------------------------------------------


def test_commas_in_details_do_not_split_fields(df):
    assert "4, RUE HENRI SCHNADT, LUXEMBOURG" in df.loc[0, "Détails"]
    assert df.loc[0, "Statut"] == "Accepté"


# --- amounts ---------------------------------------------------------------


def test_amount_is_float(df):
    assert pd.api.types.is_float_dtype(df["Montant"])


@pytest.mark.parametrize("row, expected", [(0, 313.41), (1, -1250.00), (2, -42.10)])
def test_amount_values(df, row, expected):
    assert df.loc[row, "Montant"] == pytest.approx(expected)


# --- dates -----------------------------------------------------------------


def test_dates_are_datetime(df):
    for col in ("Date d'exécution", "Date valeur"):
        assert pd.api.types.is_datetime64_any_dtype(df[col])


def test_dates_are_day_first(df):
    # 03/09/2025 must be 3 September, not 9 March
    assert df.loc[0, "Date d'exécution"] == pd.Timestamp(2025, 9, 3)
    assert df.loc[1, "Date valeur"] == pd.Timestamp(2025, 1, 14)


# --- text columns ----------------------------------------------------------


def test_accents_decoded(df):
    print(df)
    assert df.loc[1, "Nom de la contrepartie"] == "Café Élysée"
    assert df.loc[2, "Statut"] == "Refusé"


def test_sequence_number_kept_as_text(df):
    assert df.loc[0, "Nº de séquence"] == "2025-00719"


def test_empty_fields_are_nan(df):
    assert pd.isna(df.loc[0, "Motif du refus"])
    assert pd.isna(df.loc[1, "Contrepartie"])
    assert pd.isna(df.loc[1, "Communication"])


def test_refusal_reason(df):
    assert df.loc[2, "Motif du refus"] == "Provision insuffisante"


# --- edge cases & errors ---------------------------------------------------


def test_no_transactions(tmp_path):
    df = load_bnp_csv(write_csv(tmp_path / "bnp.csv", rows=[]))
    assert df.empty and list(df.columns) == EXPECTED_COLUMNS


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_bnp_csv("does_not_exist.csv")


def test_wrong_format_raises(tmp_path):
    p = tmp_path / "other.csv"
    p.write_text("a;b;c\n1;2;3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing columns"):
        load_bnp_csv(p)


def test_bad_amount_raises(tmp_path):
    bad = ROWS[0].replace("313,41", "abc")
    with pytest.raises(ValueError):
        load_bnp_csv(write_csv(tmp_path / "bnp.csv", rows=[bad]))


# --- real export (optional) ------------------------------------------------


REAL = os.environ.get("BNP_CSV")


@pytest.mark.skipif(not REAL, reason="set BNP_CSV to test a real export")
def test_real_export():
    df = load_bnp_csv(REAL)
    lines = Path(REAL).read_text(encoding="utf-8-sig").splitlines()
    assert len(df) == len([line for line in lines[1:] if line.strip()])
    assert list(df.columns) == EXPECTED_COLUMNS
    assert df["Montant"].notna().all()
    assert df["Date d'exécution"].notna().all()


CARD = (
    "PAIEMENT AVEC LA CARTE DE DEBIT NUMERO 4871 04XX XXXX 1653 "
    "DELHAIZE ROODEBE BRUXELLES 23/09/2025 BANCONTACT"
)


def test_card_payment_returns_merchant():
    assert extract_merchant(CARD) == "DELHAIZE ROODEBE BRUXELLES"


def test_non_card_returns_none():
    assert extract_merchant("REMBOURSEMENT CREDIT HABITATION 012-7586510-82") is None


@pytest.mark.parametrize("empty", [None, float("nan"), ""])
def test_missing_text_returns_none(empty):
    assert extract_merchant(empty) is None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("-877,95", -877.95),  # exports from 2025 on
        ("-877.95", -877.95),  # older yearly exports
        ("2723.84", 2723.84),
        ("-1.250,00", -1250.00),
        ("-19.99", -19.99),
    ],
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == pytest.approx(expected)


def test_old_export_with_dot_decimals(tmp_path):
    row = ROWS[0].replace("313,41", "313.41")
    df = load_bnp_csv(write_csv(tmp_path / "bnp.csv", rows=[row]))
    assert df.loc[0, "Montant"] == pytest.approx(313.41)


def test_load_from_bytes_gives_the_same_table(tmp_path):
    # on Azure the export arrives as bytes, not as a file on disk
    path = write_csv(tmp_path / "bnp.csv")
    pd.testing.assert_frame_equal(load_bnp_csv(path.read_bytes()), load_bnp_csv(path))
