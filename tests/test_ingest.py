"""Tests for detect_bank. Headers are the banks' real formats; values are fake."""

import pandas as pd
import pytest

from family_budget.ingest import Config, detect_bank, load_config, process_file

BNP_HEADER = (
    "Nº de séquence;Date d'exécution;Date valeur;Montant;Devise du compte;"
    "Numéro de compte;Type de transaction;Contrepartie;Nom de la contrepartie;"
    "Communication;Détails;Statut;Motif du refus"
)
BNP_ROW = (
    "2025-00001;01/09/2025;01/09/2025;-12,00;EUR;BE00000000000001;"
    "Paiement par carte;;;;SHOP;Accepté;"
)

BELFIUS_PREAMBLE = [
    "Date de comptabilisation à partir de;",
    "Date de comptabilisation jusqu'au;",
    "Dernier solde;1.000,00 EUR",
    ";",
]
BELFIUS_HEADER = (
    "Compte;Date de comptabilisation;Numéro d'extrait;Numéro de transaction;"
    "Compte contrepartie;Nom contrepartie contient;Rue et numéro;Code postal et ville;"
    "Transaction;Date valeur;Montant;Devise;BIC;Code pays;Communications"
)
BELFIUS_ROW = (
    "BE00 0000 0000 0001;01/09/2025;;;;SHOP;;;PAIEMENT;01/09/2025;-12,00;EUR;;BE;"
)


def bnp_file(bom=True) -> bytes:
    text = "\n".join([BNP_HEADER, BNP_ROW]) + "\n"
    return text.encode("utf-8-sig" if bom else "utf-8")


def belfius_file(preamble=True) -> bytes:
    lines = [BELFIUS_HEADER, BELFIUS_ROW]
    if preamble:
        lines = [*BELFIUS_PREAMBLE, *lines]
    return ("\n".join(lines) + "\n").encode("cp1252")  # Belfius is Windows-1252


# --- recognised ----------------------------------------------------------------------


def test_bnp_export():
    assert detect_bank(bnp_file()) == "bnp"


def test_bnp_export_without_bom():
    assert detect_bank(bnp_file(bom=False)) == "bnp"


def test_belfius_export_with_its_lines_above_the_table():
    assert detect_bank(belfius_file()) == "belfius"


def test_belfius_export_without_those_lines():
    assert detect_bank(belfius_file(preamble=False)) == "belfius"


def test_text_works_as_well_as_bytes():
    assert detect_bank(BNP_HEADER + "\n" + BNP_ROW) == "bnp"


# --- refused, with a clear message ----------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        b"date,amount,description\n2025-09-01,-12.00,SHOP\n",  # another bank
        b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n",  # a PDF renamed .csv
        b"",  # an empty file
    ],
    ids=["other bank", "pdf", "empty"],
)
def test_unknown_file_raises(content):
    with pytest.raises(ValueError, match="not a BNP or Belfius export"):
        detect_bank(content)


def test_header_far_down_the_file_is_not_searched():
    # only the beginning counts: a header after 50 lines of noise is not a bank file
    noise = "\n".join(["x;y"] * 50)
    with pytest.raises(ValueError):
        detect_bank(noise + "\n" + BNP_HEADER)


# --- the real exports (skipped when data/ is not there) -------------------------------


def test_every_real_export_is_recognised():
    from family_budget.config import BELFIUS_DIR, BNP_DIR

    files = [(p, "bnp") for p in BNP_DIR.glob("*.csv")]
    files += [(p, "belfius") for p in BELFIUS_DIR.glob("*.csv")]
    if not files:
        pytest.skip("no real exports in data/")
    for path, bank in files:
        assert detect_bank(path.read_bytes()) == bank, path.name


# --- process_file ---------------------------------------------------------------------


def bnp_export(*rows: tuple[str, str, str, str]) -> bytes:
    """rows of (sequence, date, amount, details) -> a fake BNP export."""
    lines = [BNP_HEADER] + [
        f"{seq};{day};{day};{amount};EUR;BE00000000000001;Paiement par carte;;;;"
        f"{details};Accepté;"
        for seq, day, amount, details in rows
    ]
    return ("\n".join(lines) + "\n").encode("utf-8-sig")


def belfius_export(*rows: tuple[str, str, str, str]) -> bytes:
    """rows of (statement-transaction, date, amount, name) -> a fake Belfius export."""
    lines = [*BELFIUS_PREAMBLE, BELFIUS_HEADER]
    for ref, day, amount, name in rows:
        statement, number = ref.split("-")
        lines.append(
            f"BE00 0000 0000 0002;{day};{statement};{number};;{name};;;"
            f"VIREMENT {name};{day};{amount};EUR;;BE;"
        )
    return ("\n".join(lines) + "\n").encode("cp1252")


SHOP = ("2025-00001", "01/09/2025", "-12,00", "DELHAIZE BRUXELLES")
RENT = ("2025-00002", "02/09/2025", "-850,00", "LOYER")
CAFE = ("2025-00003", "03/09/2025", "-4,50", "CAFE ROMA")


def rule(keyword, category, **extra):
    from family_budget.rules import Rule

    empty = dict(transaction_type=None, counterparty_iban=None, amount=None)
    return Rule(
        **empty,
        keyword=keyword,
        category=category,
        type="spending",
        is_fixed=False,
        **extra,
    )


CONFIG = Config(rules=[rule("DELHAIZE", "groceries"), rule("LOYER", "rent")])


def test_first_file_builds_the_table():
    table, added = process_file(bnp_export(SHOP, RENT), CONFIG, existing=None)
    assert added == 2
    assert list(table["category"]) == ["groceries", "rent"]
    for col in ["row_id", "date", "amount", "type", "is_fixed", "property"]:
        assert col in table


def test_same_file_twice_adds_nothing():
    content = bnp_export(SHOP, RENT)
    first, _ = process_file(content, CONFIG, existing=None)
    second, added = process_file(content, CONFIG, existing=first)
    assert added == 0
    pd.testing.assert_frame_equal(second, first)


def test_overlapping_export_adds_only_new_rows():
    first, _ = process_file(bnp_export(SHOP, RENT), CONFIG, existing=None)
    table, added = process_file(bnp_export(RENT, CAFE), CONFIG, existing=first)
    assert added == 1
    assert len(table) == 3


def test_both_banks_end_up_in_one_table_sorted_by_date():
    first, _ = process_file(bnp_export(RENT), CONFIG, existing=None)
    later = belfius_export(("1-1", "01/09/2025", "-5,00", "DELHAIZE"))
    table, added = process_file(later, CONFIG, existing=first)
    assert added == 1
    assert list(table["bank"]) == ["belfius", "bnp"]  # 1 Sep before 2 Sep
    assert list(table["category"]) == ["groceries", "rent"]


def test_a_new_rule_also_reclassifies_old_rows():
    first, _ = process_file(bnp_export(CAFE), Config(rules=[]), existing=None)
    assert first.loc[0, "category"] == "Uncategorised"
    config = Config(rules=[rule("CAFE", "eating out")])
    table, _ = process_file(bnp_export(SHOP), config, existing=first)
    assert table.set_index("row_id").loc[first.loc[0, "row_id"], "category"] == (
        "eating out"
    )


def test_levels_and_trips_when_configured():
    from rdflib import Graph

    tree = Graph().parse(
        format="turtle",
        data="""
        @prefix skos: <http://www.w3.org/2004/02/skos/core#> .
        @prefix fb: <https://example.org/> .
        fb:food a skos:Concept ; skos:prefLabel "food"@en .
        fb:g a skos:Concept ; skos:prefLabel "groceries"@en ; skos:broader fb:food .
        """,
    )
    trips = pd.DataFrame(
        {
            "name": ["Home week"],
            "start": [pd.Timestamp("2025-09-01")],
            "end": [pd.Timestamp("2025-09-01")],
            "country": [""],
        }
    )
    config = Config(rules=CONFIG.rules, tree=tree, trips=trips)
    table, _ = process_file(bnp_export(SHOP), config, existing=None)
    assert table.loc[0, ["level1", "level2"]].tolist() == ["food", "groceries"]
    assert table.loc[0, "trip"] == "Home week"


def test_round_trip_through_the_csv_in_blob_storage():
    # what Azure does: table -> processed/transactions.csv -> read back next time
    from family_budget.api import load_transactions

    first, _ = process_file(bnp_export(SHOP, RENT), CONFIG, existing=None)
    stored = load_transactions(first.to_csv(index=False).encode())
    table, added = process_file(bnp_export(SHOP, CAFE), CONFIG, existing=stored)
    assert added == 1
    assert len(table) == 3


# --- load_config: the files of config/, downloaded to a folder ----------------------

RULES_HEADER = (
    "transaction_type,counterparty_iban,keyword,amount,category,type,is_fixed"
)


def test_config_with_rules_only(tmp_path):
    (tmp_path / "known_expenses.csv").write_text(
        RULES_HEADER + "\n,,DELHAIZE,,groceries,spending,false\n"
    )
    config = load_config(tmp_path)
    assert [r.category for r in config.rules] == ["groceries"]
    assert config.tree is None and config.trips is None


def test_config_with_every_file(tmp_path):
    (tmp_path / "known_expenses.csv").write_text(
        RULES_HEADER + "\n,,NOTARY,,other,spending,false\n"
    )
    (tmp_path / "exceptional.csv").write_text(
        "date," + RULES_HEADER + "\n2025-09-01,,,NOTARY,,flat,exceptional,false\n"
    )
    (tmp_path / "categories.ttl").write_text(
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n"
        '<https://example.org/food> a skos:Concept ; skos:prefLabel "food"@en .\n'
    )
    (tmp_path / "trips.csv").write_text(
        "name,start,end,country\nX,2025-01-01,2025-01-02,\n"
    )
    config = load_config(tmp_path)
    assert [r.category for r in config.rules] == ["flat", "other"]  # exceptional first
    assert config.tree is not None
    assert list(config.trips["name"]) == ["X"]


def test_config_without_rules_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path)


def test_unknown_file_is_refused_before_touching_the_table():
    with pytest.raises(ValueError, match="not a BNP or Belfius export"):
        process_file(b"date,amount\n", CONFIG, existing=None)
