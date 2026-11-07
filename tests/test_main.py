"""Tests for finding the exports: data/<BANK>/<account>/*.csv."""

import pandas as pd

from family_budget.__main__ import account_problem, find_exports, load_all
from family_budget.bnp import load_bnp_csv
from family_budget.fake import generate
from family_budget.normalize import normalize_bnp


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    return path


# --- find_exports -------------------------------------------------------------------


def test_finds_exports_in_every_account_folder(tmp_path):
    a = touch(tmp_path / "BE00000000000001" / "2024.csv")
    b = touch(tmp_path / "BE00000000000002" / "2024.csv")
    assert find_exports(tmp_path) == [a, b]


def test_files_directly_in_the_bank_folder_still_count(tmp_path):
    old = touch(tmp_path / "old_layout.csv")
    assert find_exports(tmp_path) == [old]


def test_ignores_other_files_and_a_missing_folder(tmp_path):
    touch(tmp_path / "BE00000000000001" / "notes.txt")
    assert find_exports(tmp_path) == []
    assert find_exports(tmp_path / "nothing here") == []


# --- account_problem: is the file in its own account's folder? -------------------


def table(account):
    return pd.DataFrame({"account": [account, account]})


def test_file_in_its_own_account_folder_is_fine(tmp_path):
    path = tmp_path / "BE00 0000 0000 0001" / "x.csv"  # spaces or not: same account
    assert account_problem(path, table("BE00000000000001")) is None


def test_underscores_and_case_do_not_matter(tmp_path):
    path = tmp_path / "be00_0000_0000_0001" / "x.csv"
    assert account_problem(path, table("BE00000000000001")) is None


def test_file_in_another_accounts_folder_is_reported(tmp_path):
    path = tmp_path / "BE00000000000002" / "x.csv"
    problem = account_problem(path, table("BE00000000000001"))
    assert "BE00000000000001" in problem and "BE00000000000002" in problem


def test_folders_not_named_after_an_account_are_not_checked(tmp_path):
    assert (
        account_problem(tmp_path / "BNP" / "x.csv", table("BE00000000000001")) is None
    )
    assert (
        account_problem(tmp_path / "joint" / "x.csv", table("BE00000000000001")) is None
    )


# --- load_all on the fake folder ----------------------------------------------------


def test_load_all_reads_every_account_once(tmp_path):
    data = generate(tmp_path / "data", start="2024-01", months=14, seed=3)
    sources = [(data / "BNP", load_bnp_csv, normalize_bnp)]
    df = load_all(sources)
    assert df["account"].nunique() == 2  # current and savings account
    assert df["row_id"].is_unique  # the yearly exports overlap by a month
