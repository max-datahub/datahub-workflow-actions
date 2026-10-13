"""remove_tags: remove tags from one asset via batchRemoveTags (removing an absent tag is a no-op)."""

from typing import ClassVar, List

from datahub.ingestion.graph.client import DataHubGraph

from datahub_workflow_actions.events import WorkflowEvent
from datahub_workflow_actions.primitive import FirstStr, Primitive, StrList, gql, logger_for, require_urn

logger = logger_for("remove_tags")

BATCH_REMOVE_TAGS = "mutation($input: BatchRemoveTagsInput!) { batchRemoveTags(input: $input) }"


class RemoveTags(Primitive):
    required_privileges: ClassVar[List[str]] = ["Edit Tags"]
    entity: FirstStr
    tags: StrList

    def run(self, graph: DataHubGraph, event: WorkflowEvent) -> None:
        entity = require_urn(self.entity, "urn:li:", "entity")
        if not self.tags:
            logger.info("No tags to remove from %s (request %s)", entity, event.request_urn)
            return
        tags = [require_urn(t, "urn:li:tag:", "tag") for t in self.tags]
        gql(graph, BATCH_REMOVE_TAGS, {"input": {"tagUrns": tags, "resources": [{"resourceUrn": entity}]}})
        logger.info("Removed %s from %s (request %s)", ", ".join(tags), entity, event.request_urn)
