# CLAUDE.md

Read this before changing anything. The spec this repo implements is in `out/superpowers/specs/`
(not committed); this file is the durable summary of the decisions that matter.

## What this repo is

One DataHub Action (`WorkflowRouter`) that runs on the stock OSS `acryldata/datahub-actions`
image, parses each `EntityChangeEvent_v1` once, and applies the catalog side effects a customer
declared per Action Workflow in YAML. Side effects are built from small idempotent **primitives**
that write through GraphQL mutations so the service account needs granular privileges, never
Admin. Customers add primitives by import path without forking.

Flow: Kafka `PlatformEvent_v1` → recipe filter (workflow events only) → `router.act` →
`events.parse` → rules for (workflow id, phase) → `binding.resolve` → `Primitive.run` → GMS.

## Non-negotiables

- **Flat package.** No sub-packages under `datahub_workflow_actions/`. The directory is mounted
  from a single Kubernetes ConfigMap onto `PYTHONPATH` for air-gapped clusters; ConfigMap keys
  cannot nest. New primitives are `p_<name>.py` at the top level.
- **Write through GraphQL mutations.** The REST/MCP ingest path authorises only the coarse
  `Edit Entity` / `Create Entity` privilege per URN (fork `EntityAuthorizationUtils.isAPIAuthorizedIngest`);
  granular privileges (Edit Tags, Edit Owners, Create Domains, …) exist only behind GraphQL
  resolvers. An MCP write is allowed only when no mutation exists (today: domain rename), and the
  primitive must then declare `Edit Entity`.
- **Every primitive is idempotent.** Delivery is at-least-once and `replay.py` re-feeds failed
  events. A second run with the same event must be a no-op or a skip, never a duplicate or an
  error. Create primitives are create-or-skip, never create-or-update; updates get their own
  primitive with their own target and privileges (see `p_create_domain.py` vs `p_update_domain.py`).
- **`PermanentError` vs propagate.** Raise `PermanentError` for anything a retry cannot fix (bad
  form value, wrong entity type, collision, GraphQL-level rejection). Let `OperationalError` and
  other exceptions propagate so the framework retries. `primitive.gql` already does the split.
- **Declare privileges.** `required_privileges` uses DataHub privilege display names. Override
  `privileges()` when they depend on which params are bound (see `UpdateDomain`). The router prints
  the union at startup; operators build their policy from it.
- **Binding stays at three forms:** literal, `field:<id>`, `event:<param>`. No defaults, no
  transforms, no conditionals, no `result:` chaining. Value coercion that depends on the target
  (dates for structured properties) lives in the primitive.
- **Params are pydantic.** Subclass `Primitive` (a `ConfigModel`, extras forbidden). Use `FirstStr`
  for single-value params and `StrList` for multi-value params so bound lists and literal scalars
  both work. Validate at startup; a bad recipe must never start consuming.
- **Rule keys must not be YAML booleans.** The phase key is `phase`, not `on`: PyYAML (YAML 1.1)
  parses a bare `on`, `off`, `yes`, `no`, `y`, `n` as booleans. Never introduce such a key.
- **Loggers are `datahub_actions.workflow_actions.<name>`** via `primitive.logger_for`. datahub's
  run-log filter drops INFO from any other namespace.
- **No customer names, hostnames, ticket ids or customer URNs** anywhere. This repo will be public.
- **Runtime dependency is `acryl-datahub-actions` only.**

## Adding a primitive

1. `datahub_workflow_actions/p_<name>.py`: a `Primitive` subclass with pydantic params,
   `required_privileges`, `target` ("launching entity" unless it creates or targets something
   else), and `run(graph, event)`. One GraphQL mutation string per call, module-level constant.
2. Register the builtin name in `registry.py` `BUILTINS`.
3. `tests/test_p_<name>.py` against `tests.conftest.FakeGraph`: happy path with the exact
   mutation variables, the idempotent second run, every `PermanentError` branch, the privileges.
4. README: a row in the primitives table (name, params, mutation, privilege, second-run semantics).
5. If the primitive implies a new use case, add a sample workflow JSON under `workflows/` and a
   rule in `recipes/kafka.yaml`; `tests/test_shipped_config.py` checks the bindings against the
   form.
6. If the primitive targets a new kind of entity, pick a `target` string that reads well in the
   manifest.

## Event contract

All workflow events are `EntityChangeEvent_v1` with `entityType=actionRequest`,
`category=LIFECYCLE`, `parameters.actionRequestType=WORKFLOW_FORM_REQUEST`.

| Phase | ECE `operation` | distinguishing parameters |
|---|---|---|
| CREATED | `CREATE` | no `operation` param |
| STEP | `MODIFY` | `stepId`, `stepResult` |
| COMPLETED | `COMPLETED` | `operation=COMPLETE`, `result=ACCEPTED\|REJECTED` |
| CANCELLED | `MODIFY` | `operation=CANCELLED`, `cancelledBy`, `note?` |

Always present: `workflowUrn`/`workflowId`, `entityUrn` (absent for HOME-launched workflows),
`actorUrn`, `fields` as a **JSON string** `{fieldId: [value, …]}` where every value is a string
(DATE fields are epoch millis, often as a Java double like `"1.793367303267E12"`; URN fields are
URN strings). Not in the event: approver notes and per-step decisions (they live in the request's
`actionRequestInfo` aspect). Fixtures captured from a live instance are in `tests/fixtures/`.

## Framework gotchas (datahub-actions)

- One recipe = one action = one consumer group. N actions means N consumers; that is why the
  router exists.
- The Kafka source subscribes to both MetadataChangeLog topics unless `topic_routes: {pe: …}`
  restricts it. Never drop that line from the recipe.
- `max.poll.interval.ms` defaults to 10 s; the recipe sets 300 s because primitives make several
  GMS calls.
- `failure_mode: CONTINUE` (default) acks an event that exhausted retries after writing it to
  `failed_events_dir`. `THROW` stops the pipeline thread while the pod stays Running.
- The Cloud polling source persists offsets only on clean shutdown; a crash replays recent events.
- `handle_pe` silently drops platform events that are not `entityChangeEvent` /
  `relationshipChangeEvent`; Cloud also emits `notificationRequest` events on the same topic.
- `action.type` with `.` or `:` is imported directly (`datahub.ingestion.api.registry.import_path`);
  no entry point is needed, which is what lets the ConfigMap path work.
- The stock image has no runtime pip hook; code arrives by derived image, start-command pip
  install, or ConfigMap mount. `DATAHUB_ACTIONS_SYSTEM_CONFIGS_PATH` must point at an empty
  directory or the bundled executor starts too.
- `ConfigModel` hides input values in pydantic error messages; config errors name the location
  (`workflows.<key>.<index>.<field>`) and the allowed values, not the offending value.

## Commands

```bash
asdf set python 3.11.13 && python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest                                                     # unit tests, no network
python e2e/seed_and_run.py                                 # manual, needs DATAHUB_GMS_URL + DATAHUB_TOKEN
python -m datahub_workflow_actions.replay <log> -c <recipe>
(cd workflows && DATAHUB_URL=… DATAHUB_TOKEN=… ./push_workflows.sh)
docker build --platform linux/amd64 --build-arg DATAHUB_ACTIONS_TAG=<tag>-slim -t <img> .
```

## Deferred (decided, do not re-litigate)

Not in v1: `add_terms`, `set_owners`, `set_domain` / `set_data_product` / `set_application`,
`set_deprecation`, `set_lifecycle_stage`, `update_description`, `create_data_product`,
`create_glossary_term` / `create_glossary_node`, `create_document`, `stamp_request`, access
markers, workflow chaining, incidents, the `result:<step>` binding form, a privilege preflight
command, per-primitive Prometheus counters. Each follows the "Adding a primitive" checklist when
a customer needs it.
