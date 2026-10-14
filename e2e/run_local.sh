#!/usr/bin/env bash
# End-to-end run against a local DataHub (docker quickstart or the fork's quickstartDebug).
#
# Starts the stock actions image on the GMS container's Docker network with this checkout mounted
# the way helm/values.yaml mounts the ConfigMaps (recipe in the conf dir, package on PYTHONPATH),
# waits for the pipeline, runs e2e/seed_and_run.py, then stops the container. The pipeline log and
# any failed events land in e2e/out/. Exit code is the harness's; the last line is "E2E PASS".
#
# Every knob is an environment variable:
#   DATAHUB_GMS_URL         http://localhost:8080         GMS as reached from this machine
#   DATAHUB_TOKEN           (minted by the harness)       PAT that files and approves
#   GMS_CONTAINER           (publisher of the URL's port) GMS container
#   DOCKER_NETWORK          (GMS_CONTAINER's network)
#   DATAHUB_GMS_HOST        datahub-gms                   GMS hostname on that network
#   DATAHUB_GMS_PORT        8080                          GMS port on that network
#   PLATFORM_EVENT_TOPIC_NAME (recipe default)            only if the stack renamed the topic
#   KAFKA_BOOTSTRAP_SERVER  broker:29092                  broker as seen on that network
#   SCHEMA_REGISTRY_URL     http://$DATAHUB_GMS_HOST:8080/schema-registry/api/
#   ACTIONS_IMAGE           acryldata/datahub-actions:v1.7.0.1-slim
#   ACTIONS_CONTAINER       workflow-actions-e2e
#   ACTIONS_TOKEN           ""                            pipeline PAT; empty = system client creds
#   DATAHUB_SYSTEM_CLIENT_ID / DATAHUB_SYSTEM_CLIENT_SECRET   quickstart defaults
#   E2E_ARGS                "--reject --cancel"           harness flags
#   E2E_TIMEOUT             120                           seconds per side effect
#   E2E_CAPTURE             ""   1 = also run e2e/capture.yaml and write every event to e2e/out/events/
#   READY_TIMEOUT           180                           seconds for the pipeline to come up
#   KEEP_ACTIONS            ""   1 = leave the container running afterwards
#   PYTHON                  .venv/bin/python              interpreter for the harness
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT="$ROOT/e2e/out"
mkdir -p "$OUT/failed"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PYTHON" ]] || { echo "no interpreter at $PYTHON; set PYTHON (or create .venv, see README)" >&2; exit 1; }
CONTAINER="${ACTIONS_CONTAINER:-workflow-actions-e2e}"
ACTIONS_IMAGE="${ACTIONS_IMAGE:-acryldata/datahub-actions:v1.7.0.1-slim}"
export DATAHUB_GMS_URL="${DATAHUB_GMS_URL:-http://localhost:8080}"
export DATAHUB_SYSTEM_CLIENT_ID="${DATAHUB_SYSTEM_CLIENT_ID:-__datahub_system}"
export DATAHUB_SYSTEM_CLIENT_SECRET="${DATAHUB_SYSTEM_CLIENT_SECRET:-JohnSnowKnowsNothing}"
export E2E_TIMEOUT="${E2E_TIMEOUT:-120}"

port=${DATAHUB_GMS_URL##*:}; port=${port%%/*}
GMS_CONTAINER="${GMS_CONTAINER:-$(docker ps --filter "publish=$port" --format '{{.Names}}' | head -1)}"
[[ -n "$GMS_CONTAINER" ]] || { echo "no running container publishes port $port; set GMS_CONTAINER" >&2; exit 1; }
DOCKER_NETWORK="${DOCKER_NETWORK:-$(docker inspect "$GMS_CONTAINER" -f '{{range $k, $_ := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}' | grep -v control_plane | head -1)}"
DATAHUB_GMS_HOST="${DATAHUB_GMS_HOST:-datahub-gms}"
DATAHUB_GMS_PORT="${DATAHUB_GMS_PORT:-8080}"
KAFKA_BOOTSTRAP_SERVER="${KAFKA_BOOTSTRAP_SERVER:-broker:29092}"
SCHEMA_REGISTRY_URL="${SCHEMA_REGISTRY_URL:-http://$DATAHUB_GMS_HOST:$DATAHUB_GMS_PORT/schema-registry/api/}"
arch=$(uname -m); arch=${arch/x86_64/amd64}; arch=${arch/aarch64/arm64}
echo "GMS $DATAHUB_GMS_URL = $GMS_CONTAINER on $DOCKER_NETWORK; pipeline image $ACTIONS_IMAGE"

finish() {
  docker logs "$CONTAINER" > "$OUT/actions.log" 2>&1 || true
  [[ -z "${E2E_CAPTURE:-}" ]] || "$PYTHON" "$ROOT/e2e/extract_events.py" "$OUT/events" < "$OUT/actions.log" || true
  [[ -n "${KEEP_ACTIONS:-}" ]] || docker rm -f "$CONTAINER" > /dev/null 2>&1 || true
  echo "pipeline log: $OUT/actions.log"
}
docker rm -f "$CONTAINER" > /dev/null 2>&1 || true
trap finish EXIT

mounts=(-v "$ROOT/recipes/kafka.yaml:/etc/datahub/actions/conf/workflow-actions.yaml:ro"
        -v "$ROOT/datahub_workflow_actions:/etc/datahub/actions/plugins/datahub_workflow_actions:ro"
        -v "$OUT/failed:/var/datahub/actions/failed")
[[ -z "${E2E_CAPTURE:-}" ]] || mounts+=(-v "$ROOT/e2e/capture.yaml:/etc/datahub/actions/conf/capture.yaml:ro")

# Inside the container DATAHUB_TOKEN is the pipeline's token, not the harness's; the subshell
# scopes it and keeps every secret out of argv.
( export DATAHUB_TOKEN="${ACTIONS_TOKEN:-}"
  docker run -d --name "$CONTAINER" --network "$DOCKER_NETWORK" --platform "linux/$arch" \
    -e DATAHUB_GMS_HOST="$DATAHUB_GMS_HOST" -e DATAHUB_GMS_PORT="$DATAHUB_GMS_PORT" -e PLATFORM_EVENT_TOPIC_NAME \
    -e KAFKA_BOOTSTRAP_SERVER="$KAFKA_BOOTSTRAP_SERVER" -e SCHEMA_REGISTRY_URL="$SCHEMA_REGISTRY_URL" \
    -e DATAHUB_SYSTEM_CLIENT_ID -e DATAHUB_SYSTEM_CLIENT_SECRET -e DATAHUB_TOKEN \
    -e DATAHUB_ACTIONS_SYSTEM_CONFIGS_PATH=/etc/datahub/actions/system/disabled \
    -e PYTHONPATH=/etc/datahub/actions/plugins -e PYTHONUNBUFFERED=1 -e CAPTURE_RUN="-$(date +%s)" \
    "${mounts[@]}" "$ACTIONS_IMAGE" > /dev/null )

ready="Action Pipeline with name 'workflow-actions' is now running"
for _ in $(seq "${READY_TIMEOUT:-180}"); do
  docker logs "$CONTAINER" 2>&1 | grep -F "$ready" > /dev/null && break  # not -q: with pipefail an early exit fails the pipe
  [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" == true ]] || { docker logs "$CONTAINER"; echo "pipeline container exited" >&2; exit 1; }
  sleep 1
done
docker logs "$CONTAINER" 2>&1 | grep -F "$ready" > /dev/null || { docker logs "$CONTAINER" 2>&1 | tail -40; echo "pipeline not running after ${READY_TIMEOUT:-180}s" >&2; exit 1; }
docker logs "$CONTAINER" 2>&1 | grep -F 'workflow-actions ready' || true

# shellcheck disable=SC2086  # E2E_ARGS is a flag list on purpose; empty means no flags
"$PYTHON" "$ROOT/e2e/seed_and_run.py" ${E2E_ARGS---reject --cancel}
