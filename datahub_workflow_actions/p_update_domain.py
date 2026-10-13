"""update_domain: change description, owners, parent or name of the launching domain.

Each field goes through the mutation that carries its granular privilege and is skipped when the
catalog already holds the value, so a second run is a no-op. Owners are replaced within the
configured ownership type only; owners of other types are untouched. Rename has no GraphQL
mutation, so it is the one MCP write in this package and needs Edit Entity on the domain.
"""

from typing import ClassVar, List

from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.ingestion.graph.client import DataHubGraph
from datahub.metadata.schema_classes import DomainPropertiesClass, OwnershipClass
from pydantic import model_validator

from datahub_workflow_actions.events import WorkflowEvent
from datahub_workflow_actions.p_create_domain import DEFAULT_OWNERSHIP_TYPE, DOMAIN, owner_input
from datahub_workflow_actions.primitive import FirstStr, PermanentError, Primitive, StrList, gql, logger_for, require_urn

logger = logger_for("update_domain")

UPDATE_DESCRIPTION = "mutation($input: DescriptionUpdateInput!) { updateDescription(input: $input) }"
BATCH_ADD_OWNERS = "mutation($input: BatchAddOwnersInput!) { batchAddOwners(input: $input) }"
BATCH_REMOVE_OWNERS = "mutation($input: BatchRemoveOwnersInput!) { batchRemoveOwners(input: $input) }"
MOVE_DOMAIN = "mutation($input: MoveDomainInput!) { moveDomain(input: $input) }"


class UpdateDomain(Primitive):
    target: ClassVar[str] = "domain (launching)"
    entity: FirstStr
    description: FirstStr = None
    owners: StrList = []
    parent: FirstStr = None
    name: FirstStr = None
    ownership_type: str = DEFAULT_OWNERSHIP_TYPE

    @model_validator(mode="after")
    def _something_to_write(self) -> "UpdateDomain":
        if self.description is None and not self.owners and self.parent is None and self.name is None:
            raise ValueError("nothing to update: give at least one of description, owners, parent, name")
        return self

    def privileges(self) -> List[str]:
        privs = []
        if self.description is not None:
            privs.append("Edit Documentation")
        if self.owners:
            privs.append("Edit Owners")
        if self.parent is not None:
            privs.append("Manage Domains")
        if self.name is not None:
            privs.append("Edit Entity")
        return privs

    def run(self, graph: DataHubGraph, event: WorkflowEvent) -> None:
        entity = require_urn(self.entity, DOMAIN, "entity")
        props = graph.get_aspect(entity, DomainPropertiesClass)
        if props is None:
            raise PermanentError(f"domain {entity} does not exist")
        resources = [{"resourceUrn": entity}]

        if self.description is not None and self.description != (props.description or ""):
            gql(graph, UPDATE_DESCRIPTION, {"input": {"description": self.description, "resourceUrn": entity}})
            logger.info("Updated description of %s (request %s)", entity, event.request_urn)

        if self.owners:
            wanted = {o for o in self.owners if owner_input(o, self.ownership_type)}
            current = graph.get_aspect(entity, OwnershipClass)
            have = {o.owner for o in (current.owners if current else []) if o.typeUrn == self.ownership_type}
            remove, add = sorted(have - wanted), sorted(wanted - have)
            if remove:
                gql(graph, BATCH_REMOVE_OWNERS, {"input": {"ownerUrns": remove, "ownershipTypeUrn": self.ownership_type, "resources": resources}})
            if add:
                owners = [owner_input(o, self.ownership_type) for o in add]
                gql(graph, BATCH_ADD_OWNERS, {"input": {"owners": owners, "ownershipTypeUrn": self.ownership_type, "resources": resources}})
            if remove or add:
                logger.info("Owners of %s: removed %s, added %s (request %s)", entity, remove, add, event.request_urn)

        if self.parent is not None:
            parent = require_urn(self.parent, DOMAIN, "parent")
            if parent != props.parentDomain:
                gql(graph, MOVE_DOMAIN, {"input": {"resourceUrn": entity, "parentDomain": parent}})
                logger.info("Moved %s under %s (request %s)", entity, parent, event.request_urn)

        if self.name is not None:
            name = self.name.strip()
            if name and name != props.name:
                props.name = name
                graph.emit(MetadataChangeProposalWrapper(entityUrn=entity, aspect=props))
                logger.info("Renamed %s to %r (request %s)", entity, name, event.request_urn)
