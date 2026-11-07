"""Tests for load_rules and matches. All data below is fake."""

import pandas as pd
import pytest

from family_budget.rules import Rule, load_rules, matches

HEADER = "transaction_type,counterparty_iban,keyword,amount,category,type,is_fixed"


def write_rules(tmp_path, *lines, header=HEADER):
    path = tmp_path / "known_expenses.csv"
    path.write_text("\n".join([header, *lines]) + "\n", encoding="utf-8")
    return path


def rule(**fields):
    defaults = dict(
        transaction_type=None,
        counterparty_iban=None,
        keyword=None,
        amount=None,
        category="test",
        type="spending",
        is_fixed=False,
    )
    return Rule(**{**defaults, **fields})


ROW = {
    "transaction_type": "direct_debit",
    "counterparty_iban": "BE00000000000002",
    "amount": -85.0,
    "counterparty": "Allianz Benelux NV",
    "text": "PRIME PLAN FOR LIFE ( BCUD00000001 )",
}


# --- load_rules ----------------------------------------------------------------


def test_load_keeps_file_order(tmp_path):
    path = write_rules(
        tmp_path,
        "direct_debit,,PARTENA,,health insurance,spending,true",
        ",,PARTENA,,health,spending,false",
    )
    assert [r.category for r in load_rules(path)] == ["health insurance", "health"]


def test_load_empty_cells_become_none(tmp_path):
    (r,) = load_rules(write_rules(tmp_path, ",,DELHAIZE,,groceries,spending,false"))
    assert r.transaction_type is None
    assert r.counterparty_iban is None
    assert r.amount is None


def test_load_converts_types(tmp_path):
    line = "direct_debit,be00 0000 0000 0002,paypal,-38.89,subscription,Spending,TRUE"
    (r,) = load_rules(write_rules(tmp_path, line))
    assert r.amount == pytest.approx(-38.89)
    assert r.is_fixed is True
    assert r.keyword == "PAYPAL"
    assert r.counterparty_iban == "BE00000000000002"
    assert r.type == "spending"


@pytest.mark.parametrize("text, expected", [("yes", True), ("0", False)])
def test_load_accepts_yes_no_and_digits(tmp_path, text, expected):
    (r,) = load_rules(write_rules(tmp_path, f",,X,,c,spending,{text}"))
    assert r.is_fixed is expected


def test_load_missing_column_raises(tmp_path):
    path = write_rules(tmp_path, "X,c", header="keyword,category")
    with pytest.raises(ValueError, match="missing columns"):
        load_rules(path)


def test_load_bad_type_raises(tmp_path):
    with pytest.raises(ValueError, match="Line 2: type"):
        load_rules(write_rules(tmp_path, ",,X,,c,shopping,false"))


def test_load_bad_is_fixed_raises(tmp_path):
    with pytest.raises(ValueError, match="Line 2: is_fixed"):
        load_rules(write_rules(tmp_path, ",,X,,c,spending,maybe"))


def test_load_empty_category_raises(tmp_path):
    with pytest.raises(ValueError, match="category"):
        load_rules(write_rules(tmp_path, ",,X,,,spending,false"))


def test_load_rule_without_match_field_raises(tmp_path):
    with pytest.raises(ValueError, match="match all"):
        load_rules(write_rules(tmp_path, ",,,,c,spending,false"))


# --- matches -------------------------------------------------------------------


def test_keyword_only_matches_regardless_of_other_fields():
    assert matches(ROW, rule(keyword="ALLIANZ"))


def test_keyword_found_in_text_too():
    assert matches(ROW, rule(keyword="BCUD00000001"))


def test_keyword_is_case_insensitive():
    row = {**ROW, "counterparty": "allianz benelux nv"}
    assert matches(row, rule(keyword="ALLIANZ"))


def test_keyword_missing_does_not_match():
    assert not matches(ROW, rule(keyword="DELHAIZE"))


def test_transaction_type_must_be_exact():
    assert matches(ROW, rule(transaction_type="direct_debit"))
    assert not matches(ROW, rule(transaction_type="card"))


def test_iban_must_be_exact():
    assert matches(ROW, rule(counterparty_iban="BE00000000000002"))
    assert not matches(ROW, rule(counterparty_iban="BE00000000000003"))


def test_amount_within_one_cent():
    assert matches(ROW, rule(amount=-85.004))
    assert not matches(ROW, rule(amount=-204.16))


def test_all_filled_fields_must_match():
    assert not matches(ROW, rule(keyword="ALLIANZ", amount=-204.16))


def test_missing_values_in_row_do_not_crash():
    row = {**ROW, "counterparty": float("nan"), "counterparty_iban": float("nan")}
    assert matches(row, rule(keyword="PLAN FOR LIFE"))
    assert not matches(row, rule(counterparty_iban="BE00000000000002"))


# --- date (exceptional.csv) ------------------------------------------------------


def test_load_without_date_column_gives_none(tmp_path):
    (r,) = load_rules(write_rules(tmp_path, ",,X,,c,spending,false"))
    assert r.date is None


def test_load_date_and_ignores_extra_columns(tmp_path):
    header = f"date,{HEADER},note"
    line = "2019-06-27,,,PALSTERMAN,-180072.14,flat,exceptional,false,saldo notaio"
    (r,) = load_rules(write_rules(tmp_path, line, header=header))
    assert r.date == pd.Timestamp(2019, 6, 27)
    assert r.type == "exceptional"


def test_date_must_be_exact():
    row = {**ROW, "date": pd.Timestamp(2019, 6, 27)}
    assert matches(row, rule(keyword="ALLIANZ", date=pd.Timestamp(2019, 6, 27)))
    assert not matches(row, rule(keyword="ALLIANZ", date=pd.Timestamp(2019, 6, 28)))


# --- direction (money in / out) ----------------------------------------------------


def test_load_direction(tmp_path):
    header = f"{HEADER},direction"
    (r,) = load_rules(write_rules(tmp_path, ",,X,,c,income,false,IN", header=header))
    assert r.direction == "in"


def test_load_bad_direction_raises(tmp_path):
    header = f"{HEADER},direction"
    with pytest.raises(ValueError, match="direction"):
        load_rules(write_rules(tmp_path, ",,X,,c,income,false,up", header=header))


def test_direction_alone_is_not_a_match_field(tmp_path):
    header = f"{HEADER},direction"
    with pytest.raises(ValueError, match="match all"):
        load_rules(write_rules(tmp_path, ",,,,c,income,false,in", header=header))


def test_direction_in_and_out():
    money_out = ROW  # amount -85.0
    money_in = {**ROW, "amount": 497.0}
    assert matches(money_in, rule(keyword="ALLIANZ", direction="in"))
    assert not matches(money_out, rule(keyword="ALLIANZ", direction="in"))
    assert matches(money_out, rule(keyword="ALLIANZ", direction="out"))
    assert not matches(money_in, rule(keyword="ALLIANZ", direction="out"))


# --- comments and blank lines ----------------------------------------------------


def test_comment_and_blank_lines_are_skipped(tmp_path):
    path = write_rules(
        tmp_path,
        "# === Salaries ===",
        ",,EMPLOYER,,salary,income,false",
        "",
        "# === Groceries ===",
        ",,DELHAIZE,,groceries,spending,false",
    )
    assert [r.category for r in load_rules(path)] == ["salary", "groceries"]


def test_error_line_numbers_count_comments(tmp_path):
    path = write_rules(tmp_path, "# comment", "", ",,X,,c,shopping,false")
    with pytest.raises(ValueError, match="Line 4: type"):
        load_rules(path)


# --- property (optional result column) ----------------------------------------------


def test_load_without_property_column_gives_none(tmp_path):
    (r,) = load_rules(write_rules(tmp_path, ",,X,,c,spending,false"))
    assert r.property is None


def test_load_property(tmp_path):
    header = f"{HEADER},direction,property"
    line = ",,LACROIX,,rent,spending,true,out, Avroy 206 "
    (r,) = load_rules(write_rules(tmp_path, line, header=header))
    assert r.property == "Avroy 206"
    assert r.direction == "out"


def test_empty_property_cell_gives_none(tmp_path):
    header = f"{HEADER},direction,property"
    (r,) = load_rules(write_rules(tmp_path, ",,X,,c,spending,false,,", header=header))
    assert r.property is None
