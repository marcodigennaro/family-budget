"""Tests for the places tree, built from Unicode CLDR data.

The containment below is a small fake extract of CLDR's territoryContainment;
the names come from the real CLDR data shipped with babel.
"""

import pytest
from rdflib import RDF, Graph, Literal
from rdflib.compare import isomorphic

from family_budget.places import (
    SCHEMA,
    build_places,
    load_places,
    names_within,
    place_path,
    write_places,
)

CONTAINMENT = {
    "001": {"_contains": ["150", "019"]},  # world
    "150": {"_contains": ["039"]},  # Europe
    "039": {"_contains": ["IT", "PT"]},  # Southern Europe
    "019": {"_contains": ["005"]},  # Americas
    "005": {"_contains": ["AR", "BR"]},  # South America
    # a grouping overlaps the tree: it must be ignored
    "EU": {"_contains": ["IT", "PT"], "_grouping": "true"},
    # CLDR also lists old versions of some regions
    "039-status-deprecated": {"_contains": ["CS"]},
}


@pytest.fixture
def places():
    return build_places(CONTAINMENT, languages=("en", "fr", "nl"))


def node(graph, code):
    return graph.value(predicate=SCHEMA.identifier, object=Literal(code))


def names(graph, code, lang):
    found = graph.objects(node(graph, code), SCHEMA.name)
    return {str(n) for n in found if n.language == lang}


# --- build_places ----------------------------------------------------------------


def test_country_has_a_name_in_each_language(places):
    assert names(places, "IT", "en") == {"Italy"}
    assert names(places, "IT", "fr") == {"Italie"}
    assert names(places, "IT", "nl") == {"Italië"}


def test_types_follow_schema_org(places):
    def type_of(code):
        return places.value(node(places, code), RDF.type)

    assert type_of("IT") == SCHEMA.Country
    assert type_of("150") == SCHEMA.Continent  # directly inside the world
    assert type_of("039") == SCHEMA.Place  # a region inside a continent


def test_path_goes_from_world_to_country(places):
    assert place_path(places, "IT") == ["001", "150", "039", "IT"]


def test_groupings_and_deprecated_entries_are_ignored(places):
    assert place_path(places, "PT") == ["001", "150", "039", "PT"]  # not via EU
    assert place_path(places, "EU") == []
    assert place_path(places, "CS") == []


def test_unknown_code_has_empty_path(places):
    assert place_path(places, "XX") == []


def test_codes_without_cldr_name_are_skipped():
    containment = {"001": {"_contains": ["150"]}, "150": {"_contains": ["IT", "Q9"]}}
    places = build_places(containment, languages=("en",))
    assert place_path(places, "IT") == ["001", "150", "IT"]
    assert place_path(places, "Q9") == []


# --- names_within ----------------------------------------------------------------


def test_names_within_a_region_include_its_countries(places):
    found = names_within(places, "005")
    assert {"South America", "Amérique du Sud", "Argentina", "Brésil"} <= found
    assert {"AR", "BR"} <= found  # ISO codes, as Belfius writes them
    assert "Italy" not in found


def test_names_within_a_country(places):
    assert names_within(places, "IT") == {"IT", "Italy", "Italie", "Italië"}


def test_numeric_region_codes_are_not_names(places):
    # "005" would match amounts and references in the bank text
    assert "005" not in names_within(places, "005")


def test_names_within_unknown_code_raises(places):
    with pytest.raises(KeyError, match="XX"):
        names_within(places, "XX")


# --- write and load ----------------------------------------------------------------


def test_turtle_round_trip(places, tmp_path):
    path = tmp_path / "places.ttl"
    write_places(places, path)
    assert isomorphic(load_places(path), places)


def test_build_is_deterministic():
    a = build_places(CONTAINMENT, languages=("en",))
    b = build_places(CONTAINMENT, languages=("en",))
    assert isomorphic(a, b)


# --- the generated file in the repository ------------------------------------------


def test_generated_file():
    from family_budget.config import PLACES_PATH

    if not PLACES_PATH.exists():
        pytest.skip("run: python -m family_budget.places")
    places = load_places(PLACES_PATH)
    assert place_path(places, "IT") == ["001", "150", "039", "IT"]
    assert place_path(places, "NZ")[:2] == ["001", "009"]  # Oceania
    countries = set(places.subjects(RDF.type, SCHEMA.Country))
    assert len(countries) > 200
    assert isinstance(places, Graph)
