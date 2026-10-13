#!/usr/bin/env bash
# Upsert Action Workflow JSON definitions and read each one back to confirm it was saved.
# Usage: DATAHUB_URL=https://<instance>.acryl.io DATAHUB_TOKEN=<token> ./push_workflows.sh [file.json ...]
set -euo pipefail
: "${DATAHUB_URL:?set DATAHUB_URL, e.g. https://<instance>.acryl.io}"
: "${DATAHUB_TOKEN:?set DATAHUB_TOKEN to a personal access token}"
cd "$(dirname "$0")"
[ $# -gt 0 ] || set -- *.json

gql() {
  curl -sS --fail-with-body "${DATAHUB_URL%/}/api/graphql" \
    -H "Authorization: Bearer $DATAHUB_TOKEN" -H 'Content-Type: application/json' -d @-
}

for f in "$@"; do
  urn=$(jq -r .urn "$f")
  echo "==> $f ($urn)"
  resp=$(jq '{query: "mutation($input: UpsertActionWorkflowInput!) { upsertActionWorkflow(input: $input) { urn name } }", variables: {input: .}}' "$f" | gql)
  if jq -e '.errors' <<<"$resp" >/dev/null; then echo "$resp" | jq .errors; exit 1; fi
  echo "    upserted: $(jq -r '.data.upsertActionWorkflow.name' <<<"$resp")"

  # listActionWorkflows is search-index backed, so a fresh upsert can take a few seconds to show up.
  # ponytail: no single-workflow query exists, so list and filter; raise count past 1000 workflows
  for _ in $(seq 10); do
    found=$(jq -n '{query: "{ listActionWorkflows(input: {start: 0, count: 1000}) { workflows { urn steps { id } trigger { form { fields { id } } } } } }"}' | gql |
      jq -r --arg u "$urn" '.data.listActionWorkflows.workflows[] | select(.urn == $u)
        | "\(.steps | length) steps [\(.steps | map(.id) | join(", "))], \(.trigger.form.fields | length) fields"')
    [ -n "$found" ] && break
    sleep 3
  done
  [ -n "$found" ] || { echo "    NOT FOUND after upsert"; exit 1; }
  echo "    verified: $found"
done
