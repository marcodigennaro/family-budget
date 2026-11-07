"""A tiny read-only HTTP API over the processed data, to study HTTP URIs.

Run `python -m family_budget` first (it writes data/processed/transactions.csv),
then:  python -m family_budget.api   and open http://127.0.0.1:8000/

Anatomy of a URI used here:

    http://127.0.0.1:8000/categories/eating%20out/transactions?year=2025&limit=5
    └┬─┘   └────┬────┘ └┬─┘└──────────────────┬──────────────┘└────────┬───────┘
   scheme     host    port                  path                     query

- The PATH names a resource: /months, /months/2025-09, /transactions/<row_id>.
  Segments like 2025-09 or "eating out" are path parameters.
- The QUERY filters or shapes a collection: ?category=groceries&limit=10.
- Special characters are percent-encoded: a space is %20.
- The status code says what happened: 200 OK, 400 bad request,
  404 not found, 405 method not allowed.

Only the standard library is used, so you can see every step.
`route` is a pure function (no network), which is what the tests call.
"""

from __future__ import annotations

import io
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlsplit

import pandas as pd

from family_budget.config import OUTPUT_DIR
from family_budget.summary import monthly_summary, trip_summary

MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")  # 2025-09, not 2025-13
FIELDS = [
    "row_id",
    "date",
    "amount",
    "counterparty",
    "category",
    "level1",
    "type",
    "property",
    "trip",
]
FILTERS = ["category", "level1", "type", "property", "trip"]  # ?name=value
DEFAULT_LIMIT = 50


def load_transactions(
    source: Path | str | bytes = OUTPUT_DIR / "transactions.csv",
) -> pd.DataFrame:
    """Read the table written by `python -m family_budget`.

    `source` is a file path, or the file's bytes (as downloaded from Blob Storage).
    """
    if isinstance(source, bytes):
        source = io.BytesIO(source)
    df = pd.read_csv(source, parse_dates=["date"], dtype={"row_id": str})
    df["is_fixed"] = df["is_fixed"].astype(bool)
    return df


def _records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> JSON-friendly list of dicts (dates as text, NaN as null)."""
    cols = [c for c in FIELDS if c in df.columns]
    out = df[cols].copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    return json.loads(out.to_json(orient="records"))


def _summary_records(summary: pd.DataFrame, key: str) -> list[dict]:
    out = summary.reset_index()
    if "first_date" in out:
        out["first_date"] = out["first_date"].dt.strftime("%Y-%m-%d")
    return json.loads(
        out.rename(columns={out.columns[0]: key}).to_json(orient="records")
    )


def _filter(df: pd.DataFrame, query: dict[str, list[str]]):
    """Apply ?month= ?year= ?limit= and the FILTERS (?category= ?property= ...).

    Returns (rows, None) or (None, error message) for a 400.
    """
    allowed = {"month", "year", "limit", *FILTERS}
    unknown = set(query) - allowed
    if unknown:
        return None, f"unknown query parameter(s): {sorted(unknown)}"
    q = {k: v[0] for k, v in query.items()}  # first value of each parameter
    if "month" in q:
        if not MONTH.fullmatch(q["month"]):
            return None, "month must look like 2025-09"
        df = df[df["date"].dt.strftime("%Y-%m") == q["month"]]
    if "year" in q:
        if not q["year"].isdigit():
            return None, "year must be a number"
        df = df[df["date"].dt.year == int(q["year"])]
    for col in FILTERS:
        if col in q:
            values = df[col] if col in df else pd.Series(pd.NA, index=df.index)
            df = df[values.eq(q[col]).fillna(False).astype(bool)]
    limit = q.get("limit", str(DEFAULT_LIMIT))
    if not limit.isdigit():
        return None, "limit must be a positive number"
    return df.sort_values("date", ascending=False).head(int(limit)), None


def _groups(df: pd.DataFrame, query: dict[str, list[str]]):
    """Spending per top group (level1), positive amounts, biggest first."""
    year = query.get("year", [""])[0]
    if year and not year.isdigit():
        return 400, {"error": "year must be a number"}
    if year:
        df = df[df["date"].dt.year == int(year)]
    spending = df[df["type"] == "spending"]
    group = spending["level1"] if "level1" in spending else spending["category"]
    totals = (-spending["amount"]).groupby(group).agg(["sum", "count"])
    totals = totals.sort_values("sum", ascending=False)
    return 200, [
        {
            "group": name,
            "spending": round(float(row["sum"]), 2),
            "count": int(row["count"]),
        }
        for name, row in totals.iterrows()
    ]


def _properties(df: pd.DataFrame):
    """Per flat or house: rent and other money received, paid, and the net."""
    if "property" not in df:
        return 200, []
    rows = df[df["property"].notna()]
    out = []
    for name, group in rows.groupby("property"):
        received = float(group.loc[group["amount"] > 0, "amount"].sum())
        paid = float(-group.loc[group["amount"] < 0, "amount"].sum())
        out.append(
            {
                "property": name,
                "received": round(received, 2),
                "paid": round(paid, 2),
                "net": round(received - paid, 2),
            }
        )
    return 200, out


def route(method: str, target: str, df: pd.DataFrame) -> tuple[int, dict | list]:
    """Map (method, URI) to (status code, JSON body). No network involved."""
    if method != "GET":
        return 405, {"error": "this API is read-only: use GET"}

    parts = urlsplit(target)  # splits path from query
    query = parse_qs(parts.query)  # "a=1&b=2" -> {"a": ["1"], "b": ["2"]}
    segments = [unquote(s) for s in parts.path.strip("/").split("/") if s]

    match segments:
        case []:
            return 200, {
                "resources": [
                    "/months",
                    "/months/{YYYY-MM}",
                    (
                        "/transactions?month=&year=&category=&level1=&type="
                        "&property=&trip=&limit="
                    ),
                    "/transactions/{row_id}",
                    "/categories",
                    "/categories/{name}/transactions",
                    "/groups?year=",
                    "/properties",
                    "/trips",
                    "/trips/{name}",
                ]
            }
        case ["groups"]:
            return _groups(df, query)
        case ["properties"]:
            return _properties(df)
        case ["months"]:
            return 200, _summary_records(monthly_summary(df), "month")
        case ["months", month]:
            if not MONTH.fullmatch(month):
                return 400, {"error": "month must look like 2025-09"}
            summary = monthly_summary(df)
            if month not in summary.index:
                return 404, {"error": f"no data for {month}"}
            return 200, {"month": month, **summary.loc[month].to_dict()}
        case ["transactions"]:
            rows, error = _filter(df, query)
            return (400, {"error": error}) if error else (200, _records(rows))
        case ["transactions", row_id]:
            rows = df[df["row_id"] == row_id]
            if rows.empty:
                return 404, {"error": f"no transaction {row_id}"}
            return 200, _records(rows)[0]
        case ["categories"]:
            totals = df.groupby("category")["amount"].agg(["count", "sum"]).round(2)
            return 200, _summary_records(totals.sort_values("sum"), "category")
        case ["categories", name, "transactions"]:
            if name not in set(df["category"]):
                return 404, {"error": f"no category {name!r}"}
            rows, error = _filter(df[df["category"] == name], query)
            return (400, {"error": error}) if error else (200, _records(rows))
        case ["trips"]:
            return 200, _summary_records(trip_summary(df), "trip")
        case ["trips", name]:
            summary = trip_summary(df)
            if name not in summary.index:
                return 404, {"error": f"no trip {name!r}"}
            return 200, _summary_records(summary.loc[[name]], "trip")[0]
    return 404, {"error": f"no resource at {parts.path}"}


def target_from_url(url: str, prefix: str = "/api") -> str:
    """Azure Functions URL -> the target `route` expects.

    https://<app>.azurewebsites.net/api/months?code=KEY -> /months
    The /api prefix belongs to Azure (the dashboard uses /api/web/data), and
    `code` is the function key, which is a password: it must not reach
    `route` (it would be an unknown parameter).
    """
    parts = urlsplit(url)
    path = parts.path.removeprefix(prefix).rstrip("/") or "/"
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "code"]
    return path + ("?" + urlencode(query) if query else "")


def serve(port: int = 8000) -> None:
    """Serve on 127.0.0.1 only: your bank data never leaves this machine."""
    from family_budget.web import dashboard_html

    df = load_transactions()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, data: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _answer(self) -> None:
            path = urlsplit(self.path).path
            if path in ("/web", "/web/"):  # the dashboard page
                self._send(200, dashboard_html().encode(), "text/html")
                return
            if path.startswith("/web/upload"):  # uploads only exist on Azure
                body = {"error": "upload works on Azure only"}
                status = 405
            else:  # /web/data/<resource> is the same API as /<resource>
                target = self.path.removeprefix("/web/data")
                status, body = route(self.command, target or "/", df)
            data = json.dumps(body, ensure_ascii=False, indent=2).encode()
            self._send(status, data, "application/json")

        do_GET = do_POST = do_PUT = do_DELETE = _answer  # noqa: N815

    print(f"Dashboard: http://127.0.0.1:{port}/web/  (Ctrl+C to stop)")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    serve()
