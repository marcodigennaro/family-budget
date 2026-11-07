"""Learning tests for EPSO Field 4, area 3: data architecture and semantic technologies.

One test per topic of the syllabus: URIs, RDF, SKOS taxonomies, SPARQL,
controlled vocabularies, semantic interoperability, RDFS, DCAT, linked data.
Everything uses this project's own ideas (categories, places, the processed
transactions file) with fake data. Run:  pytest tests/test_d3_semantic.py -v
"""

from rdflib import RDF, RDFS, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCAT, DCTERMS, SKOS, XSD

FB = Namespace("https://budget.mdigennaro.net/category/")
PLACE = Namespace("https://budget.mdigennaro.net/place/")
SCHEMA = Namespace("https://schema.org/")
# EU Vocabularies (Publications Office): the EU's controlled vocabulary of countries
EU_COUNTRY = Namespace("http://publications.europa.eu/resource/authority/country/")

TURTLE = """
@prefix skos:   <http://www.w3.org/2004/02/skos/core#> .
@prefix rdfs:   <http://www.w3.org/2000/01/rdf-schema#> .
@prefix schema: <https://schema.org/> .
@prefix fb:     <https://budget.mdigennaro.net/category/> .
@prefix place:  <https://budget.mdigennaro.net/place/> .
@prefix eu:     <http://publications.europa.eu/resource/authority/country/> .

# --- a taxonomy (SKOS) -----------------------------------------------------------
fb:food       a skos:Concept ; skos:prefLabel "food"@en , "alimentation"@fr .
fb:groceries  a skos:Concept ; skos:prefLabel "groceries"@en ;
              skos:altLabel "supermarket"@en ; skos:broader fb:food .
fb:eating_out a skos:Concept ; skos:prefLabel "eating out"@en ;
              skos:broader fb:food .

# --- places, linked to the EU controlled vocabulary --------------------------------
place:IT a schema:Country ; schema:name "Italy"@en ;
         schema:containedInPlace place:039 ;
         skos:exactMatch eu:ITA .                 # same thing, other vocabulary
place:039 a schema:Place ; schema:name "Southern Europe"@en .

# --- a tiny ontology (RDFS): classes and their hierarchy ----------------------------
schema:Country rdfs:subClassOf schema:Place .
"""


def graph() -> Graph:
    return Graph().parse(data=TURTLE, format="turtle")


# 1. URIs ------------------------------------------------------------------------------
def test_uri_names_a_thing_globally():
    # A URI is a global identifier; a prefix is only a local abbreviation.
    assert FB.groceries == URIRef("https://budget.mdigennaro.net/category/groceries")


# 2. RDF: everything is a (subject, predicate, object) triple -------------------------
def test_rdf_is_a_set_of_triples():
    g = graph()
    assert (FB.groceries, SKOS.broader, FB.food) in g
    # objects can be URIs (links) or literals (values, with language or datatype)
    assert Literal("groceries", lang="en") in set(
        g.objects(FB.groceries, SKOS.prefLabel)
    )
    # 3 + 4 + 3 triples for the concepts, 4 + 2 for the places, 1 for the class
    assert len(g) == 17


# 3. SKOS taxonomy: prefLabel, altLabel, broader --------------------------------------
def test_skos_altlabel_is_a_synonym_of_the_same_concept():
    g = graph()
    concept = g.value(predicate=SKOS.altLabel, object=Literal("supermarket", lang="en"))
    assert concept == FB.groceries


def test_skos_multilingual_labels():
    labels = {
        lbl.language: str(lbl) for lbl in graph().objects(FB.food, SKOS.prefLabel)
    }
    assert labels == {"en": "food", "fr": "alimentation"}


# 4. SPARQL: pattern matching over triples ---------------------------------------------
def test_sparql_select_with_property_path():
    # broader+ = one or more steps up the tree (a "property path")
    query = """
        SELECT ?label WHERE {
            ?c skos:broader+ fb:food ;
               skos:prefLabel ?label .
        } ORDER BY ?label"""
    rows = graph().query(query, initNs={"skos": SKOS, "fb": FB})
    assert [str(r.label) for r in rows] == ["eating out", "groceries"]


def test_sparql_ask_and_count():
    g = graph()
    assert g.query("ASK { ?c a skos:Concept }", initNs={"skos": SKOS}).askAnswer
    (count,) = next(
        iter(
            g.query(
                "SELECT (COUNT(?c) AS ?n) WHERE { ?c a skos:Concept }",
                initNs={"skos": SKOS},
            )
        )
    )
    assert int(count) == 3


def test_sparql_construct_builds_a_new_graph():
    # CONSTRUCT returns triples, not rows: here a flat "narrower" view of the tree
    query = """
        CONSTRUCT { ?parent skos:narrower ?child }
        WHERE { ?child skos:broader ?parent }"""
    result = graph().query(query, initNs={"skos": SKOS}).graph
    assert set(result.objects(FB.food, SKOS.narrower)) == {FB.groceries, FB.eating_out}


# 5. Controlled vocabulary + semantic interoperability --------------------------------
def test_exact_match_links_own_data_to_an_eu_vocabulary():
    # Our 'IT' and the EU's 'ITA' are different URIs for the same country.
    # skos:exactMatch lets any system that knows the EU table understand ours.
    query = """
        SELECT ?name WHERE {
            ?place skos:exactMatch eu:ITA ; schema:name ?name .
        }"""
    rows = graph().query(
        query, initNs={"skos": SKOS, "schema": SCHEMA, "eu": EU_COUNTRY}
    )
    assert [str(r.name) for r in rows] == ["Italy"]


# 6. RDFS ontology: classes and subclasses -------------------------------------------
def test_rdfs_subclass_lets_a_query_find_more_than_was_said():
    # place:IT is only stated to be a Country, but Country is a subclass of Place.
    # rdf:type/rdfs:subClassOf* follows the class hierarchy at query time
    # (a reasoner such as owlrl would add these triples permanently).
    query = """
        SELECT ?name WHERE {
            ?p rdf:type/rdfs:subClassOf* schema:Place ; schema:name ?name .
        } ORDER BY ?name"""
    rows = graph().query(query, initNs={"rdf": RDF, "rdfs": RDFS, "schema": SCHEMA})
    assert [str(r.name) for r in rows] == ["Italy", "Southern Europe"]


# 7. DCAT: metadata about datasets (the basis of data.europa.eu, via DCAT-AP) ----------
def describe_output() -> Graph:
    """Metadata for the file written by `python -m family_budget`."""
    g = Graph()
    ds = URIRef("https://budget.mdigennaro.net/dataset/transactions")
    dist = URIRef("https://budget.mdigennaro.net/dataset/transactions/csv")
    g.add((ds, RDF.type, DCAT.Dataset))
    g.add((ds, DCTERMS.title, Literal("Categorised bank transactions", lang="en")))
    g.add((ds, DCAT.theme, FB.food))  # themes can point to our own taxonomy
    g.add((ds, DCTERMS.spatial, EU_COUNTRY.BEL))
    g.add((ds, DCAT.distribution, dist))
    g.add((dist, RDF.type, DCAT.Distribution))
    g.add(
        (
            dist,
            DCAT.mediaType,
            URIRef("https://www.iana.org/assignments/media-types/text/csv"),
        )
    )
    g.add((dist, DCAT.downloadURL, URIRef("file:data/processed/transactions.csv")))
    g.add((dist, DCTERMS.issued, Literal("2026-10-16", datatype=XSD.date)))
    return g


def test_dcat_dataset_has_a_csv_distribution():
    query = """
        SELECT ?title ?url WHERE {
            ?ds a dcat:Dataset ; dct:title ?title ; dcat:distribution ?d .
            ?d dcat:mediaType ?type ; dcat:downloadURL ?url .
            FILTER(STRENDS(STR(?type), "text/csv"))
        }"""
    rows = list(describe_output().query(query, initNs={"dcat": DCAT, "dct": DCTERMS}))
    assert len(rows) == 1
    assert str(rows[0].title) == "Categorised bank transactions"


# 8. Linked data: graphs from different sources merge on shared URIs -------------------
def test_linked_data_merges_on_shared_uris():
    merged = graph() + describe_output()  # set union of the triples
    # one query now crosses both: datasets whose theme is under 'food'
    query = """
        SELECT ?label WHERE {
            ?ds a dcat:Dataset ; dcat:theme ?t .
            ?t skos:prefLabel ?label . FILTER(lang(?label) = "en")
        }"""
    rows = merged.query(query, initNs={"dcat": DCAT, "skos": SKOS})
    assert [str(r.label) for r in rows] == ["food"]


# 9. Serialisation: the same graph in different syntaxes -------------------------------
def test_turtle_and_json_ld_hold_the_same_triples():
    g = graph()
    from rdflib.compare import isomorphic

    again = Graph().parse(data=g.serialize(format="json-ld"), format="json-ld")
    assert isomorphic(g, again)
