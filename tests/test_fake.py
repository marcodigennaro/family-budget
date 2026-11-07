"""Tests for the fake data folder: realistic enough to run every step on."""

import importlib

import pandas as pd
import pytest

from family_budget import config as config_module
from family_budget.__main__ import account_problem
from family_budget.belfius import load_belfius_csv
from family_budget.bnp import load_bnp_csv
from family_budget.classify import UNCATEGORISED
from family_budget.fake import MARKER, generate, main
from family_budget.ingest import detect_bank, load_config, process_file
from family_budget.normalize import normalize_belfius, normalize_bnp
from family_budget.ontology import missing_categories

MONTHS = 24


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    path = tmp_path_factory.mktemp("data")
    generate(path, start="2024-01", months=MONTHS, seed=1)
    return path


@pytest.fixture(scope="module")
def table(folder):
    """Every export through process_file, as the ingest function does on Azure."""
    config = load_config(folder)
    result = None
    for export in exports(folder):
        result, _ = process_file(export.read_bytes(), config, result)
    return result


def exports(folder):
    return sorted(folder.glob("BNP/*/*.csv")) + sorted(folder.glob("BELFIUS/*/*.csv"))


# --- the folder looks like the real one ----------------------------------------------


def test_folder_has_the_same_layout_as_the_real_one(folder):
    for name in [
        "known_expenses.csv",
        "exceptional.csv",
        "trips.csv",
        "categories.ttl",
    ]:
        assert (folder / name).is_file(), name
    assert (folder / "processed").is_dir()
    assert (folder / MARKER).is_file()  # says: generated, safe to overwrite


def test_one_folder_per_account_and_one_export_per_year(folder):
    bnp = sorted(p.name for p in (folder / "BNP").iterdir())
    belfius = sorted(p.name for p in (folder / "BELFIUS").iterdir())
    assert bnp == ["BE00000000000001", "BE00000000000009"]  # current, savings
    assert belfius == ["BE00000000000002"]
    assert len(list(folder.glob("BNP/BE00000000000001/*.csv"))) >= 2


def test_every_export_is_in_its_own_accounts_folder(folder):
    for path in exports(folder):
        load = load_bnp_csv if "BNP" in path.parts else load_belfius_csv
        normalize = normalize_bnp if "BNP" in path.parts else normalize_belfius
        assert account_problem(path, normalize(load(path))) is None, path


def test_exports_load_with_the_real_loaders(folder):
    for path in folder.glob("BNP/*/*.csv"):
        assert detect_bank(path.read_bytes()) == "bnp"
        assert len(load_bnp_csv(path)) > 0
    for path in folder.glob("BELFIUS/*/*.csv"):
        assert detect_bank(path.read_bytes()) == "belfius"
        assert len(load_belfius_csv(path)) > 0


def test_same_seed_same_files_other_seed_other_files(tmp_path):
    generate(tmp_path / "a", start="2024-01", months=3, seed=7)
    generate(tmp_path / "b", start="2024-01", months=3, seed=7)
    generate(tmp_path / "c", start="2024-01", months=3, seed=8)
    for path in (tmp_path / "a").rglob("*.csv"):
        relative = path.relative_to(tmp_path / "a")
        assert path.read_bytes() == (tmp_path / "b" / relative).read_bytes()
    a = (tmp_path / "a" / "BNP").rglob("*.csv")
    c = (tmp_path / "c" / "BNP").rglob("*.csv")
    assert [p.read_bytes() for p in a] != [p.read_bytes() for p in c]


def test_only_fake_account_numbers(folder):
    for path in folder.rglob("*.csv"):
        text = path.read_bytes().decode("utf-8-sig", errors="replace")
        for word in text.replace(";", " ").replace(",", " ").split():
            if word.startswith("BE") and word[2:].isdigit() and len(word) >= 14:
                assert word.startswith("BE00"), f"{path.name}: {word}"


# --- the pipeline has something to do -----------------------------------------------


def test_covers_the_whole_period(table):
    months = table["date"].dt.to_period("M").nunique()
    assert months == MONTHS


def test_overlapping_exports_add_no_duplicates(table):
    assert table["row_id"].is_unique


def test_refused_payments_are_dropped(folder, table):
    raw = pd.concat(load_bnp_csv(p) for p in folder.glob("BNP/*/*.csv"))
    assert (raw["Statut"] != "Accepté").any()
    assert len(table[table["bank"] == "bnp"]) < len(
        raw.drop_duplicates(["Numéro de compte", "Nº de séquence"])
    )


def test_most_but_not_all_rows_are_classified(table):
    share = (table["category"] == UNCATEGORISED).mean()
    assert 0.01 < share < 0.15  # some left for you to practise writing rules


def test_every_category_is_in_the_tree(folder, table):
    tree = load_config(folder).tree
    used = set(table["category"]) - {UNCATEGORISED}
    assert missing_categories(tree, used) == []


def test_salary_comes_in_every_month(table):
    salary = table[table["category"] == "salary"]
    assert salary["date"].dt.to_period("M").nunique() == MONTHS
    assert (salary["amount"] > 0).all()


def test_rent_paid_and_received_per_property(table):
    rent = table[table["category"] == "rent"]
    by_property = rent.groupby("property")["amount"]
    assert (by_property.max() < 0).any()  # a flat you rent
    assert (by_property.min() > 0).any()  # a flat you let


def test_trips_are_tagged(table):
    assert table["trip"].notna().sum() >= 5
    assert table["trip"].nunique() >= 2


def test_exceptional_project_wins_over_general_rules(table):
    assert (table["type"] == "exceptional").any()


def test_both_banks_and_both_owners(table):
    assert set(table["bank"]) == {"bnp", "belfius"}
    assert table["owner"].nunique() >= 2


# --- safety: never overwrite real data --------------------------------------------


def test_refuses_a_folder_with_real_files(tmp_path):
    (tmp_path / "known_expenses.csv").write_text("my real rules")
    with pytest.raises(FileExistsError):
        generate(tmp_path)
    assert (tmp_path / "known_expenses.csv").read_text() == "my real rules"


def test_regenerates_a_fake_folder(tmp_path):
    generate(tmp_path, months=2, seed=1)
    (tmp_path / "BNP" / "old.csv").write_text("left over")
    generate(tmp_path, months=2, seed=2)
    assert not (tmp_path / "BNP" / "old.csv").exists()


def test_savings_account_receives_the_monthly_transfer(table):
    savings = table[table["account"] == "BE00000000000009"]
    assert (savings["amount"] > 0).all()
    assert set(savings["category"]) <= {"own accounts", "interest"}


def test_command_line(tmp_path, capsys):
    assert main([str(tmp_path / "data"), "--months", "2"]) == 0
    assert "fake" in capsys.readouterr().out.lower()
    assert main([str(tmp_path / "data"), "--months", "2"]) == 0  # again: fine


def test_command_line_refuses_real_folder(tmp_path, capsys):
    (tmp_path / "x.csv").write_text("real")
    assert main([str(tmp_path)]) == 1
    assert "not" in capsys.readouterr().err.lower()


# --- where the pipeline looks for data ----------------------------------------------


def test_data_folder_can_be_moved_with_a_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("FAMILY_BUDGET_DATA", str(tmp_path))
    try:
        reloaded = importlib.reload(config_module)
        assert reloaded.DATA_DIR == tmp_path
        assert reloaded.BNP_DIR == tmp_path / "BNP"
        assert reloaded.BELFIUS_DIR == tmp_path / "BELFIUS"
    finally:
        monkeypatch.delenv("FAMILY_BUDGET_DATA")
        importlib.reload(config_module)


def test_data_folder_defaults_to_data_in_the_project(monkeypatch):
    monkeypatch.delenv("FAMILY_BUDGET_DATA", raising=False)
    reloaded = importlib.reload(config_module)
    assert reloaded.DATA_DIR == reloaded.BASE_DIR / "data"
