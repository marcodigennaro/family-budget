"""Phase 6: helpers for the dashboard (page, data and upload behind a login).

Login is done by Azure's built-in authentication (Easy Auth) in front of the
Function App: it signs people in with their Microsoft account and passes who
they are in the X-MS-CLIENT-PRINCIPAL* request headers. This module decides
whether that person may see the data. Without Easy Auth those headers could
be sent by anyone, so nothing is allowed until EASY_AUTH=on is set.

Easy Auth is set to *allow* anonymous requests: Event Grid must still reach
the ingest webhook. The dashboard routes refuse them here instead, and the
page sends people without a login to login_url().

Settings:
    EASY_AUTH      "on" once Azure's authentication is switched on
    AUTH_PROVIDER  aad (Microsoft, default) or github, ...
    ALLOWED_USERS  comma-separated names as the provider gives them: e-mail
                   addresses for Microsoft, user names for GitHub
"""

from __future__ import annotations

import base64
import json
import re
import sys
from collections.abc import Mapping
from datetime import datetime
from importlib.resources import files
from pathlib import PurePosixPath
from urllib.parse import quote

MAX_UPLOAD_BYTES = 1_000_000  # a whole year of exports is about 350 kB


# --- upload ----------------------------------------------------------------------


def validate_upload(filename: str, size: int) -> str | None:
    """None if the file may go to raw/, otherwise the message to show."""
    if not filename.lower().endswith(".csv") or filename.lower() == ".csv":
        return "Only CSV exports from your bank are accepted."
    if size <= 0:
        return "The file is empty."
    if size > MAX_UPLOAD_BYTES:
        return "File too large (max 1 MB). Bank exports are much smaller."
    return None


def safe_blob_name(filename: str, now: datetime) -> str:
    """'../x/Extrait été.csv' -> '20261107-101500_Extrait_t.csv'.

    Only the last part of the name is kept (so nothing can leave raw/), odd
    characters become '_', and the upload time in front means a new upload
    never overwrites an older one.
    """
    name = PurePosixPath(filename.replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return f"{now:%Y%m%d-%H%M%S}_{name}"


# --- login -----------------------------------------------------------------------


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    return next((v for k, v in headers.items() if k.lower() == wanted), None)


def user_from_headers(headers: Mapping[str, str]) -> str | None:
    """The signed-in person's e-mail (lower case), or None."""
    name = _header(headers, "X-MS-CLIENT-PRINCIPAL-NAME")
    if name:
        return name.strip().lower()
    encoded = _header(headers, "X-MS-CLIENT-PRINCIPAL")
    if not encoded:
        return None
    try:
        claims = json.loads(base64.b64decode(encoded, validate=True))["claims"]
    except (ValueError, KeyError, TypeError):
        return None
    for claim in claims:
        if claim.get("typ") in ("preferred_username", "email", "name"):
            return str(claim.get("val", "")).strip().lower() or None
    return None


def check_login(headers: Mapping[str, str], env: Mapping[str, str]) -> tuple[int, str]:
    """(200, user) if this person may see the data, else (status, message)."""
    if env.get("EASY_AUTH", "").lower() != "on":
        return 503, "Login is not switched on yet (EASY_AUTH)."
    user = user_from_headers(headers)
    if user is None:
        return 401, "Please sign in."
    allowed = {u.strip().lower() for u in env.get("ALLOWED_USERS", "").split(",")}
    if user not in allowed - {""}:
        return 403, f"{user} is not allowed to see this budget."
    return 200, user


_PLAIN_NAME = re.compile(r"[a-z0-9._+-]+(@[a-z0-9.-]+)?")


def merge_users(current: str, new: str) -> str:
    """ALLOWED_USERS plus `new`: 'a@x.com' + 'B@y.com' -> 'a@x.com,b@y.com'.

    Names may be separated by commas or spaces; case and duplicates are cleaned up.
    A name with odd characters raises ValueError: the list goes into a shell command.
    """
    merged: list[str] = []
    for name in re.split(r"[,\s]+", f"{current},{new}".lower()):
        if not name or name in merged:
            continue
        if not _PLAIN_NAME.fullmatch(name):
            raise ValueError(f"not an e-mail address or user name: {name!r}")
        merged.append(name)
    return ",".join(merged)


PROVIDERS = {"aad", "github", "google", "facebook", "twitter", "apple"}


def login_url(return_to: str, provider: str = "aad") -> str:
    """Azure's sign-in page for `provider` (AUTH_PROVIDER setting: aad = Microsoft).

    After signing in, it sends you back to `return_to`.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"unknown sign-in provider {provider!r}")
    redirect = quote(return_to, safe="")
    return f"/.auth/login/{provider}?post_login_redirect_uri={redirect}"


# --- the page --------------------------------------------------------------------


def dashboard_html() -> str:
    """The single-page dashboard (src/family_budget/static/dashboard.html)."""
    return (files("family_budget") / "static" / "dashboard.html").read_text("utf-8")


# --- command line (used by scripts/azure.sh allow) -------------------------------


def main(argv: list[str]) -> int:
    """python -m family_budget.web merge-users CURRENT NEW   prints the merged list."""
    if len(argv) != 3 or argv[0] != "merge-users":
        print(main.__doc__, file=sys.stderr)
        return 2
    try:
        print(merge_users(argv[1], argv[2]))
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
