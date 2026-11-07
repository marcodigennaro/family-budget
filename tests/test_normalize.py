"""Tests for normalize. All data below is fake."""

import pandas as pd
import pytest

from family_budget.belfius import EXPECTED_COLUMNS as BELFIUS_COLUMNS
from family_budget.bnp import EXPECTED_COLUMNS as BNP_COLUMNS
from family_budget.normalize import (
    COLUMNS,
    normalize_belfius,
    normalize_bnp,
    to_transaction_type,
)

NAN = float("nan")


def make_raw(columns, rows):
    """A DataFrame shaped like a loader's output: every column present."""
    df = pd.DataFrame(rows).reindex(columns=columns)
    for col in columns:
        if col == "Montant":
            df[col] = df[col].astype(float)
        elif not col.startswith("Date"):
            df[col] = df[col].astype("str")  # loaders read text as str, NaN kept
    return df


# --- BNP ---------------------------------------------------------------------

BNP_CARD = {
    "Nº de séquence": "2025-00001",
    "Date d'exécution": pd.Timestamp(2025, 9, 23),
    "Montant": -61.89,
    "Numéro de compte": "BE00000000000001",
    "Type de transaction": "Paiement par carte",
    "Détails": "PAIEMENT AVEC LA CARTE DE DEBIT NUMERO 1111 22XX XXXX 3333 "
    "DELHAIZE ROODEBE BRUXELLES 23/09/2025 BANCONTACT",
    "Statut": "Accepté",
}
BNP_TRANSFER = {
    "Nº de séquence": "2025-00002",
    "Date d'exécution": pd.Timestamp(2025, 9, 24),
    "Montant": 293.06,
    "Numéro de compte": "BE00000000000001",
    "Type de transaction": "Virement en euros",
    "Contrepartie": "BE00 0000 0000 0002",
    "Nom de la contrepartie": "Some Employer",
    "Communication": "salary   september",
    "Détails": "virement en euros",
    "Statut": "Accepté",
}
BNP_REFUSED = {
    "Nº de séquence": "2025-00003",
    "Date d'exécution": pd.Timestamp(2025, 9, 25),
    "Montant": -42.10,
    "Numéro de compte": "BE00000000000001",
    "Type de transaction": "Domiciliation",
    "Statut": "Refusé",
}


@pytest.fixture
def bnp():
    raw = make_raw(BNP_COLUMNS, [BNP_CARD, BNP_TRANSFER, BNP_REFUSED])
    return normalize_bnp(raw)


def test_bnp_has_standard_columns(bnp):
    assert list(bnp.columns) == COLUMNS


def test_bnp_drops_rows_not_accepted(bnp):
    assert len(bnp) == 2
    assert "BE00000000000001-2025-00003" not in set(bnp["row_id"])


def test_bnp_row_id(bnp):
    assert bnp.loc[0, "row_id"] == "BE00000000000001-2025-00001"
    assert bnp["row_id"].is_unique


def test_bnp_card_counterparty_is_merchant(bnp):
    assert bnp.loc[0, "counterparty"] == "DELHAIZE ROODEBE BRUXELLES"


def test_bnp_transfer_keeps_counterparty_name(bnp):
    assert bnp.loc[1, "counterparty"] == "Some Employer"


def test_bnp_iban_has_no_spaces(bnp):
    assert bnp.loc[1, "counterparty_iban"] == "BE00000000000002"


def test_bnp_fixed_values(bnp):
    assert (bnp["bank"] == "bnp").all()
    assert (bnp["owner"] == "Marco").all()


def test_bnp_owner_can_be_set():
    raw = make_raw(BNP_COLUMNS, [BNP_CARD])
    assert normalize_bnp(raw, owner="Acacia").loc[0, "owner"] == "Acacia"


def test_bnp_transaction_type_in_english(bnp):
    assert list(bnp["transaction_type"]) == ["card", "transfer"]


def test_bnp_text_is_upper_case_with_single_spaces(bnp):
    assert bnp.loc[1, "text"] == "SALARY SEPTEMBER VIREMENT EN EUROS"


def test_bnp_amount_and_date_unchanged(bnp):
    assert bnp.loc[0, "amount"] == pytest.approx(-61.89)
    assert bnp.loc[0, "date"] == pd.Timestamp(2025, 9, 23)


def test_bnp_drops_rows_not_booked_yet():
    unbooked = {**BNP_CARD, "Nº de séquence": "2024-"}
    out = normalize_bnp(make_raw(BNP_COLUMNS, [BNP_CARD, unbooked]))
    assert list(out["row_id"]) == ["BE00000000000001-2025-00001"]


def test_bnp_empty_input():
    out = normalize_bnp(make_raw(BNP_COLUMNS, []))
    assert out.empty and list(out.columns) == COLUMNS


# --- Belfius -----------------------------------------------------------------

BELFIUS_CARD = {
    "Compte": "BE00 0000 0000 0009",
    "Date de comptabilisation": pd.Timestamp(2026, 10, 4),
    "Numéro d'extrait": "00010",
    "Numéro de transaction": "001",
    "Nom contrepartie contient": "DELHAIZE ROODEBEEK",
    "Transaction": "BANCONTACT - ACHAT - DELHAIZE ROODEBEEK - 1200   BRUXELLES "
    "- 04/10/26 17:50 - CARTE 1111 22XX XXXX 4444 - Smith  Acacia   "
    "REF. : 0000000000001 VAL. 04-10",
    "Montant": -25.40,
}
BELFIUS_TRANSFER = {
    "Compte": "BE00 0000 0000 0009",
    "Date de comptabilisation": pd.Timestamp(2026, 10, 5),
    "Numéro d'extrait": "00010",
    "Numéro de transaction": "002",
    "Compte contrepartie": "BE00 0000 0000 0001",
    "Nom contrepartie contient": "Di Gennaro Marco",
    "Transaction": "VIREMENT INSTANTANE EN EUROS",
    "Montant": 500.00,
    "Communications": "monthly share",
}
BELFIUS_PENDING = {
    "Compte": "BE00 0000 0000 0009",
    "Date de comptabilisation": pd.Timestamp(2026, 10, 7),
    "Nom contrepartie contient": "Pharmacie",
    "Transaction": "BANCONTACT - ACHAT - Pharmacie",
    "Montant": -16.14,
}


@pytest.fixture
def belfius():
    raw = make_raw(BELFIUS_COLUMNS, [BELFIUS_CARD, BELFIUS_TRANSFER, BELFIUS_PENDING])
    return normalize_belfius(raw)


def test_belfius_has_standard_columns(belfius):
    assert list(belfius.columns) == COLUMNS


def test_belfius_drops_pending_rows(belfius):
    assert len(belfius) == 2
    assert "Pharmacie" not in set(belfius["counterparty"])


def test_belfius_row_id(belfius):
    assert belfius.loc[0, "row_id"] == "BE00000000000009-00010-001"
    assert belfius["row_id"].is_unique


def test_belfius_ibans_have_no_spaces(belfius):
    assert belfius.loc[0, "account"] == "BE00000000000009"
    assert belfius.loc[1, "counterparty_iban"] == "BE00000000000001"


def test_belfius_owner_from_card(belfius):
    assert belfius.loc[0, "owner"] == "Smith Acacia"


def test_belfius_owner_empty_without_card(belfius):
    assert pd.isna(belfius.loc[1, "owner"])


def test_belfius_counterparty_and_bank(belfius):
    assert belfius.loc[0, "counterparty"] == "DELHAIZE ROODEBEEK"
    assert (belfius["bank"] == "belfius").all()


def test_belfius_transaction_type_in_english(belfius):
    assert list(belfius["transaction_type"]) == ["card", "transfer"]


def test_belfius_empty_input():
    out = normalize_belfius(make_raw(BELFIUS_COLUMNS, []))
    assert out.empty and list(out.columns) == COLUMNS


# --- both banks together -----------------------------------------------------


def test_banks_stack_into_one_table(bnp, belfius):
    combined = pd.concat([bnp, belfius], ignore_index=True)
    assert list(combined.columns) == COLUMNS
    assert len(combined) == 4
    assert combined["row_id"].is_unique


# --- transaction types -------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Paiement par carte", "card"),
        ("Paiement par carte de crédit", "credit_card"),
        ("Retrait d'espèces par carte", "cash"),
        ("Domiciliation", "direct_debit"),
        ("VOTRE DOMICILIATION EUROPEENNE", "direct_debit"),
        ("Virement instantané en euros", "transfer"),
        ("VERSEMENT DU COMPTE", "transfer"),
        ("ACHAT VIA WERO", "transfer"),
        ("Frais liés au compte", "fee"),
        ("SUPPLEMENT POUR VOTRE COMPTE", "fee"),
        ("Remboursements Crédits Hypothécaires", "loan"),
        ("PAIEMENT DEBITMASTERCARD 03/10", "card"),
        ("BANCONTACT - ACHAT", "card"),
        ("Something new", "other"),
        (NAN, "other"),
    ],
)
def test_to_transaction_type(text, expected):
    assert to_transaction_type(text) == expected
