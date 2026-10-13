"""add_tags: add tags to one asset via batchAddTags (set union, so re-adding is a no-op)."""

from typing import ClassVar, List

from datahub.ingestion.graph.client import DataHubGraph

from datahub_workflow_actions.events import WorkflowEvent
from datahub_workflow_actions.primitive import FirstStr, Primitive, StrList, gql, logger_for, require_urn

logger = logger_for("add_tags")

BATCH_ADD_TAGS = "mutation($input: BatchAddTagsInput!) { batchAddTags(input: $input) }"


class AddTags(Primitive):
    required_privileges: ClassVar[List[str]] = ["Edit Tags"]
    entity: FirstStr
    tags: StrList

    def run(self, graph: DataHubGraph, event: WorkflowEvent) -> None:
        entity = require_urn(self.entity, "urn:li:", "entity")
        if not self.tags:
            logger.info("No tags to add to %s (request %s)", entity, event.request_urn)
            return
        tags = [require_urn(t, "urn:li:tag:", "tag") for t in self.tags]
        gql(graph, BATCH_ADD_TAGS, {"input": {"tagUrns": tags, "resources": [{"resourceUrn": entity}]}})
        logger.info("Added %s to %s (request %s)", ", ".join(tags), entity, event.request_urn)
