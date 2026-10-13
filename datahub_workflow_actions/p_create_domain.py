"""create_domain: create a domain from form values via createDomain. Create-or-skip, never upsert.

The id is derived from the name (and the parent id) so a redelivered event finds the domain it
already created and skips. A different domain at that id is a collision and a permanent error;
editing an existing domain is update_domain's job.
"""

import re
import unicodedata
from typing import ClassVar, Dict, List, Optional

from datahub.ingestion.graph.client import DataHubGraph
from datahub.metadata.schema_classes import DomainPropertiesClass

from datahub_workflow_actions.events import WorkflowEvent
from datahub_workflow_actions.primitive import FirstStr, PermanentError, Primitive, StrList, gql, logger_for, require_urn

logger = logger_for("create_domain")

CREATE_DOMAIN = "mutation($input: CreateDomainInput!) { createDomain(input: $input) }"
DOMAIN = "urn:li:domain:"
DEFAULT_OWNERSHIP_TYPE = "urn:li:ownershipType:__system__business_owner"
OWNER_ENTITY_TYPES = {"urn:li:corpuser:": "CORP_USER", "urn:li:corpGroup:": "CORP_GROUP"}


def slug(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")


def domain_id(name: str, parent_urn: Optional[str]) -> str:
    name_slug = slug(name)
    if not name_slug:
        raise PermanentError(f"domain name {name!r} has no characters usable in an id")
    # The parent id is already unique; re-slugging it could make distinct parents collide.
    return f"{parent_urn[len(DOMAIN):]}.{name_slug}" if parent_urn else name_slug


def owner_input(urn: str, ownership_type: str) -> Dict[str, str]:
    for prefix, entity_type in OWNER_ENTITY_TYPES.items():
        if urn.startswith(prefix):
            return {"ownerUrn": urn, "ownerEntityType": entity_type, "ownershipTypeUrn": ownership_type}
    raise PermanentError(f"owner {urn!r} is not a user or group")


class CreateDomain(Primitive):
    required_privileges: ClassVar[List[str]] = ["Create Domains"]
    target: ClassVar[str] = "domain (created)"
    name: FirstStr
    description: FirstStr = None
    parent: FirstStr = None
    owners: StrList = []
    ownership_type: str = DEFAULT_OWNERSHIP_TYPE

    def run(self, graph: DataHubGraph, event: WorkflowEvent) -> None:
        name = (self.name or "").strip()
        if not name:
            raise PermanentError("the form carried no domain name")
        parent = require_urn(self.parent, DOMAIN, "parent") if self.parent else None
        owners = [owner_input(o, self.ownership_type) for o in self.owners]
        new_id = domain_id(name, parent)
        urn = DOMAIN + new_id

        existing = graph.get_aspect(urn, DomainPropertiesClass)
        if existing is not None:
            if existing.name.strip().lower() == name.lower():
                logger.info("Domain %s already exists; skipping (request %s)", urn, event.request_urn)
                return
            raise PermanentError(f"{name!r} maps to {urn}, which already holds the domain {existing.name!r}")

        gql(
            graph,
            CREATE_DOMAIN,
            {"input": {"id": new_id, "name": name, "description": self.description, "parentDomain": parent, "owners": owners}},
        )
        logger.info("Created domain %s with %d owner(s) (request %s)", urn, len(owners), event.request_urn)
