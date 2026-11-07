"""Step 4 of the pipeline: put each category in a tree (a small ontology).

The rules say WHAT a transaction is ("eating out"). The tree in
data/categories.ttl says what that category is a kind of:

    eating out  --skos:broader-->  food

so totals can be read at any level. The file uses SKOS, a W3C vocabulary
for category trees:

    skos:Concept     a node of the tree
    skos:prefLabel   its name; the English one must equal the category text
    skos:altLabel    a synonym ("supermarket" for groceries)
    skos:broader     its parent ("is a kind of"); at most one here

rdflib reads the Turtle file into a Graph: a set of
(subject, predicate, object) triples.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from pathlib import Path

import pandas as pd
from rdflib import RDF, Graph, Literal
from rdflib.namespace import SKOS
from rdflib.term import Node


def _english(tree: Graph, concept: Node) -> str | None:
    """English prefLabel of a concept ('food', not 'cibo')."""
    for label in tree.objects(concept, SKOS.prefLabel):
        if isinstance(label, Literal) and label.language == "en":
            return str(label)
    return None


def load_tree(path: Path) -> Graph:
    """Read the Turtle file and check it is a tree with unique English names."""
    tree = Graph().parse(path)
    concepts = list(tree.subjects(RDF.type, SKOS.Concept))

    names = {concept: _english(tree, concept) for concept in concepts}
    unnamed = [str(c) for c, name in names.items() if name is None]
    if unnamed:
        raise ValueError(f"concepts without an English prefLabel: {unnamed}")

    counts = Counter(names.values())
    duplicates = sorted(name for name, n in counts.items() if n > 1)
    if duplicates:
        raise ValueError(f"label used by more than one concept: {duplicates}")

    for concept, name in names.items():
        if len(list(tree.objects(concept, SKOS.broader))) > 1:
            raise ValueError(f"{name!r} has more than one parent (skos:broader)")
    return tree


def _find(tree: Graph, label: str) -> Node | None:
    """Concept whose English prefLabel, or else altLabel, is `label`."""
    text = Literal(label, lang="en")
    for prop in (SKOS.prefLabel, SKOS.altLabel):
        concept = tree.value(predicate=prop, object=text)
        if concept is not None:
            return concept
    return None


def category_path(tree: Graph, label: str) -> list[str]:
    """'groceries' -> ['food', 'groceries'], from the top down; [] if unknown."""
    path: list[str] = []
    concept = _find(tree, label)
    while concept is not None:
        name = _english(tree, concept)
        if name in path:
            raise ValueError(f"loop in the tree at {name!r}")
        path.insert(0, name)
        concept = tree.value(concept, SKOS.broader)  # parent, or None at the top
    return path


def missing_categories(tree: Graph, labels: Iterable[str]) -> list[str]:
    """Labels that are not in the tree, sorted, each once."""
    return sorted({label for label in labels if not category_path(tree, label)})


def add_levels(df: pd.DataFrame, tree: Graph) -> pd.DataFrame:
    """Return a copy of df with `level1` (top group) and `level2` columns.

    A short path repeats its deepest level ('salary' -> salary, salary), so a
    groupby on any level keeps every row. A category not in the tree is used
    as its own level.
    """
    paths = {}
    for label in df["category"].unique():
        paths[label] = category_path(tree, label) or [label]
    out = df.copy()
    out["level1"] = df["category"].map({c: p[0] for c, p in paths.items()})
    out["level2"] = df["category"].map(
        {c: p[min(1, len(p) - 1)] for c, p in paths.items()}
    )
    return out
