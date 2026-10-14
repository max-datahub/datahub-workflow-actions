# datahub-workflow-actions

One DataHub Action that applies catalog side effects when [Action Workflow](https://docs.datahub.com/docs/managed-datahub/workflows/action-workflows) requests are submitted, approved, rejected or cancelled. Runs on the stock `acryldata/datahub-actions` image. You declare what should happen per workflow in YAML; the primitives are idempotent and write through GraphQL, so the service account needs only the privileges your configuration uses.

```yaml
action:
  type: datahub_workflow_actions.router:WorkflowRouter
  config:
    workflows:
      dataset-certification:
        - phase: COMPLETED           # CREATED | STEP | COMPLETED | CANCELLED
          when: ACCEPTED             # ACCEPTED | REJECTED | ANY (COMPLETED only, default ACCEPTED)
          do:
            - add_tags: { entity: event:entityUrn, tags: ["urn:li:tag:CERTIFIED"] }
            - set_structured_properties:
                entity: event:entityUrn
                values: { urn:li:structuredProperty:certification_expiry: field:certification_expiration }
```

Full recipes: [`recipes/kafka.yaml`](recipes/kafka.yaml) (self-managed) and [`recipes/cloud.yaml`](recipes/cloud.yaml) (DataHub Cloud). Reference workflows: [`workflows/`](workflows/).

A workflow key maps to a list of rules so one workflow can react to several phases. `step_id` narrows a `STEP` rule to one approval step. The key is called `phase`, not `on`, because YAML parses a bare `on` as a boolean.

## Binding values

| Form | Resolves to |
|---|---|
| any other value | itself |
| `field:<id>` | the submitted values of that form field, always a list (missing → `[]`) |
| `event:<param>` | `entityUrn` (launching entity), `actorUrn` (requester), `requestUrn`, `workflowId`, `stepId`, `result` |

A single-value param takes the first element of a list; a multi-value param wraps a scalar.

## Primitives

| Name | Params | Writes via | Privilege | Second run |
|---|---|---|---|---|
| `add_tags` | `entity`, `tags[]` | `batchAddTags` | Edit Tags | no-op (set union) |
| `remove_tags` | `entity`, `tags[]` | `batchRemoveTags` | Edit Tags | no-op |
| `set_structured_properties` | `entity`, `values: {propertyUrn: value(s)}` | `upsertStructuredProperties` | Edit Properties | no-op (per-property replace; dates are coerced from the form's epoch millis) |
| `create_domain` | `name`, `description?`, `parent?`, `owners[]?`, `ownership_type?` | `createDomain` (id = slug of name under the parent) | Create Domains | skip if the same domain exists; error if the id holds a different domain |
| `update_domain` | `entity`, `description?`, `owners[]?`, `parent?`, `name?`, `ownership_type?` | `updateDescription`, owner batch mutations, `moveDomain`, MCP for rename | Edit Documentation / Edit Owners / Manage Domains / Edit Entity, per field given | no-op; only given fields are written |

Every primitive also reads before it writes, so the service account needs View Entity Page on the targets. The router logs the full list at startup:

```
workflow-actions ready for 3 workflow(s); privileges required: Create Domains, View Entity Page on domain (created); Edit Documentation, Edit Owners, Manage Domains, View Entity Page on domain (launching); Edit Properties, Edit Tags, View Entity Page on launching entity
```

Custom primitive: subclass `datahub_workflow_actions.primitive.Primitive` anywhere on `PYTHONPATH` and reference it by import path (`- my_company.wf:NotifyCmdb: { endpoint: ... }`, or a bare `- my_company.wf:NotifyCmdb` when it takes no params). See `CLAUDE.md` for the contract.

## Pending-marker pattern

Tag the asset when a request is submitted and clear it when the request ends, and exclude the tag in the workflow's entrypoint filter, so the button disappears while a request is open:

```yaml
- phase: CREATED
  do: [ { add_tags: { entity: event:entityUrn, tags: ["urn:li:tag:certification-pending"] } } ]
- phase: COMPLETED
  when: ANY
  do: [ { remove_tags: { entity: event:entityUrn, tags: ["urn:li:tag:certification-pending"] } } ]
- phase: CANCELLED
  do: [ { remove_tags: { entity: event:entityUrn, tags: ["urn:li:tag:certification-pending"] } } ]
```

## Deploy on self-managed DataHub (stock image)

Three ways to get the code into the actions pod, all from the same directory:

| Path | How |
|---|---|
| ConfigMap (air-gapped) | `kubectl create configmap workflow-actions-code --from-file=datahub_workflow_actions/`, mounted at `/etc/datahub/actions/plugins/datahub_workflow_actions` with `PYTHONPATH=/etc/datahub/actions/plugins` (see `helm/values.yaml`) |
| Derived image | `docker build --platform linux/amd64 --build-arg DATAHUB_ACTIONS_TAG=<datahub-version>-slim -t <registry>/datahub-workflow-actions:<version> .` |
| Install at start | container command `pip install datahub-workflow-actions==<version> && exec /start_datahub_actions.sh` |

Then mount `recipes/kafka.yaml` at `/etc/datahub/actions/conf/` and set `DATAHUB_ACTIONS_SYSTEM_CONFIGS_PATH` to an empty directory (the bundled executor otherwise competes with the real one). `helm/values.yaml` has the subchart snippet.

**Service account:** create a DataHub user for the action, grant exactly the privileges the startup log lists through a metadata policy, and put its token in the `workflow-actions` Secret. Without a token the action uses the system client credentials the chart injects, which are Admin.

## Deploy on DataHub Cloud

Create an Ingestion Source with the `source:` block from `recipes/cloud.yaml`, and add the package to Extra Pip Libraries (Step 5, Advanced):

```json
["/datahub-integrations-service", "https://github.com/<org>/datahub-workflow-actions/archive/refs/tags/v0.1.0.zip"]
```

## Delivery semantics

- At-least-once. The Kafka offset commits after the action returns; every primitive is idempotent.
- The consumer group is the recipe `name`. A new group starts at the latest offset, so requests approved before the first start are not processed.
- An event that still fails after `retry_count` is appended to `<failed_events_dir>/<name>/failed_events.log` and processing continues. Mount that directory on a PVC. After fixing the cause:

  ```bash
  python -m datahub_workflow_actions.replay /var/datahub/actions/failed/workflow-actions/failed_events.log -c /etc/datahub/actions/conf/workflow-actions.yaml
  ```

- Bad form values (not a URN, unknown property, name collision) are logged with the request URN and **not** retried; GMS or network failures are.
- Do not set `failure_mode: THROW`: it stops the pipeline thread while the pod stays Running.

## Push the reference workflows

```bash
cd workflows && DATAHUB_URL=https://<instance> DATAHUB_TOKEN=<token> ./push_workflows.sh
```

Each file pins its URN, so re-running updates the existing workflow. Replace the placeholder URNs (`urn:li:tag:CERTIFIED`, `urn:li:tag:PII`, `urn:li:structuredProperty:certification_expiry`) with your own before pushing.

## End-to-end check against a local DataHub

`e2e/run_local.sh` runs the stock actions image next to a local docker quickstart (or the fork's
quickstartDebug) with this checkout mounted the way the Helm values mount the ConfigMaps, then runs
`e2e/seed_and_run.py`: it seeds the fixtures, pushes the three reference workflows, files a request
per workflow, approves every step and asserts the side effects, plus a rejected and a cancelled
certification request. GMS must have Action Workflows v2 on (`ACTON_WORKFLOWS_ENABLED=true` and
`ACTION_WORKFLOWS_V2_ENABLED=true`; the harness checks and says so otherwise).

```bash
DATAHUB_GMS_URL=http://localhost:8080 e2e/run_local.sh    # last line: E2E PASS
E2E_CAPTURE=1 e2e/run_local.sh                            # also writes every workflow event to e2e/out/events/
```

Every knob is an environment variable; the header of `e2e/run_local.sh` lists them (GMS URL and
container, Docker network, Kafka bootstrap, image tag, tokens, timeouts, harness flags). Without
`DATAHUB_TOKEN` the harness mints a one-hour PAT for `urn:li:corpuser:datahub` through the quickstart
system client. The pipeline log and any failed events land in `e2e/out/`. The harness alone
(`python e2e/seed_and_run.py --reject --cancel`) works against any instance that already runs
`recipes/kafka.yaml`. The generic recipe for running the stock image against any local quickstart,
including how to capture raw events and read consumer offsets, is in
[docs/testing-the-actions-image-locally.md](docs/testing-the-actions-image-locally.md).

## Develop

```bash
asdf set python 3.11.13
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```
