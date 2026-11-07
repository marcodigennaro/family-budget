#!/usr/bin/env bash
# family-budget on Azure: every shell command in one place.
#
# Run from the project folder:
#   bash scripts/azure.sh check            is everything in place? (changes nothing)
#   bash scripts/azure.sh deploy           tests + ruff, then publish the code
#   bash scripts/azure.sh upload FILE      put a bank export into raw/
#   bash scripts/azure.sh results          what the ingest function produced
#   bash scripts/azure.sh setup            create everything (safe to re-run)
#   bash scripts/azure.sh sql              create the Azure SQL database (safe to re-run)
#   bash scripts/azure.sh web USERS        switch the dashboard login on for these
#                                          comma-separated Microsoft e-mail addresses
#                                          (PROVIDER=github: GitHub user names instead)
#   bash scripts/azure.sh allow USERS      add people to the login list (keeps the others)
#   bash scripts/azure.sh reset            delete the processed table (e.g. the fake data)
#   bash scripts/azure.sh config [FOLDER]  upload your rules to config/ (default: data/)
#   bash scripts/azure.sh github           let GitHub Actions deploy main (once)
#
# Real data (anything from data/) goes up only once the login is on: `upload`
# and `config` refuse it before that. Do the stranger test first.

set -euo pipefail
[[ -n "${DEBUG:-}" ]] && set -x   # DEBUG=1 bash scripts/azure.sh ...  shows every command

RG=rg-family-budget
ST=stfamilybudgetmdg
APP=func-family-budget-mdg
ID=id-family-budget
LOC_STORAGE=westeurope
LOC_APP=northeurope   # West Europe refused new resources for this subscription
TENANT=63d9eeee-c12a-48e6-9040-216e41d6028c
# becomes <name>.database.windows.net; override: SQL_SERVER=other-name bash ...
SQL_SERVER=${SQL_SERVER:-sql-family-budget-mdg}
SQL_DB=familybudget
# regions to try for the SQL server, in order (override: SQL_REGIONS="x y" bash ...)
SQL_REGIONS=${SQL_REGIONS:-"swedencentral francecentral germanywestcentral belgiumcentral uksouth northeurope"}
STORAGE_URL="https://$ST.blob.core.windows.net"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok() { printf '  \033[32mok\033[0m    %s\n' "$*"; }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAILED=1; }
step() { printf '  -> %s\n' "$*"; }

long() {  # long "what it does" command...   prints a dot every 5 s while it runs
  local what=$1 seconds=0 pid
  shift
  printf '  -> %s ' "$what"
  "$@" &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    sleep 5
    seconds=$((seconds + 5))
    printf '.'
  done
  if wait "$pid"; then
    printf ' done (%ss)\n' "$seconds"
  else
    printf ' FAILED after %ss\n' "$seconds"
    return 1
  fi
}

in_project() {
  [[ -f function_app.py && -d src/family_budget ]] ||
    { echo "Run this from the family-budget folder."; exit 1; }
}

logged_in() {
  step "checking the Azure login"
  if ! az account show --query name -o tsv >/dev/null 2>&1; then
    step "not logged in: a browser window opens for az login"
    az login --tenant "$TENANT" -o none
  fi
  step "subscription: $(az account show --query name -o tsv)"
}

storage_id() { az storage account show -n "$ST" -g "$RG" --query id -o tsv; }
function_key() { az functionapp keys list -g "$RG" -n "$APP" --query functionKeys.default -o tsv; }

# --- check ---------------------------------------------------------------------------

check() {
  FAILED=0
  say "Code"
  if pytest -q >/dev/null 2>&1; then ok "pytest"; else bad "pytest (run: pytest)"; fi

  say "Function App"
  local fns
  fns=$(az functionapp function list -g "$RG" -n "$APP" --query "[].name" -o tsv 2>/dev/null || true)
  for f in api ingest web_page web_data web_upload; do
    if grep -q "/$f$" <<<"$fns"; then ok "function $f"; else bad "function $f (run: deploy)"; fi
  done
  local url
  url=$(az functionapp config appsettings list -g "$RG" -n "$APP" \
    --query "[?name=='STORAGE_ACCOUNT_URL'].value" -o tsv 2>/dev/null || true)
  if [[ "$url" == "$STORAGE_URL" ]]; then ok "setting STORAGE_ACCOUNT_URL"; else bad "setting STORAGE_ACCOUNT_URL"; fi

  say "Storage"
  local containers
  containers=$(az storage container list --account-name "$ST" --auth-mode login --query "[].name" -o tsv)
  for c in raw processed config archive rejected; do
    if grep -qx "$c" <<<"$containers"; then ok "container $c"; else bad "container $c"; fi
  done
  if az storage blob exists --account-name "$ST" -c config -n known_expenses.csv \
    --auth-mode login --query exists -o tsv | grep -q true; then
    ok "config/known_expenses.csv"
  else bad "config/known_expenses.csv"; fi

  say "Event Grid"
  local state
  state=$(az eventgrid event-subscription show --name ingest-raw --source-resource-id "$(storage_id)" \
    --query provisioningState -o tsv 2>/dev/null || true)
  if [[ "$state" == "Succeeded" ]]; then ok "subscription ingest-raw"; else bad "subscription ingest-raw"; fi

  say "API"
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' "https://$APP.azurewebsites.net/api/data/months?code=$(function_key)")
  if [[ "$code" == "200" ]]; then ok "/api/data/months answers 200"; else bad "/api/data/months answers $code"; fi
  code=$(curl -s -o /dev/null -w '%{http_code}' "https://$APP.azurewebsites.net/api/web")
  case "$code" in
    302) ok "/api/web sends visitors to sign in" ;;
    503) bad "/api/web: login not switched on (run: bash scripts/azure.sh web EMAILS)" ;;
    *) bad "/api/web answers $code" ;;
  esac

  echo
  if [[ $FAILED == 0 ]]; then echo "All good."; else echo "Something is missing: see FAIL lines."; exit 1; fi
}

# --- deploy --------------------------------------------------------------------------

deploy() {
  say "Tests and style"
  pytest -q
  ruff format --check . && ruff check .
  say "Publish"
  func azure functionapp publish "$APP"
}

# --- upload --------------------------------------------------------------------------

setting() {  # setting NAME   -> its value in the Function App ("" if not set)
  az functionapp config appsettings list -g "$RG" -n "$APP" \
    --query "[?name=='$1'].value | [0]" -o tsv 2>/dev/null || true
}

real_data_ok() {  # real_data_ok FILE_OR_FOLDER   refuses real data while the login is off
  case "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")" in
    */family-budget/data*) ;;
    *) return 0 ;;   # not from data/: fake or example files, always fine
  esac
  if [[ "$(setting EASY_AUTH)" != "on" || -z "$(setting ALLOWED_USERS)" ]]; then
    echo "Refused: $1 is real bank data and the dashboard login is not on yet."
    echo "Run: bash scripts/azure.sh web you@outlook.com   (then do the stranger test)"
    exit 1
  fi
  step "real data: login is on for $(setting ALLOWED_USERS)"
}

upload() {
  local file=${1:?usage: bash scripts/azure.sh upload FILE}
  [[ -f "$file" ]] || { echo "No such file: $file"; exit 1; }
  real_data_ok "$file"
  say "Upload to raw/"
  az storage blob upload --account-name "$ST" -c raw -n "$(basename "$file")" \
    -f "$file" --auth-mode login --overwrite -o none
  echo "Uploaded. Wait about a minute, then: bash scripts/azure.sh results"
}

# --- config (your rules and categories -> config/) ----------------------------------

CONFIG_FILES="known_expenses.csv exceptional.csv categories.ttl trips.csv"

config() {
  local folder=${1:-data}
  [[ -f "$folder/known_expenses.csv" ]] || { echo "No $folder/known_expenses.csv"; exit 1; }
  real_data_ok "$folder"
  say "Upload rules to config/ (replaces what is there)"
  local f
  for f in $CONFIG_FILES; do
    if [[ -f "$folder/$f" ]]; then
      long "$f" az storage blob upload --account-name "$ST" -c config -n "$f" \
        -f "$folder/$f" --auth-mode login --overwrite -o none
    else
      step "$f: not in $folder, skipped (optional)"
    fi
  done
  echo "New rules apply to the next upload. To re-classify everything:"
  echo "  bash scripts/azure.sh reset, then upload all exports again."
}

# --- reset (start the processed table from nothing) ---------------------------------

reset() {
  say "Delete processed/transactions.csv"
  echo "The dashboard is empty until the next upload; exports in archive/ are kept."
  echo "Azure SQL is replaced by the next upload too."
  local answer
  read -r -p "  Type yes to delete: " answer
  [[ "$answer" == "yes" ]] || { echo "Nothing deleted."; exit 1; }
  az storage blob delete --account-name "$ST" -c processed -n transactions.csv \
    --auth-mode login -o none 2>/dev/null || step "there was no table: nothing to delete"
  echo "Done. Upload exports with the dashboard's Upload button or: bash scripts/azure.sh upload FILE"
}

# --- results -------------------------------------------------------------------------

results() {
  for c in raw archive rejected; do
    say "$c/"
    az storage blob list --account-name "$ST" -c "$c" --auth-mode login \
      --query "[].{name:name, modified:properties.lastModified}" -o table
  done
  say "/api/data/months"
  curl -s "https://$APP.azurewebsites.net/api/data/months?code=$(function_key)"
  echo
}

# --- setup (everything from scratch; skips what already exists) ----------------------

setup() {
  say "Resource group and providers"
  long "resource group $RG" az group create -n "$RG" -l "$LOC_STORAGE" -o none
  for p in Microsoft.Storage Microsoft.ManagedIdentity Microsoft.Web Microsoft.EventGrid; do
    long "register $p" az provider register --namespace "$p" --wait
  done

  say "Storage account (no anonymous access, no keys)"
  if ! az storage account show -n "$ST" -g "$RG" -o none 2>/dev/null; then
    long "create storage account $ST" \
      az storage account create -n "$ST" -g "$RG" -l "$LOC_STORAGE" --sku Standard_LRS \
      --kind StorageV2 --allow-blob-public-access false --allow-shared-key-access false \
      --min-tls-version TLS1_2 -o none
  fi
  local st_id me
  st_id=$(storage_id)
  me=$(az ad signed-in-user show --query id -o tsv)
  grant "$me" User "Storage Blob Data Contributor" "$st_id"
  echo "(a new role can take a few minutes to apply)"
  for c in raw processed config archive rejected app-package; do
    long "container $c" az storage container create --account-name "$ST" -n "$c" \
      --auth-mode login -o none
  done

  say "Managed identity"
  az identity show -g "$RG" -n "$ID" -o none 2>/dev/null ||
    long "create identity $ID" az identity create -g "$RG" -n "$ID" -l "$LOC_APP" -o none
  local id_res id_principal id_client
  id_res=$(az identity show -g "$RG" -n "$ID" --query id -o tsv)
  id_principal=$(az identity show -g "$RG" -n "$ID" --query principalId -o tsv)
  id_client=$(az identity show -g "$RG" -n "$ID" --query clientId -o tsv)
  for role in "Storage Blob Data Owner" "Storage Queue Data Contributor" "Storage Table Data Contributor"; do
    grant "$id_principal" ServicePrincipal "$role" "$st_id"
  done

  say "Function App (Flex Consumption)"
  if ! az functionapp show -g "$RG" -n "$APP" -o none 2>/dev/null; then
    long "create Function App $APP (1-2 min)" \
      az functionapp create -g "$RG" -n "$APP" --flexconsumption-location "$LOC_APP" \
      --runtime python --runtime-version 3.11 --storage-account "$ST" \
      --deployment-storage-name "$ST" --deployment-storage-container-name app-package \
      --deployment-storage-auth-type UserAssignedIdentity --deployment-storage-auth-value "$id_res" \
      --assign-identity "$id_res" -o none
  fi
  az functionapp config appsettings set -g "$RG" -n "$APP" -o none --settings \
    AzureWebJobsStorage__accountName="$ST" AzureWebJobsStorage__credential=managedidentity \
    AzureWebJobsStorage__clientId="$id_client" AZURE_CLIENT_ID="$id_client" \
    STORAGE_ACCOUNT_URL="$STORAGE_URL" TRANSACTIONS_URL="$STORAGE_URL/processed/transactions.csv"
  az functionapp config appsettings delete -g "$RG" -n "$APP" \
    --setting-names AzureWebJobsStorage -o none 2>/dev/null || true

  say "Fake data and rules"
  az storage blob upload --account-name "$ST" -c config -n known_expenses.csv \
    -f examples/config/known_expenses.csv --auth-mode login --overwrite -o none
  if ! az storage blob exists --account-name "$ST" -c processed -n transactions.csv \
    --auth-mode login --query exists -o tsv | grep -q true; then
    az storage blob upload --account-name "$ST" -c processed -n transactions.csv \
      -f examples/transactions_sample.csv --auth-mode login -o none
  fi

  deploy

  say "Event Grid: raw/ -> ingest"
  if ! az eventgrid event-subscription show --name ingest-raw --source-resource-id "$st_id" -o none 2>/dev/null; then
    local blobs_key=""
    for _ in 1 2 3 4 5 6; do   # the key appears shortly after the first publish
      blobs_key=$(az functionapp keys list -g "$RG" -n "$APP" --query systemKeys.blobs_extension -o tsv)
      [[ -n "$blobs_key" ]] && break
      sleep 20
    done
    [[ -n "$blobs_key" ]] || { echo "No blobs_extension key yet: run setup again in a minute."; exit 1; }
    az eventgrid event-subscription create --name ingest-raw --source-resource-id "$st_id" \
      --endpoint "https://$APP.azurewebsites.net/runtime/webhooks/blobs?functionName=Host.Functions.ingest&code=$blobs_key" \
      --endpoint-type webhook --included-event-types Microsoft.Storage.BlobCreated \
      --subject-begins-with /blobServices/default/containers/raw/ -o none
  fi
  check
}

# --- sql (Phase 5: Azure SQL free offer, Entra login only) ----------------------------

sql() {
  say "1/5 Provider"
  long "register Microsoft.Sql (first time: up to ~10 min)" \
    az provider register --namespace Microsoft.Sql --wait

  say "2/5 SQL server: login with Microsoft accounts only, no SQL passwords"
  if az sql server show -g "$RG" -n "$SQL_SERVER" -o none 2>/dev/null; then
    step "server $SQL_SERVER already exists"
  else
    local upn oid
    upn=$(az ad signed-in-user show --query userPrincipalName -o tsv)
    oid=$(az ad signed-in-user show --query id -o tsv)
    step "admin will be $upn"
    local region created=""
    for region in $SQL_REGIONS; do   # busy regions refuse new subscriptions
      if long "create server $SQL_SERVER in $region (a few minutes)" \
        az sql server create -g "$RG" -n "$SQL_SERVER" -l "$region" \
        --enable-ad-only-auth --external-admin-principal-type User \
        --external-admin-name "$upn" --external-admin-sid "$oid" -o none; then
        created=$region
        break
      fi
      step "$region refused, trying the next region"
    done
    [[ -n "$created" ]] || { echo "No region accepted the SQL server: see the errors above."; exit 1; }
    step "server created in $created"
  fi

  say "3/5 Firewall: this Mac, and Azure services (the Function App)"
  local ip
  ip=$(curl -s https://api.ipify.org)
  step "this Mac's internet address: $ip"
  long "allow this Mac" az sql server firewall-rule create -g "$RG" -s "$SQL_SERVER" \
    -n this-mac --start-ip-address "$ip" --end-ip-address "$ip" -o none
  long "allow Azure services" az sql server firewall-rule create -g "$RG" -s "$SQL_SERVER" \
    -n azure-services --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0 -o none

  say "4/5 Database: free offer, pauses itself when the free monthly amount is used"
  if az sql db show -g "$RG" -s "$SQL_SERVER" -n "$SQL_DB" -o none 2>/dev/null; then
    step "database $SQL_DB already exists"
  else
    long "create database $SQL_DB (a few minutes)" \
      az sql db create -g "$RG" -s "$SQL_SERVER" -n "$SQL_DB" \
      --edition GeneralPurpose --family Gen5 --capacity 2 --compute-model Serverless \
      --use-free-limit --free-limit-exhaustion-behavior AutoPause \
      --backup-storage-redundancy Local -o none
  fi

  say "5/5 Table, access for the Function App"
  long "add SQL_SERVER and SQL_DATABASE to the Function App settings" \
    az functionapp config appsettings set -g "$RG" -n "$APP" -o none \
    --settings SQL_SERVER="$SQL_SERVER" SQL_DATABASE="$SQL_DB"
  if python -c "import mssql_python" 2>/dev/null; then
    long "create the transactions table (first connection can be slow)" \
      python -m family_budget.store "$SQL_SERVER" "$SQL_DB" schema
    long "let $ID read and write" \
      python -m family_budget.store "$SQL_SERVER" "$SQL_DB" grant "$ID"
    python -m family_budget.store "$SQL_SERVER" "$SQL_DB" count
  else
    # the SQL driver needs macOS 15+: do this one-time step in the browser instead
    step "no SQL driver on this Mac: run the SQL below once in the Azure Portal"
    step "portal.azure.com > SQL databases > $SQL_DB > Query editor > sign in > paste > Run"
    echo
    python -m family_budget.store print-sql "$ID"
    echo
  fi
  echo "Done."
}

# --- web (Phase 6: dashboard login) ---------------------------------------------------

web() {
  local users=${1:?usage: bash scripts/azure.sh web you@outlook.com,wife@example.com}
  local provider=${PROVIDER:-aad}   # aad = Microsoft accounts; PROVIDER=github also works
  say "Is Azure's sign-in (Authentication) switched on for $APP?"
  local enabled app_id
  app_id=$(az functionapp show -g "$RG" -n "$APP" --query id -o tsv)
  # `az webapp auth show` returns nothing for Flex Consumption apps: read the settings directly
  enabled=$(az resource show --ids "$app_id/config/authsettingsV2" \
    --query "properties.platform.enabled" -o tsv 2>/dev/null || true)
  if [[ "$enabled" != "true" ]]; then
    echo "Not yet. Add the $provider sign-in once in the portal, then run this again:"
    echo "  portal.azure.com > $APP > Settings > Authentication > Add identity provider"
    echo "  Unauthenticated requests: ALLOW (Event Grid must still reach ingest;"
    echo "  the dashboard refuses anyone not on your list by itself)"
    exit 1
  fi
  step "sign-in is on"
  long "provider $provider, allow: $users" \
    az functionapp config appsettings set -g "$RG" -n "$APP" -o none \
    --settings EASY_AUTH=on AUTH_PROVIDER="$provider" ALLOWED_USERS="$users"
  echo
  echo "Dashboard: https://$APP.azurewebsites.net/api/web"
  echo "A refused name is shown on the page ('... is not allowed'): add it exactly"
  echo "as shown with: bash scripts/azure.sh allow NAME"
}

allow() {
  local new=${1:?usage: bash scripts/azure.sh allow wife@example.com}
  [[ "$(setting EASY_AUTH)" == "on" ]] || { echo "Login is not on yet: run web first."; exit 1; }
  local current merged
  current=$(setting ALLOWED_USERS)
  merged=$(python -m family_budget.web merge-users "$current" "$new") || exit 1
  step "before: ${current:-nobody}"
  long "now: $merged" az functionapp config appsettings set -g "$RG" -n "$APP" -o none \
    --settings ALLOWED_USERS="$merged"
  echo "Takes effect in about 30 seconds."
}

# --- github (Phase 7: GitHub Actions deploys main, no passwords) ---------------------

REPO=marcodigennaro/family-budget
GH_ID=id-github-deploy

github() {
  say "1/3 An identity for GitHub, allowed only to publish code to $APP"
  az identity show -g "$RG" -n "$GH_ID" -o none 2>/dev/null ||
    long "create identity $GH_ID" az identity create -g "$RG" -n "$GH_ID" -l "$LOC_APP" -o none
  local principal client app_id
  principal=$(az identity show -g "$RG" -n "$GH_ID" --query principalId -o tsv)
  client=$(az identity show -g "$RG" -n "$GH_ID" --query clientId -o tsv)
  app_id=$(az functionapp show -g "$RG" -n "$APP" --query id -o tsv)
  grant "$principal" ServicePrincipal "Website Contributor" "$app_id"

  say "2/3 Trust GitHub tokens from $REPO, branch main only"
  if az identity federated-credential show -g "$RG" --identity-name "$GH_ID" \
    -n github-main -o none 2>/dev/null; then
    step "already trusted"
  else
    long "trust repo:$REPO:ref:refs/heads/main" \
      az identity federated-credential create -g "$RG" --identity-name "$GH_ID" \
      -n github-main --issuer https://token.actions.githubusercontent.com \
      --subject "repo:$REPO:ref:refs/heads/main" --audiences api://AzureADTokenExchange -o none
  fi

  say "3/3 Tell GitHub which identity to use (IDs, not secrets)"
  local sub
  sub=$(az account show --query id -o tsv)
  if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    gh variable set AZURE_CLIENT_ID -R "$REPO" --body "$client"
    gh variable set AZURE_TENANT_ID -R "$REPO" --body "$TENANT"
    gh variable set AZURE_SUBSCRIPTION_ID -R "$REPO" --body "$sub"
    step "variables set on $REPO"
  else
    echo "  github.com/$REPO > Settings > Secrets and variables > Actions > Variables tab"
    echo "  > New repository variable, three times:"
    echo "    AZURE_CLIENT_ID        $client"
    echo "    AZURE_TENANT_ID        $TENANT"
    echo "    AZURE_SUBSCRIPTION_ID  $sub"
  fi
  echo
  echo "Done. The next push to main deploys if the tests pass:"
  echo "  github.com/$REPO/actions"
}

grant() {  # grant PRINCIPAL_ID PRINCIPAL_TYPE ROLE SCOPE, unless it is already there
  local existing
  existing=$(az role assignment list --assignee "$1" --role "$3" --scope "$4" --query "[0].id" -o tsv)
  if [[ -n "$existing" ]]; then
    step "role '$3' already given"
  else
    long "give role '$3'" az role assignment create --assignee-object-id "$1" \
      --assignee-principal-type "$2" --role "$3" --scope "$4" -o none
  fi
}

# --- main ----------------------------------------------------------------------------

in_project
case "${1:-}" in
  check) logged_in; check ;;
  deploy) logged_in; deploy ;;
  upload) logged_in; upload "${2:-}" ;;
  results) logged_in; results ;;
  setup) logged_in; setup ;;
  sql) logged_in; sql ;;
  web) logged_in; web "${2:-}" ;;
  allow) logged_in; allow "${2:-}" ;;
  config) logged_in; config "${2:-}" ;;
  reset) logged_in; reset ;;
  github) logged_in; github ;;
  *) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
