"""SPARQL property paths on this project's own ontologies.

Run from the project folder:  python examples/sparql_paths.py

A property path follows links of any length in one pattern:
    +   one or more steps        *   zero or more steps (includes the start)
    ^   walk a link backwards    /   a chain of links
    |   either link
"""

from rdflib import Graph

places = Graph().parse("ontologies/places.ttl")
categories = Graph().parse("data/categories.ttl")  # private, on your Mac only

PREFIXES = """
PREFIX schema: <https://schema.org/>
PREFIX place:  <https://budget.mdigennaro.net/place/>
PREFIX skos:   <http://www.w3.org/2004/02/skos/core#>
PREFIX fb:     <https://budget.mdigennaro.net/category/>
"""

QUERIES = {
    "+  countries anywhere in Europe": (
        places,
        """SELECT (COUNT(?c) AS ?n) WHERE {
            ?c a schema:Country ; schema:containedInPlace+ place:150 }""",
    ),
    "*  Italy and everything above it": (
        places,
        """SELECT ?name WHERE {
            place:IT schema:containedInPlace* ?p .
            ?p schema:name ?name FILTER(lang(?name) = "en") }""",
    ),
    "^  regions directly inside Europe": (
        places,
        """SELECT ?name WHERE {
            place:150 ^schema:containedInPlace ?r .
            ?r schema:name ?name FILTER(lang(?name) = "en") }""",
    ),
    "/  Italy's grandparent": (
        places,
        """SELECT ?name WHERE {
            place:IT schema:containedInPlace/schema:containedInPlace/schema:name ?name
            FILTER(lang(?name) = "en") }""",
    ),
    "|  every name of the rent category": (
        categories,
        """SELECT ?label WHERE { fb:rent skos:prefLabel|skos:altLabel ?label }""",
    ),
    "+  categories under taxes, any depth": (
        categories,
        """SELECT ?label WHERE { ?c skos:broader+ fb:taxes ; skos:prefLabel ?label }""",
    ),
}

for title, (graph, query) in QUERIES.items():
    rows = sorted(
        ", ".join(str(v) for v in row) for row in graph.query(PREFIXES + query)
    )
    print(f"{title}:\n    {rows}")
