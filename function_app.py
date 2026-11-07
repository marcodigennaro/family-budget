"""Azure Functions entry point: thin wrappers around the tested package.

api     HTTP, /api/data/{*path}: answers with route() from processed/transactions.csv.
        Needs the function key (?code=): for scripts and curl. (Not a catch-all
        /api/{*path}: that would also swallow /api/web.)
web     /api/web: the dashboard page; /api/web/data/...: the same answers as
        api; /api/web/upload: puts a CSV into raw/. Needs a login (Azure's
        built-in authentication: Microsoft or GitHub) and a name in ALLOWED_USERS.
ingest  Blob trigger on raw/: a new bank export is added with process_file(),
        the table is written back to processed/, the export moves to archive/
        (or to rejected/ if it is not a BNP or Belfius file).

Neither function decides anything itself: that is family_budget, under test.
Storage is reached with the app's managed identity (your az login locally).

Settings (Azure: app settings; locally: local.settings.json):
    STORAGE_ACCOUNT_URL  https://<account>.blob.core.windows.net
    TRANSACTIONS_URL     <STORAGE_ACCOUNT_URL>/processed/transactions.csv
    SQL_SERVER, SQL_DATABASE   optional: also save the table to Azure SQL
    EASY_AUTH, AUTH_PROVIDER, ALLOWED_USERS   the dashboard login (family_budget.web)
"""

import json
import logging
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import azure.functions as func
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobClient, BlobServiceClient

# the package lives in src/ (standard src layout)
sys.path.insert(0, str(Path(__file__).parent / "src"))

from family_budget.api import load_transactions, route, target_from_url  # noqa: E402
from family_budget.ingest import load_config, process_file  # noqa: E402
from family_budget.store import connect, create_schema, save_to_db  # noqa: E402
from family_budget.web import (  # noqa: E402
    check_login,
    dashboard_html,
    login_url,
    safe_blob_name,
    validate_upload,
)

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


def read_transactions_blob() -> bytes:
    # DefaultAzureCredential = the managed identity on Azure, your `az login` locally
    blob = BlobClient.from_blob_url(
        os.environ["TRANSACTIONS_URL"], credential=DefaultAzureCredential()
    )
    return blob.download_blob().readall()


NO_DATA = {"error": "No data yet: upload a bank export."}


def _answer(method: str, url: str, prefix: str) -> func.HttpResponse:
    """route() on the processed table; 404 before the first upload (or after reset)."""
    try:
        df = load_transactions(read_transactions_blob())
    except ResourceNotFoundError:
        return _json(404, NO_DATA)
    return _json(*route(method, target_from_url(url, prefix=prefix), df))


def _json(status: int, body) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(body, ensure_ascii=False, indent=2),
        status_code=status,
        mimetype="application/json",
    )


@app.route(route="data/{*path}", methods=["GET", "POST", "PUT", "DELETE"])
def api(req: func.HttpRequest) -> func.HttpResponse:
    return _answer(req.method, req.url, prefix="/api/data")


# --- web: the dashboard, behind the Microsoft login ---------------------------------
# ANONYMOUS here means "no function key": the login check below protects them.

WEB = func.AuthLevel.ANONYMOUS


@app.route(route="web", methods=["GET"], auth_level=WEB)
def web_page(req: func.HttpRequest) -> func.HttpResponse:
    status, message = check_login(req.headers, os.environ)
    if status == 401:  # not signed in: off to the sign-in page (Microsoft, GitHub...)
        provider = os.environ.get("AUTH_PROVIDER", "aad")
        return func.HttpResponse(
            status_code=302, headers={"Location": login_url("/api/web", provider)}
        )
    if status != 200:
        return func.HttpResponse(message, status_code=status, mimetype="text/plain")
    return func.HttpResponse(dashboard_html(), mimetype="text/html")


@app.route(route="web/data/{*path}", methods=["GET"], auth_level=WEB)
def web_data(req: func.HttpRequest) -> func.HttpResponse:
    status, message = check_login(req.headers, os.environ)
    if status != 200:
        return _json(status, {"error": message})
    return _answer("GET", req.url, prefix="/api/web/data")


@app.route(route="web/upload", methods=["POST"], auth_level=WEB)
def web_upload(req: func.HttpRequest) -> func.HttpResponse:
    status, who = check_login(req.headers, os.environ)
    if status != 200:
        return _json(status, {"error": who})
    name, content = req.params.get("name", ""), req.get_body()
    error = validate_upload(name, len(content))
    if error:
        return _json(400, {"error": error})
    blob = safe_blob_name(name, datetime.now(UTC))
    _storage().get_blob_client("raw", blob).upload_blob(content)  # ingest takes over
    logging.info("upload by %s: raw/%s (%d bytes)", who, blob, len(content))
    return _json(202, {"accepted": blob})


# --- ingest: a new export in raw/ -------------------------------------------------


def _storage() -> BlobServiceClient:
    return BlobServiceClient(
        os.environ["STORAGE_ACCOUNT_URL"], credential=DefaultAzureCredential()
    )


def _move(storage: BlobServiceClient, name: str, content: bytes, to: str) -> None:
    """raw/<name> -> <to>/<name> (copy, then delete the original)."""
    storage.get_blob_client(to, name).upload_blob(content, overwrite=True)
    storage.get_blob_client("raw", name).delete_blob()


@app.blob_trigger(
    arg_name="blob",
    path="raw/{name}",
    connection="AzureWebJobsStorage",  # same storage, via the managed identity
    source=func.BlobSource.EVENT_GRID,  # Flex Consumption: Event Grid calls us
)
def ingest(blob: func.InputStream) -> None:
    name = blob.name.removeprefix("raw/")
    content = blob.read()
    storage = _storage()

    # 1. config/ -> a temporary folder -> Config (rules, category tree, trips)
    with tempfile.TemporaryDirectory() as tmp:
        config_folder = storage.get_container_client("config")
        for item in config_folder.list_blobs():
            data = config_folder.download_blob(item.name).readall()
            (Path(tmp) / Path(item.name).name).write_bytes(data)
        config = load_config(Path(tmp))

    # 2. the table so far (none before the first upload)
    processed = storage.get_blob_client("processed", "transactions.csv")
    existing = None
    if processed.exists():
        existing = load_transactions(processed.download_blob().readall())

    # 3. the tested logic
    try:
        table, added = process_file(content, config, existing)
    except ValueError as error:
        logging.warning("raw/%s rejected: %s", name, error)
        _move(storage, name, content, to="rejected")
        return

    # 4. write the result (CSV, and the database when one is configured)
    processed.upload_blob(table.to_csv(index=False), overwrite=True)
    if os.environ.get("SQL_SERVER"):
        # if this fails, the export stays in raw/ and Azure retries it:
        # safe, because processing the same file twice adds nothing
        with connect() as conn:
            create_schema(conn)
            save_to_db(conn, table)
        logging.info("saved %d rows to Azure SQL", len(table))

    # 5. file the export away
    _move(storage, name, content, to="archive")
    logging.info("raw/%s: %d new rows, %d in total", name, added, len(table))
