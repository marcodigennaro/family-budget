from pathlib import Path

# Project root: src/family_budget/config.py -> 3 levels up
BASE_DIR = Path(__file__).resolve().parents[2]

DATA_DIR = BASE_DIR / "data"
BNP_DIR = DATA_DIR / "BNP"
BELFIUS_DIR = DATA_DIR / "belfius_common"
OUTPUT_DIR = DATA_DIR / "processed"
RULES_PATH = DATA_DIR / "known_expenses.csv"
EXCEPTIONAL_PATH = DATA_DIR / "exceptional.csv"
TRIPS_PATH = DATA_DIR / "trips.csv"
CATEGORIES_PATH = DATA_DIR / "categories.ttl"

# General, not personal: generated from open data and kept in the repository
PLACES_PATH = BASE_DIR / "ontologies" / "places.ttl"
