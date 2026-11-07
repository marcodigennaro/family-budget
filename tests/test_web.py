"""Tests for the dashboard helpers: upload checks, login check, the page."""

import base64
import json
from datetime import datetime

import pytest

from family_budget.web import (
    MAX_UPLOAD_BYTES,
    check_login,
    dashboard_html,
    login_url,
    main,
    merge_users,
    safe_blob_name,
    user_from_headers,
    validate_upload,
)

# --- validate_upload: the first gate, before anything reaches raw/ -----------------


@pytest.mark.parametrize("name", ["2025.csv", "Export BNP.CSV", "x.csv"])
def test_csv_files_are_accepted(name):
    assert validate_upload(name, 2_000) is None


@pytest.mark.parametrize("name", ["statement.pdf", "photo.jpg", "export", "csv"])
def test_other_files_are_refused(name):
    assert "CSV" in validate_upload(name, 2_000)


def test_empty_file_is_refused():
    assert "empty" in validate_upload("x.csv", 0)


def test_size_limit():
    assert validate_upload("x.csv", MAX_UPLOAD_BYTES) is None
    assert "1 MB" in validate_upload("x.csv", MAX_UPLOAD_BYTES + 1)


# --- safe_blob_name: never overwrite, never leave raw/ ------------------------------

NOW = datetime(2026, 11, 7, 10, 15, 0)


def test_blob_name_starts_with_the_upload_time():
    assert safe_blob_name("2025.csv", NOW) == "20261107-101500_2025.csv"


def test_blob_name_drops_folders():
    assert safe_blob_name("../../config/known_expenses.csv", NOW).endswith(
        "_known_expenses.csv"
    )
    assert "/" not in safe_blob_name("a\\b/c.csv", NOW)


def test_blob_name_keeps_only_plain_characters():
    name = safe_blob_name("Extrait été 2025!.csv", NOW)
    assert name == "20261107-101500_Extrait_t_2025_.csv"


# --- login: Azure's built-in authentication signs people in; we check who --------


def principal(email):
    claims = {"claims": [{"typ": "preferred_username", "val": email}]}
    return base64.b64encode(json.dumps(claims).encode()).decode()


def test_user_comes_from_the_name_header():
    headers = {"X-MS-CLIENT-PRINCIPAL-NAME": "Marco@Example.com"}
    assert user_from_headers(headers) == "marco@example.com"


def test_header_names_are_case_insensitive():
    assert user_from_headers({"x-ms-client-principal-name": "a@b.c"}) == "a@b.c"


def test_user_from_the_encoded_principal_when_no_name_header():
    assert user_from_headers({"X-MS-CLIENT-PRINCIPAL": principal("a@b.c")}) == "a@b.c"


def test_no_login_headers_means_no_user():
    assert user_from_headers({}) is None
    assert user_from_headers({"X-MS-CLIENT-PRINCIPAL": "not base64!"}) is None


ON = {"EASY_AUTH": "on", "ALLOWED_USERS": "Marco@example.com, wife@example.com"}


def test_everyone_is_refused_until_login_is_switched_on():
    # without Azure's login in front, the headers could be faked by anyone
    headers = {"X-MS-CLIENT-PRINCIPAL-NAME": "marco@example.com"}
    status, _ = check_login(headers, {"ALLOWED_USERS": "marco@example.com"})
    assert status == 503


def test_not_signed_in_is_401():
    assert check_login({}, ON)[0] == 401


def test_signed_in_but_not_on_the_list_is_403():
    headers = {"X-MS-CLIENT-PRINCIPAL-NAME": "stranger@example.com"}
    assert check_login(headers, ON)[0] == 403


def test_people_on_the_list_get_in():
    headers = {"X-MS-CLIENT-PRINCIPAL-NAME": "WIFE@example.com"}
    assert check_login(headers, ON) == (200, "wife@example.com")


def test_login_url_brings_you_back_to_the_dashboard():
    assert login_url("/api/web") == (
        "/.auth/login/aad?post_login_redirect_uri=%2Fapi%2Fweb"
    )


def test_login_url_for_github():
    assert login_url("/api/web", provider="github").startswith("/.auth/login/github?")


def test_login_url_refuses_unknown_providers():
    with pytest.raises(ValueError):
        login_url("/api/web", provider="../evil")


def test_page_leaves_the_choice_of_sign_in_to_the_server():
    # on 401 the page reloads itself; the server knows which provider to use
    assert "/.auth/login" not in dashboard_html()


def test_an_empty_list_lets_nobody_in():
    headers = {"X-MS-CLIENT-PRINCIPAL-NAME": "marco@example.com"}
    assert check_login(headers, {"EASY_AUTH": "on"})[0] == 403


# --- merge_users: add people to ALLOWED_USERS without dropping anyone ---------------


def test_merge_adds_new_people_after_the_existing_ones():
    assert merge_users("a@x.com", "b@y.com") == "a@x.com,b@y.com"


def test_merge_into_an_empty_list():
    assert merge_users("", "a@x.com") == "a@x.com"


def test_merge_cleans_up_case_and_spaces():
    assert merge_users(" A@X.com ,", "B@y.com") == "a@x.com,b@y.com"


def test_merge_never_lists_anyone_twice():
    assert merge_users("a@x.com", "A@x.com") == "a@x.com"


def test_merge_takes_several_names_separated_by_commas_or_spaces():
    assert merge_users("a@x.com", "b@y.com, c@z.com d@w.com") == (
        "a@x.com,b@y.com,c@z.com,d@w.com"
    )


@pytest.mark.parametrize("bad", ["a@x.com;rm -rf", "x=y", 'a"@x.com', "é@x.com"])
def test_merge_refuses_odd_names(bad):
    # the list ends up in an `az ... --settings` command: keep it plain
    with pytest.raises(ValueError):
        merge_users("a@x.com", bad)


def test_merged_list_lets_the_new_person_in():
    allowed = merge_users("marco@example.com", "Wife@Example.com")
    env = {"EASY_AUTH": "on", "ALLOWED_USERS": allowed}
    headers = {"X-MS-CLIENT-PRINCIPAL-NAME": "wife@example.com"}
    assert check_login(headers, env) == (200, "wife@example.com")


def test_command_line_prints_the_merged_list(capsys):
    assert main(["merge-users", "a@x.com", "b@y.com"]) == 0
    assert capsys.readouterr().out.strip() == "a@x.com,b@y.com"


def test_command_line_explains_a_bad_name(capsys):
    assert main(["merge-users", "", "x=y"]) == 1
    assert "x=y" in capsys.readouterr().err


# --- the page -----------------------------------------------------------------------


def test_page_reads_its_data_relative_to_itself():
    html = dashboard_html()
    assert "<title>" in html
    assert 'BASE + "data/"' in html  # <page>/data/<resource>
    assert 'get("months")' in html and 'get("groups")' in html
    assert 'BASE + "upload' in html


def test_page_loads_nothing_from_other_sites():
    # charts are drawn in SVG by the page itself: no external scripts or styles
    html = dashboard_html()
    assert 'src="http' not in html and 'href="http' not in html


def test_page_never_puts_bank_text_into_html():
    # counterparty names come from bank exports: show them as text, never as HTML
    assert "innerHTML" not in dashboard_html()
