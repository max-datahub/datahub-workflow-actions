# Setup guide: workflow side effects on self-managed DataHub

This guide takes a running, self-managed DataHub Cloud instance to a working deployment of the
`datahub-workflow-actions` pipeline with the three reference workflows: domain creation, domain
change and dataset certification. It needs no image build; everything is YAML and Kubernetes
objects. Plan about an hour the first time.

## 1. What you are deploying

One DataHub Action, `WorkflowRouter`, running on the stock `acryldata/datahub-actions` image. It
reads `PlatformEvent_v1` from Kafka, reacts to Action Workflow request events (submitted, step
completed, completed, cancelled) and applies the side effects declared per workflow in
`recipes/kafka.yaml`: tags, structured properties, domain creation and domain edits. Every side
effect is idempotent and written through GraphQL mutations, so the service account needs only the
privileges your configuration uses and redelivered events do no harm.

```
request event on Kafka -> recipe filter -> router -> rules for (workflow, phase) -> primitives -> GMS
```

| Reference workflow | On submit | On approval | On rejection or cancel |
|---|---|---|---|
| `dataset-certification` | adds `certification-pending` | adds `CERTIFIED`, sets `certification_expiry`, removes the pending tag | removes the pending tag |
| `domain-create` | | creates the domain under the chosen parent with the chosen owners | |
| `domain-edit` | | applies the description, owner and parent changes to the launching domain | |

## 2. Prerequisites

| Need | Detail |
|---|---|
| DataHub | DataHub Cloud, self-managed, with Action Workflows v2 on. GMS environment: `ACTON_WORKFLOWS_ENABLED=true` (this spelling is the product's) and `ACTION_WORKFLOWS_V2_ENABLED=true`. Verify with GraphQL: `{ appConfig { featureFlags { actionWorkflowsEnabled actionWorkflowsV2Enabled } } }` must return both `true`. |
| Actions image | `acryldata/datahub-actions:<tag>-slim` for your DataHub release line. `v1.7.0.1-slim` is validated against DataHub v2.3. |
| Kafka | The actions pod must be able to read `PlatformEvent_v1`, with the same credentials as any existing actions pod (SASL or cloud IAM; see the commented block in `recipes/kafka.yaml`). |
| Kubernetes | `kubectl` and `helm` against the DataHub namespace, using the `acryl-datahub` chart with its `acryl-datahub-actions` subchart. |
| Admin access | A DataHub admin personal access token, to push the workflows and set up the service account. |
| Package | This repository at a release tag: `git clone --branch v0.2.0 https://github.com/max-datahub/datahub-workflow-actions.git`. |

## 3. Prepare the catalog

Create once, through the UI or the CLI. Keep the names, or rename them consistently in
`recipes/kafka.yaml` and `workflows/*.json`.

| Object | URN | Notes |
|---|---|---|
| Tag `CERTIFIED` | `urn:li:tag:CERTIFIED` | applied on approved certification |
| Tag `certification-pending` | `urn:li:tag:certification-pending` | applied while a certification request is open; the workflow's entrypoint filter hides the button while it is present |
| Structured property `certification_expiry` | `urn:li:structuredProperty:certification_expiry` | type Date, applies to Datasets, single value |
| Tag `PII` (optional) | `urn:li:tag:PII` | the certification workflow's third step, an Admin approval, opens only when the dataset carries it |

Reviewers are resolved from ownership, so a dataset must have owners, and belong to a domain that
has owners, before anyone requests its certification. Give the Admin role to at least one user if
the PII step can open.

## 4. Adapt and push the workflows

`workflows/*.json` are the definitions. Before pushing:

- `dataset-certification.json`: the entrypoint filter limits the "Request Certification" button to
  tables of one platform (`urn:li:dataPlatform:databricks` as shipped) without the `CERTIFIED`
  tag. Change the platform, or drop that filter clause, to match your estate.
- Steps resolve reviewers dynamically: owners of the launching entity, owners of its domain, then
  the Admin role for PII datasets. Adjust ownership types or quorum (`allOf` / `anyOf`) if your
  governance model differs.
- `domain-create.json` can be launched from a domain page (sub-domain) or from the home page
  (top-level domain); `domain-edit.json` from the domain page.

Push and verify in one step:

```bash
cd workflows
DATAHUB_URL=https://<your-datahub> DATAHUB_TOKEN=<admin token> ./push_workflows.sh
```

Each file pins its URN, so re-running updates in place. The script reads every workflow back from
the list and prints its steps and fields; the workflows then appear in the Action Workflows
section of the UI.

## 5. Create the service account

The pipeline writes as one DataHub user. Create a user named for the purpose (for example
`workflow-actions`), then grant it, through metadata and platform policies, exactly what the
reference configuration needs:

| Scope | Privileges |
|---|---|
| Datasets (or all assets the certification workflow may target) | Edit Tags, Edit Properties, View Entity Page |
| Domains | Edit Documentation, Edit Owners, View Entity Page |
| Platform | Create Domains, Manage Domains |
| Action requests | View Entity Page, so the pipeline can read a cancelled request. If your policy editor cannot target that resource type, grant View Entity Page on all resource types. |

The pipeline prints the exact list for your configuration at startup (section 7), so after any
change to `recipes/kafka.yaml` compare the log line with the policies. Generate a personal access
token for the user and store it:

```bash
kubectl create secret generic workflow-actions --from-literal=token=<service account token> -n <namespace>
```

Pilot shortcut: leave the secret out and the pipeline authenticates with the system client the
chart injects. That account has every privilege; use it to get going, not in production.

## 6. Deploy

Create the two ConfigMaps from the checkout. The code one makes the Python package available
without building an image.

```bash
kubectl create configmap workflow-actions-recipes --from-file=workflow-actions.yaml=recipes/kafka.yaml -n <namespace>
kubectl create configmap workflow-actions-code --from-file=datahub_workflow_actions/ -n <namespace>
```

Edit `recipes/kafka.yaml` first if Kafka needs authentication: uncomment and fill the
`consumer_config` block (SASL or cloud IAM) the same way your existing actions pod is configured.
The `${VAR}` placeholders (GMS host and port, Kafka bootstrap, schema registry, system client
credentials) are filled by the chart.

Then choose one of two ways to run it:

**A. Alongside the existing actions pod (recommended when that pod runs the bundled system
actions such as documentation propagation).** The image starts one pipeline per file in
`/etc/datahub/actions/conf`, so adding the recipe as a second file adds a second pipeline in the
same pod. Add to the `acryl-datahub-actions` values:

```yaml
acryl-datahub-actions:
  extraEnvs:
    - name: PYTHONPATH
      value: /etc/datahub/actions/plugins
    - name: DATAHUB_TOKEN
      valueFrom: { secretKeyRef: { name: workflow-actions, key: token, optional: true } }
  extraVolumes:
    - name: workflow-actions-recipes
      configMap: { name: workflow-actions-recipes }
    - name: workflow-actions-code
      configMap: { name: workflow-actions-code }
    - name: workflow-actions-failed
      persistentVolumeClaim: { claimName: workflow-actions-failed }
  extraVolumeMounts:
    - name: workflow-actions-recipes
      mountPath: /etc/datahub/actions/conf/workflow-actions.yaml
      subPath: workflow-actions.yaml
      readOnly: true
    - name: workflow-actions-code
      mountPath: /etc/datahub/actions/plugins/datahub_workflow_actions
      readOnly: true
    - name: workflow-actions-failed
      mountPath: /var/datahub/actions/failed
```

Merge these into any `extraEnvs`, `extraVolumes` and `extraVolumeMounts` you already set: Helm
replaces those lists, it does not append.

**B. A dedicated actions pod.** Use `helm/values.yaml` as shipped. It also sets
`DATAHUB_ACTIONS_SYSTEM_CONFIGS_PATH` to an empty directory so the image's bundled system actions
(including a second ingestion executor) do not start in this pod.

Either way, create the PVC named `workflow-actions-failed` (1 GiB, ReadWriteOnce) so events that
exhaust their retries survive pod restarts, then apply:

```bash
helm upgrade datahub acryl-datahub/acryl-datahub -n <namespace> --reuse-values -f <your values file>
kubectl rollout status deploy/<actions deployment> -n <namespace>
```

## 7. Verify

Startup, in the actions pod log:

```
workflow-actions ready for 3 workflow(s); privileges required: Create Domains, View Entity Page on action request; Create Domains, View Entity Page on domain (created); Edit Documentation, Edit Owners, Manage Domains, View Entity Page on domain (launching); Edit Properties, Edit Tags, View Entity Page on launching entity
Action Pipeline with name 'workflow-actions' is now running.
```

The first line is the privilege list to mirror in the policies. If the second line never appears,
the pod cannot reach Kafka or GMS; the lines before it say which.

End to end, on a dataset that has owners and sits in a domain with owners:

1. Request Certification from the dataset page. Within a few seconds the `certification-pending`
   tag appears on the dataset.
2. Approve as the owner, then as a domain owner. `CERTIFIED` appears, `certification_expiry` is
   set to the submitted date, and the pending tag is gone.
3. File another request and reject or cancel it. The pending tag is removed and nothing else
   changes.

Every action logs one line per request, with the request URN:

```bash
kubectl logs deploy/<actions deployment> -n <namespace> | grep workflow_actions
```

## 8. Operating notes

- Delivery is at-least-once; every primitive is idempotent. The consumer group is the recipe
  `name`; a new group starts at the latest offset, so requests completed before the first start are
  not processed.
- Bad input (a value that is not a URN, an unknown property, a domain name collision) is logged
  with the request URN and not retried. GMS or network failures are retried `retry_count` times,
  then the event is appended to `/var/datahub/actions/failed/workflow-actions/failed_events.log`
  and processing continues. After fixing the cause:

  ```bash
  kubectl exec deploy/<actions deployment> -n <namespace> -- \
    python -m datahub_workflow_actions.replay /var/datahub/actions/failed/workflow-actions/failed_events.log \
      -c /etc/datahub/actions/conf/workflow-actions.yaml
  ```

- To change what a workflow does, edit the `workflows:` block of `recipes/kafka.yaml` (the README
  documents the rule shape, the binding forms and each primitive), replace the recipes ConfigMap
  and restart the pod. To upgrade the library, replace the code ConfigMap and restart.
- Two behaviours of DataHub itself to know about:
  - Reviewers are resolved when a request is filed. A request filed on an entity whose owners or
    domain owners are missing, or filed by automation within a second or two of an ownership
    change, gets no reviewers and can only be cancelled and re-filed. Make sure ownership is in
    place before users request certification; automation should wait until the new ownership is
    visible in search before filing.
  - On current DataHub releases the cancellation event carries no workflow or entity context. The
    pipeline reads the cancelled request itself, which is why the service account needs View Entity
    Page on action requests. No action on your side.

## 9. Reference

- `README.md`: rule syntax, binding forms, primitives and their privileges, delivery semantics,
  how to add a primitive.
- `recipes/kafka.yaml`: the shipped configuration, including the Kafka authentication block.
- `helm/values.yaml`: the dedicated-pod values (option B).
- `docs/testing-the-actions-image-locally.md`: how to try the pipeline against a local docker
  quickstart before touching a cluster.
