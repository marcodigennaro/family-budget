import os
from pathlib import Path

# Project root: src/family_budget/config.py -> 3 levels up
BASE_DIR = Path(__file__).resolve().parents[2]

# Your real exports and rules live outside the project:
#   export FAMILY_BUDGET_DATA=~/Documents/family-budget-data
# Without the setting, data/ in the project is used: fake data made with
#   python -m family_budget.fake
DATA_DIR = Path(os.environ.get("FAMILY_BUDGET_DATA", BASE_DIR / "data")).expanduser()
# one folder per account inside each bank: BNP/BE00000000000001/2025.csv
BNP_DIR = DATA_DIR / "BNP"
BELFIUS_DIR = DATA_DIR / "BELFIUS"
OUTPUT_DIR = DATA_DIR / "processed"
RULES_PATH = DATA_DIR / "known_expenses.csv"
EXCEPTIONAL_PATH = DATA_DIR / "exceptional.csv"
TRIPS_PATH = DATA_DIR / "trips.csv"
CATEGORIES_PATH = DATA_DIR / "categories.ttl"

# General, not personal: generated from open data and kept in the repository
PLACES_PATH = BASE_DIR / "ontologies" / "places.ttl"
