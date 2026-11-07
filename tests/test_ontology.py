"""Tests for the category tree (SKOS). The tree below is fake and small."""

import pandas as pd
import pytest

from family_budget.ontology import (
    add_levels,
    category_path,
    load_tree,
    missing_categories,
)

PREFIXES = """
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix fb:   <https://example.org/category/> .
"""

TREE = """
fb:spending a skos:Concept ; skos:prefLabel "spending"@en .
fb:food a skos:Concept ; skos:prefLabel "food"@en , "cibo"@it ;
    skos:broader fb:spending .
fb:groceries a skos:Concept ; skos:prefLabel "groceries"@en ;
    skos:altLabel "supermarket"@en ; skos:broader fb:food .
fb:eating_out a skos:Concept ; skos:prefLabel "eating out"@en ;
    skos:broader fb:food .
fb:salary a skos:Concept ; skos:prefLabel "salary"@en .
"""


def write_tree(tmp_path, body=TREE):
    path = tmp_path / "categories.ttl"
    path.write_text(PREFIXES + body, encoding="utf-8")
    return path


@pytest.fixture
def tree(tmp_path):
    return load_tree(write_tree(tmp_path))


# --- category_path ---------------------------------------------------------------


def test_path_goes_from_top_to_leaf(tree):
    assert category_path(tree, "groceries") == ["spending", "food", "groceries"]


def test_path_uses_english_labels(tree):
    # "food" also has an Italian label; only the English one is used
    assert category_path(tree, "eating out") == ["spending", "food", "eating out"]


def test_a_group_can_be_a_category_itself(tree):
    assert category_path(tree, "food") == ["spending", "food"]


def test_top_concept_path_is_itself(tree):
    assert category_path(tree, "salary") == ["salary"]


def test_alt_label_finds_the_concept(tree):
    assert category_path(tree, "supermarket") == ["spending", "food", "groceries"]


def test_unknown_label_gives_empty_path(tree):
    assert category_path(tree, "taxi") == []


# --- load_tree checks --------------------------------------------------------------


def test_two_parents_raise(tmp_path):
    body = TREE + "fb:eating_out skos:broader fb:spending .\n"
    with pytest.raises(ValueError, match="eating out"):
        load_tree(write_tree(tmp_path, body))


def test_duplicate_label_raises(tmp_path):
    body = TREE + 'fb:other a skos:Concept ; skos:prefLabel "groceries"@en .\n'
    with pytest.raises(ValueError, match="groceries"):
        load_tree(write_tree(tmp_path, body))


# --- missing_categories ------------------------------------------------------------


def test_missing_categories_lists_unknown_labels_once(tree):
    labels = ["groceries", "taxi", "salary", "taxi", "flights"]
    assert missing_categories(tree, labels) == ["flights", "taxi"]


def test_no_missing_categories(tree):
    assert missing_categories(tree, ["groceries", "supermarket"]) == []


# --- add_levels --------------------------------------------------------------------


def test_add_levels(tree):
    df = pd.DataFrame(
        {
            "amount": [-61.89, -25.0, 2500.0, -14.0],
            "category": ["groceries", "eating out", "salary", "taxi"],
        }
    )
    out = add_levels(df, tree)
    assert list(out["level1"]) == ["spending", "spending", "salary", "taxi"]
    # level2 falls back to the deepest level that exists
    assert list(out["level2"]) == ["food", "food", "salary", "taxi"]
    assert list(out["category"]) == list(df["category"])  # unchanged
    assert "level1" not in df  # input not modified


def test_add_levels_keeps_index(tree):
    df = pd.DataFrame({"category": ["salary", "groceries"]}, index=[10, 20])
    assert list(add_levels(df, tree).loc[20, ["level1", "level2"]]) == [
        "spending",
        "food",
    ]


def test_totals_at_level2(tree):
    df = pd.DataFrame(
        {"amount": [-61.89, -25.0], "category": ["groceries", "eating out"]}
    )
    totals = add_levels(df, tree).groupby("level2")["amount"].sum()
    assert totals["food"] == pytest.approx(-86.89)


# --- the real files (skipped when data/ is not there) ----------------------------


def test_real_tree_covers_every_rule_category():
    from family_budget.config import CATEGORIES_PATH, EXCEPTIONAL_PATH, RULES_PATH
    from family_budget.rules import load_rules

    paths = [CATEGORIES_PATH, RULES_PATH, EXCEPTIONAL_PATH]
    if not all(p.exists() for p in paths):
        pytest.skip("real data files not available")
    rules = load_rules(EXCEPTIONAL_PATH) + load_rules(RULES_PATH)
    labels = [r.category for r in rules] + ["Uncategorised"]
    assert missing_categories(load_tree(CATEGORIES_PATH), labels) == []
