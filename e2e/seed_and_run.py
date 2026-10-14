"""Exercise the three reference workflows end to end against a live DataHub.

Seeds the fixtures, pushes the workflows, files one request per workflow, approves every step and
waits for the side effects. Needs a pipeline running recipes/kafka.yaml against the same instance;
e2e/run_local.sh starts one next to a local docker quickstart.

  python e2e/seed_and_run.py [--reject] [--cancel]

Environment (every variable is optional):
  DATAHUB_GMS_URL   GMS base URL                                     http://localhost:8080
  DATAHUB_TOKEN     PAT of the user who files and approves. Unset: mint one for E2E_USER with the
                    system client (DATAHUB_SYSTEM_CLIENT_ID / _SECRET, quickstart defaults).
  E2E_USER          actor of the minted token                        urn:li:corpuser:datahub
  E2E_TIMEOUT       seconds to wait for each side effect             120

The user must be able to manage users and groups (the seed gives them the Admin role, which the
last certification step requires), create tags, domains and structured properties, and edit the
seeded dataset. Everything the seed writes is fixed-URN and idempotent, so re-runs are safe.
"""

import argparse
import json
import os
import pathlib
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
from datahub.ingestion.graph.openapi import RelationshipDirection
from datahub.metadata.schema_classes import (
    DatasetPropertiesClass,
    DomainPropertiesClass,
    DomainsClass,
    GlobalTagsClass,
    OwnerClass,
    OwnershipClass,
    OwnershipTypeClass,
    StructuredPropertiesClass,
    StructuredPropertyDefinitionClass,
    SubTypesClass,
    TagPropertiesClass,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ("domain-create", "domain-edit", "dataset-certification")
BUSINESS_OWNER = "urn:li:ownershipType:__system__business_owner"
ADMIN_ROLE = "urn:li:dataHubRole:Admin"
PARENT = "urn:li:domain:e2e-parent"
DATASET = "urn:li:dataset:(urn:li:dataPlatform:databricks,my_catalog.my_schema.events,PROD)"
EXPIRY = "urn:li:structuredProperty:certification_expiry"
CERTIFIED, PENDING = "urn:li:tag:CERTIFIED", "urn:li:tag:certification-pending"

CREATE_TOKEN = "mutation($input: CreateAccessTokenInput!) { createAccessToken(input: $input) { accessToken } }"
ASSIGN_ROLE = "mutation($input: BatchAssignRoleInput!) { batchAssignRole(input: $input) }"
UPSERT_WORKFLOW = "mutation($input: UpsertActionWorkflowInput!) { upsertActionWorkflow(input: $input) { urn } }"
LIST_WORKFLOWS = "{ listActionWorkflows(input: {start: 0, count: 1000}) { workflows { urn } } }"
FILE_REQUEST = "mutation($input: CreateActionWorkflowFormRequestInput!) { createActionWorkflowFormRequest(input: $input) }"
REVIEW_REQUEST = "mutation($input: ReviewActionWorkflowFormRequestInput!) { reviewActionWorkflowFormRequest(input: $input) }"
CANCEL_REQUEST = "mutation($input: CancelActionWorkflowFormRequestInput!) { cancelActionWorkflowFormRequest(input: $input) }"
FLAGS = "{ appConfig { featureFlags { actionWorkflowsEnabled actionWorkflowsV2Enabled } } }"


def single(value: Any) -> List[Dict[str, Any]]:
    return [{"stringValue": value}]


def mint_token(server: str, actor: str) -> str:
    """Dev instances only: a PAT for `actor`, minted with the system client. The SDK picks the
    DATAHUB_SYSTEM_CLIENT_* variables up by itself when no token is given."""
    os.environ.setdefault("DATAHUB_SYSTEM_CLIENT_ID", "__datahub_system")
    os.environ.setdefault("DATAHUB_SYSTEM_CLIENT_SECRET", "JohnSnowKnowsNothing")
    system = DataHubGraph(DatahubClientConfig(server=server))
    token_input = {"type": "PERSONAL", "actorUrn": actor, "duration": "ONE_HOUR", "name": "workflow-actions-e2e"}
    return system.execute_graphql(CREATE_TOKEN, variables={"input": token_input})["createAccessToken"]["accessToken"]


class Harness:
    def __init__(self, graph: DataHubGraph, timeout: int) -> None:
        self.graph, self.timeout = graph, timeout
        self.me: str = self.gql("{ me { corpUser { urn } } }")["me"]["corpUser"]["urn"]

    def gql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.graph.execute_graphql(query, variables=variables)

    def emit(self, urn: str, aspect: Any) -> None:
        self.graph.emit(MetadataChangeProposalWrapper(entityUrn=urn, aspect=aspect))

    # --- setup -------------------------------------------------------------------------------

    def check_flags(self) -> None:
        flags = self.gql(FLAGS)["appConfig"]["featureFlags"]
        if not all(flags.values()):
            sys.exit(f"Action Workflows v2 is off on GMS ({flags}); set ACTON_WORKFLOWS_ENABLED=true and ACTION_WORKFLOWS_V2_ENABLED=true")

    def seed(self) -> None:
        self.gql(ASSIGN_ROLE, {"input": {"roleUrn": ADMIN_ROLE, "actors": [self.me]}})
        for tid in ("CERTIFIED", "PII", "certification-pending"):
            self.emit(f"urn:li:tag:{tid}", TagPropertiesClass(name=tid))
        self.emit(
            EXPIRY,
            StructuredPropertyDefinitionClass(
                qualifiedName="certification_expiry",
                displayName="Certification expiry",
                valueType="urn:li:dataType:datahub.date",
                entityTypes=["urn:li:entityType:datahub.dataset"],
            ),
        )
        owned_by_me = OwnershipClass(owners=[OwnerClass(owner=self.me, type=OwnershipTypeClass.CUSTOM, typeUrn=BUSINESS_OWNER)])
        self.emit(PARENT, DomainPropertiesClass(name="E2E Parent", description="seeded"))
        self.emit(PARENT, owned_by_me)
        self.emit(DATASET, DatasetPropertiesClass(name="events"))
        self.emit(DATASET, SubTypesClass(typeNames=["Table"]))
        self.emit(DATASET, DomainsClass(domains=[PARENT]))  # the co-signer picker resolves DOMAIN_OWNERS_OF
        self.emit(DATASET, owned_by_me)
        self.reset_dataset()
        self.wait_for(lambda: self.owners_indexed(PARENT) and self.owners_indexed(DATASET), "seeded ownership indexed")

    def reset_dataset(self) -> None:
        self.emit(DATASET, GlobalTagsClass(tags=[]))
        self.emit(DATASET, StructuredPropertiesClass(properties=[]))

    def push_workflows(self) -> None:
        for name in WORKFLOWS:
            self.gql(UPSERT_WORKFLOW, {"input": json.loads((ROOT / "workflows" / f"{name}.json").read_text())})
        wanted = {f"urn:li:actionWorkflow:{name}" for name in WORKFLOWS}
        listed = lambda: wanted <= {w["urn"] for w in self.gql(LIST_WORKFLOWS)["listActionWorkflows"]["workflows"]}  # noqa: E731
        self.wait_for(listed, "workflows pushed and listed")  # the list is search-index backed

    # --- requests ----------------------------------------------------------------------------

    def file_request(self, workflow: str, entity: Optional[str], fields: Dict[str, List[Dict[str, Any]]]) -> str:
        payload: Dict[str, Any] = {
            "workflowUrn": f"urn:li:actionWorkflow:{workflow}",
            "fields": [{"id": k, "values": v} for k, v in fields.items()],
        }
        if entity:
            payload["entityUrn"] = entity
        urn = self.gql(FILE_REQUEST, {"input": payload})["createActionWorkflowFormRequest"]
        print(f"filed {workflow} -> {urn}")
        return urn

    def review(self, request: str, result: str) -> None:
        self.gql(REVIEW_REQUEST, {"input": {"urn": request, "result": result, "comment": "e2e"}})

    def cancel(self, request: str) -> None:
        self.gql(CANCEL_REQUEST, {"input": {"urn": request, "note": "e2e"}})

    def request_state(self, request: str) -> str:
        aspects = self.graph.get_entity_raw(request, aspects=["actionRequestStatus", "actionRequestInfo"])["aspects"]
        status = aspects.get("actionRequestStatus", {}).get("value", {})
        info = aspects.get("actionRequestInfo", {}).get("value", {})
        step = ((info.get("params") or {}).get("workflowFormRequest") or {}).get("stepState")
        return f"{status.get('status')}/{status.get('result')}, assignees {info.get('assignedUsers')}, step {step}"

    def owners_indexed(self, urn: str) -> bool:
        """v2 resolves OWNERS_OF through the graph index, which trails the ownership aspect by a
        second or two; a request filed before the edge lands gets no assignees and cannot be approved."""
        related = self.graph.get_related_entities(urn, ["OwnedBy"], RelationshipDirection.OUTGOING)
        return self.me in {r.urn for r in related}

    def wait_for(self, done: Callable[[], bool], what: str, request: Optional[str] = None) -> None:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if done():
                print(f"OK {what}")
                return
            time.sleep(1)
        state = f"; request {request} is {self.request_state(request)}" if request else ""
        sys.exit(f"TIMEOUT after {self.timeout}s: {what}{state}. Is a pipeline running recipes/kafka.yaml against this instance?")

    def approve_until(self, request: str, done: Callable[[], bool], what: str) -> None:
        """Accept step after step until the side effect shows up (or the request is terminal)."""
        deadline, seen = time.time() + self.timeout, set()
        while time.time() < deadline and not done():
            if self.request_state(request).startswith("COMPLETED"):
                break
            try:
                self.review(request, "ACCEPTED")
            except Exception as e:  # the next step may not be assigned yet
                if str(e)[:200] not in seen:
                    seen.add(str(e)[:200])
                    print(f"  review deferred: {str(e)[:200]}")
            time.sleep(2)
        self.wait_for(done, what, request)

    # --- assertions --------------------------------------------------------------------------

    def has_tag(self, urn: str, tag: str) -> bool:
        tags = self.graph.get_aspect(urn, GlobalTagsClass)
        return tags is not None and any(t.tag == tag for t in tags.tags)

    def expiry_set(self) -> bool:
        props = self.graph.get_aspect(DATASET, StructuredPropertiesClass)
        return props is not None and any(p.propertyUrn == EXPIRY and p.values for p in props.properties)

    def description_is(self, urn: str, text: str) -> bool:
        props = self.graph.get_aspect(urn, DomainPropertiesClass)
        return props is not None and props.description == text

    # --- scenarios ---------------------------------------------------------------------------

    def domain_create(self) -> str:
        run = str(int(time.time()))
        domain = f"urn:li:domain:e2e-parent.e2e-payments-{run}"
        fields = {"name": single(f"E2E Payments {run}"), "description": single("Created by e2e"), "parent_domain": single(PARENT), "owners": single(self.me)}
        req = self.file_request("domain-create", PARENT, fields)
        self.approve_until(req, lambda: self.graph.get_aspect(domain, DomainPropertiesClass) is not None, f"domain {domain} created")
        owners = self.graph.get_aspect(domain, OwnershipClass)
        assert owners and [o.owner for o in owners.owners] == [self.me], owners
        self.wait_for(lambda: self.owners_indexed(domain), "new domain's ownership indexed")
        return domain

    def domain_edit(self, domain: str) -> None:
        req = self.file_request("domain-edit", domain, {"description": single("Edited by e2e")})
        self.approve_until(req, lambda: self.description_is(domain, "Edited by e2e"), "domain description edited")

    def certification(self, outcome: str) -> None:
        """outcome: ACCEPTED (marker on submit, CERTIFIED + expiry on approval, marker gone),
        REJECTED or CANCELLED (marker on submit, marker gone, nothing certified)."""
        self.reset_dataset()
        fields = {
            "justification": single("Ready for certification: validated, documented and monitored by the owning team."),
            "certification_expiration": [{"numberValue": int(time.time() * 1000) + 30 * 86400000}],
            "co_signer_steward": single(self.me),
        }
        req = self.file_request("dataset-certification", DATASET, fields)
        self.wait_for(lambda: self.has_tag(DATASET, PENDING), f"pending marker applied ({outcome})", req)
        if outcome == "ACCEPTED":
            certified = lambda: self.has_tag(DATASET, CERTIFIED) and self.expiry_set() and not self.has_tag(DATASET, PENDING)  # noqa: E731
            self.approve_until(req, certified, "dataset certified, expiry set, marker removed")
            return
        self.review(req, "REJECTED") if outcome == "REJECTED" else self.cancel(req)
        untouched = lambda: not self.has_tag(DATASET, PENDING) and not self.has_tag(DATASET, CERTIFIED)  # noqa: E731
        self.wait_for(untouched, f"marker removed after {outcome.lower()}, nothing certified", req)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reject", action="store_true", help="also file a certification request and reject it")
    ap.add_argument("--cancel", action="store_true", help="also file a certification request and cancel it")
    args = ap.parse_args()

    server = os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080")
    token = os.environ.get("DATAHUB_TOKEN") or mint_token(server, os.environ.get("E2E_USER", "urn:li:corpuser:datahub"))
    h = Harness(DataHubGraph(DatahubClientConfig(server=server, token=token)), int(os.environ.get("E2E_TIMEOUT", "120")))
    print(f"GMS {server}, acting as {h.me}, {h.timeout}s per side effect")

    h.check_flags()
    h.seed()
    h.push_workflows()
    domain = h.domain_create()
    h.domain_edit(domain)
    h.certification("ACCEPTED")
    if args.reject:
        h.certification("REJECTED")
    if args.cancel:
        h.certification("CANCELLED")
    print("E2E PASS")


if __name__ == "__main__":
    main()
