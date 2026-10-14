# Testing a DataHub Actions image against a local docker quickstart

A runbook for running the stock `acryldata/datahub-actions` image (or an image derived from it)
next to a local DataHub that was started with docker compose, so an action can be exercised
end to end before it is deployed with the Helm chart. Nothing here is specific to this repository;
`e2e/run_local.sh` is one scripted instance of it.

## 1. Run the pipeline inside the quickstart's Docker network, not on the host

The quickstart's Kafka broker advertises `localhost:9092` to clients outside the network. That is
right for exactly one stack on the default port. A second stack on a remapped port (or any stack
whose broker port is not 9092) still advertises `localhost:9092`, so a host-side consumer that
bootstraps from the mapped port is redirected to the *wrong* broker, silently. Inside the network
the broker advertises its container name (`broker:29092`), which always resolves.

Find the pieces from the GMS container:

```bash
GMS=$(docker ps --filter publish=8080 --format '{{.Names}}' | head -1)   # the port you reach GMS on
docker inspect "$GMS" -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} aliases={{$v.Aliases}}{{"\n"}}{{end}}'
```

Pick the network that is not a control-plane network. Hostnames on it are the compose service
names and container hostnames: `datahub-gms` and `broker` both resolve on the OSS quickstart and on
the fork's `quickstartDebug`. Check from inside when in doubt:

```bash
docker run --rm --network <network> --entrypoint python acryldata/datahub-actions:<tag>-slim \
  -c 'import socket; print(socket.gethostbyname("datahub-gms"), socket.gethostbyname("broker"))'
```

## 2. Start the image the way the chart would

The image's `start.sh` waits for `http://$DATAHUB_GMS_HOST:$DATAHUB_GMS_PORT/health`, then starts
one pipeline per `*.yml`/`*.yaml` in `/etc/datahub/actions/conf` **and** one per file in
`DATAHUB_ACTIONS_SYSTEM_CONFIGS_PATH` (default `/etc/datahub/actions/system/conf`, which ships the
bundled executor and other system actions). Point the system path at a directory that does not
exist unless you want those too.

```bash
docker run -d --name my-action --network <network> \
  -e DATAHUB_GMS_HOST=datahub-gms -e DATAHUB_GMS_PORT=8080 \
  -e KAFKA_BOOTSTRAP_SERVER=broker:29092 \
  -e SCHEMA_REGISTRY_URL=http://datahub-gms:8080/schema-registry/api/ \
  -e DATAHUB_SYSTEM_CLIENT_ID -e DATAHUB_SYSTEM_CLIENT_SECRET \
  -e DATAHUB_ACTIONS_SYSTEM_CONFIGS_PATH=/etc/datahub/actions/system/disabled \
  -e PYTHONPATH=/etc/datahub/actions/plugins -e PYTHONUNBUFFERED=1 \
  -v "$PWD/recipe.yaml:/etc/datahub/actions/conf/recipe.yaml:ro" \
  -v "$PWD/my_package:/etc/datahub/actions/plugins/my_package:ro" \
  acryldata/datahub-actions:<tag>-slim
```

Notes on that command:

- **Schema registry.** Recent quickstarts run the schema registry inside GMS
  (`SCHEMA_REGISTRY_TYPE=INTERNAL`); the URL is GMS plus `/schema-registry/api/`. Older stacks
  have a separate `schema-registry:8081` container.
- **Credentials.** `-e NAME` with no value copies the variable from your shell, so secrets never
  appear in `docker inspect`'s command line or in `ps`. With no `token` in the recipe, the SDK
  falls back to `DATAHUB_SYSTEM_CLIENT_ID` / `DATAHUB_SYSTEM_CLIENT_SECRET` (the quickstart
  defaults are `__datahub_system` / `JohnSnowKnowsNothing`). To test the privileges a real service
  account will have, mint a PAT for that user and put it in the recipe's `datahub.token` instead.
- **Code without an image build.** A directory on `PYTHONPATH` is enough for an action referenced
  by import path (`action.type: my_package.module:MyAction`). This is also how a Kubernetes
  ConfigMap delivers it, so a flat package mounted read-only is the faithful test. Python silently
  skips writing `__pycache__` on a read-only mount.
- **Platform.** On Apple silicon pull the `linux/arm64` variant (`docker pull --platform
  linux/arm64 ...`) or pass `--platform`; the amd64 image runs under emulation and Kafka clients
  are noticeably slower there.
- **Writable paths.** A recipe option such as `failed_events_dir` must point at a directory the
  `datahub` user (uid 1000) can create; an unwritable path is fatal at startup. Bind-mount a host
  directory to keep the file inspectable.
- **stdout is block-buffered** when the container has no TTY. `logging` output appears immediately
  (stderr), `print()` output may not appear until the buffer fills or the process exits, and
  `docker rm -f` discards it. `PYTHONUNBUFFERED=1` fixes that; without it a debugging action that
  prints looks as if it never ran.

## 3. Know when the pipeline is actually up

```bash
docker logs -f my-action 2>&1 | grep -F "is now running"
```

`Action Pipeline with name '<name>' is now running.` is logged once per pipeline after the Kafka
consumer joined. Wait for it before producing test events, and treat a container that exits before
it as a startup failure (bad recipe, unwritable path, GMS unreachable). The pipeline name is the
consumer group id; keep it stable across restarts so offsets carry over, and give throwaway
capture pipelines a unique name so they start from the latest offset instead of replaying.

## 4. See what GMS actually emits

Do not reason about event shapes from source alone; capture them. The bundled `hello_world` action
prints every envelope that passes the recipe's filters as indented JSON:

```yaml
name: capture-${RUN_ID:-}
source: { type: kafka, config: { connection: { bootstrap: ${KAFKA_BOOTSTRAP_SERVER}, schema_registry_url: ${SCHEMA_REGISTRY_URL}, consumer_config: { auto.offset.reset: latest } }, topic_routes: { pe: PlatformEvent_v1 } } }
filters:
  - type: event_type
    config: { filter: { EntityChangeEvent_v1: { event: [ { entityType: actionRequest } ] } } }
action: { type: hello_world }
```

Mount it as a second file in the conf directory; the image runs it as a second pipeline in the same
container. `topic_routes` limits the source to the platform-event topic; without it the source
also subscribes to both MetadataChangeLog topics, which is a firehose.

To see raw records regardless of any filter, read the topic on the broker:

```bash
docker exec <broker> kafka-console-consumer --bootstrap-server broker:29092 \
  --topic PlatformEvent_v1 --from-beginning --timeout-ms 10000 \
  --property print.timestamp=true --property print.partition=true --property print.offset=true \
  | tr -c '[:print:]\n' '.' | grep '<urn fragment>'
```

Avro-encoded records still contain the JSON payload as plain text, so `grep` works. Compare
`CreateTime` with the pipeline log to tell "never emitted" from "emitted but dropped".

Whether a pipeline consumed a record is a committed-offset question:

```bash
docker exec <broker> kafka-consumer-groups --bootstrap-server broker:29092 --describe --group <pipeline name>
```

`LAG 0` with the record's offset below `CURRENT-OFFSET` means the pipeline read it; if nothing
happened, the recipe's filter dropped it or the action ignored it. Reproduce a filter decision
offline by instantiating the filter from the recipe spec and calling `matches()` on the captured
envelope.

## 5. Mind the indexes

Writes through GMS are visible immediately in entity reads (`get_aspect`, the entity endpoints)
but reach the search and graph indexes asynchronously, typically a second or two later. Anything
that resolves through those indexes lags behind: search-backed listings, relationship traversals,
and workflow actor resolvers that walk `OwnedBy` edges. A test that writes ownership and files a
request in the same breath can get an empty reviewer set and a request nobody can approve. Poll
the index the consumer uses (`/relationships` for graph edges, a search query for listings) before
the next step instead of sleeping a fixed time.

## 6. Feature flags and restarts

Features gated by GMS flags need the variables set on the GMS container, which means a container
restart, not a request header. On the fork's `quickstartDebug`, `scripts/dev/datahub-dev.sh env
set KEY=VALUE` followed by `env restart` does that per worktree; on the OSS quickstart edit the
compose env file and recreate the service. Verify through the API rather than trusting the file:

```bash
curl -s http://localhost:8080/api/graphql -H 'Authorization: Basic __datahub_system:JohnSnowKnowsNothing' \
  -H 'Content-Type: application/json' -d '{"query":"{ appConfig { appVersion featureFlags { actionWorkflowsEnabled actionWorkflowsV2Enabled } } }"}'
```

## 7. Keep the run reproducible

- Seed fixtures with fixed URNs and idempotent writes so a re-run starts from the same state;
  reset the aspects the action mutates (tags, structured properties) at the start of every run.
- Give every wait a deadline and print the subject's current state on timeout (for a workflow
  request: status, result, resolved assignees, current step), read from the entity aspects rather
  than a GraphQL resolver that may hide it behind authorization.
- Save the container log on exit, success or failure, and remove the container in the same trap.
- Under `set -o pipefail`, `docker logs ... | grep -q` fails once the log outgrows a pipe buffer:
  `grep -q` exits on the first match and `docker logs` dies of SIGPIPE. Use `grep ... >/dev/null`.
