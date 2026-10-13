"""Seed a dev DataHub, push the reference workflows, file one request per workflow, approve every
step and wait for the side effects. Usage:
  DATAHUB_GMS_URL=http://localhost:8080 DATAHUB_TOKEN=... python e2e/seed_and_run.py [--reject]
Requires a pipeline running recipes/kafka.yaml. --reject files one extra certification request
and rejects it, to exercise the pending-marker cleanup.
"""

import json
import os
import pathlib
import sys
import time
from typing import Any, Dict, List, Optional

from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
from datahub.metadata.schema_classes import (
    AuditStampClass,
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
ME = "urn:li:corpuser:datahub"
BUSINESS_OWNER = "urn:li:ownershipType:__system__business_owner"
PARENT = "urn:li:domain:e2e-parent"
DATASET = "urn:li:dataset:(urn:li:dataPlatform:databricks,my_catalog.my_schema.events,PROD)"
EXPIRY = "urn:li:structuredProperty:certification_expiry"
CERTIFIED, PENDING = "urn:li:tag:CERTIFIED", "urn:li:tag:certification-pending"
RUN = str(int(time.time()))
NEW_DOMAIN_NAME = f"E2E Payments {RUN}"
NEW_DOMAIN = f"urn:li:domain:e2e-parent.e2e-payments-{RUN}"

graph = DataHubGraph(DatahubClientConfig(server=os.environ["DATAHUB_GMS_URL"], token=os.environ["DATAHUB_TOKEN"]))
stamp = AuditStampClass(time=int(time.time() * 1000), actor=ME)


def emit(urn: str, aspect: Any) -> None:
    graph.emit(MetadataChangeProposalWrapper(entityUrn=urn, aspect=aspect))


def gql(query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return graph.execute_graphql(query, variables=variables)


def seed() -> None:
    for tid in ("CERTIFIED", "PII", "certification-pending"):
        emit(f"urn:li:tag:{tid}", TagPropertiesClass(name=tid))
    emit(
        EXPIRY,
        StructuredPropertyDefinitionClass(
            qualifiedName="certification_expiry",
            displayName="Certification expiry",
            valueType="urn:li:dataType:datahub.date",
            entityTypes=["urn:li:entityType:datahub.dataset"],
        ),
    )
    emit(PARENT, DomainPropertiesClass(name="E2E Parent", description="seeded"))
    emit(PARENT, OwnershipClass(owners=[OwnerClass(owner=ME, type=OwnershipTypeClass.CUSTOM, typeUrn=BUSINESS_OWNER)]))
    emit(DATASET, DatasetPropertiesClass(name="events"))
    emit(DATASET, SubTypesClass(typeNames=["Table"]))
    emit(DATASET, DomainsClass(domains=[PARENT]))  # the co-signer picker resolves DOMAIN_OWNERS_OF
    emit(DATASET, GlobalTagsClass(tags=[]))
    emit(DATASET, OwnershipClass(owners=[OwnerClass(owner=ME, type=OwnershipTypeClass.CUSTOM, typeUrn=BUSINESS_OWNER)]))


def push_workflows() -> None:
    mutation = "mutation($input: UpsertActionWorkflowInput!) { upsertActionWorkflow(input: $input) { urn } }"
    for f in ("domain-create.json", "domain-edit.json", "dataset-certification.json"):
        gql(mutation, {"input": json.loads((ROOT / "workflows" / f).read_text())})


def file_request(workflow: str, entity: Optional[str], fields: Dict[str, List[Dict[str, Any]]]) -> str:
    payload: Dict[str, Any] = {
        "workflowUrn": f"urn:li:actionWorkflow:{workflow}",
        "fields": [{"id": k, "values": v} for k, v in fields.items()],
    }
    if entity:
        payload["entityUrn"] = entity
    return gql(
        "mutation($input: CreateActionWorkflowFormRequestInput!) { createActionWorkflowFormRequest(input: $input) }",
        {"input": payload},
    )["createActionWorkflowFormRequest"]


def review(request_urn: str, result: str) -> None:
    gql(
        "mutation($input: ReviewActionWorkflowFormRequestInput!) { reviewActionWorkflowFormRequest(input: $input) }",
        {"input": {"urn": request_urn, "result": result, "comment": "e2e"}},
    )


def wait_for(done, what: str, seconds: int = 60) -> None:
    for _ in range(seconds):
        if done():
            print(f"OK {what}")
            return
        time.sleep(1)
    sys.exit(f"{what} never appeared")


def approve_until(request_urn: str, done, what: str, max_steps: int = 5) -> None:
    for _ in range(max_steps):
        if done():
            break
        try:
            review(request_urn, "ACCEPTED")
        except Exception as e:  # a review after the last step fails with "terminal state"
            print(f"review stopped: {e}")
        time.sleep(3)
    wait_for(done, what)


def has_tag(urn: str, tag: str) -> bool:
    tags = graph.get_aspect(urn, GlobalTagsClass)
    return tags is not None and any(t.tag == tag for t in tags.tags)


def expiry_set() -> bool:
    props = graph.get_aspect(DATASET, StructuredPropertiesClass)
    return props is not None and any(p.propertyUrn == EXPIRY and p.values for p in props.properties)


def description_is(urn: str, text: str) -> bool:
    props = graph.get_aspect(urn, DomainPropertiesClass)
    return props is not None and props.description == text


if __name__ == "__main__":
    seed()
    push_workflows()
    time.sleep(5)  # the workflow list is search-index backed
    s = lambda v: [{"stringValue": v}]  # noqa: E731

    # 1. domain-create
    req = file_request(
        "domain-create",
        PARENT,
        {"name": s(NEW_DOMAIN_NAME), "description": s("Created by e2e"), "parent_domain": s(PARENT), "owners": s(ME)},
    )
    approve_until(req, lambda: graph.get_aspect(NEW_DOMAIN, DomainPropertiesClass) is not None, f"domain {NEW_DOMAIN}")
    owners = graph.get_aspect(NEW_DOMAIN, OwnershipClass)
    assert owners and [o.owner for o in owners.owners] == [ME], owners

    # 2. domain-edit on the domain just created
    req = file_request("domain-edit", NEW_DOMAIN, {"description": s("Edited by e2e")})
    approve_until(req, lambda: description_is(NEW_DOMAIN, "Edited by e2e"), "domain description edited")

    # 3. dataset-certification: pending marker on submit, CERTIFIED + expiry on approval, marker gone
    req = file_request(
        "dataset-certification",
        DATASET,
        {
            "justification": s("Ready for certification: validated, documented and monitored by the owning team."),
            "certification_expiration": [{"numberValue": int(time.time() * 1000) + 30 * 86400000}],
            "co_signer_steward": s(ME),
        },
    )
    wait_for(lambda: has_tag(DATASET, PENDING), "pending marker applied")
    approve_until(
        req,
        lambda: has_tag(DATASET, CERTIFIED) and expiry_set() and not has_tag(DATASET, PENDING),
        "dataset certified, expiry set, marker removed",
    )

    if "--reject" in sys.argv:
        emit(DATASET, GlobalTagsClass(tags=[]))
        req = file_request(
            "dataset-certification",
            DATASET,
            {"justification": s("x" * 60), "certification_expiration": [{"numberValue": int(time.time() * 1000)}], "co_signer_steward": s(ME)},
        )
        wait_for(lambda: has_tag(DATASET, PENDING), "pending marker applied (reject run)")
        review(req, "REJECTED")
        wait_for(lambda: not has_tag(DATASET, PENDING) and not has_tag(DATASET, CERTIFIED), "marker removed after rejection, nothing certified")
