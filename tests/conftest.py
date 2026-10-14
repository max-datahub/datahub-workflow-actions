import json
import pathlib
from typing import Any, Dict, List, Optional, Tuple

from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub_actions.event.event_envelope import EventEnvelope

DATASET = "urn:li:dataset:(urn:li:dataPlatform:databricks,my_catalog.my_schema.events,PROD)"
PARENT = "urn:li:domain:finance"
REQUESTER = "urn:li:corpuser:requester"
REQUEST = "urn:li:actionRequest:req-1"
FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture_envelope(name: str) -> EventEnvelope:
    """An event captured from a live instance (tests/fixtures/)."""
    return EventEnvelope.from_json((FIXTURES / name).read_text())


def envelope(
    workflow: str = "dataset-certification",
    *,
    ece_operation: str = "COMPLETED",
    operation: Optional[str] = "COMPLETE",
    result: Optional[str] = "ACCEPTED",
    entity_urn: Optional[str] = DATASET,
    fields: Optional[Dict[str, List[str]]] = None,
    fields_as_dict: bool = False,
    event_type: str = "EntityChangeEvent_v1",
    entity_type: str = "actionRequest",
    action_request_type: Optional[str] = "WORKFLOW_FORM_REQUEST",
    **extra: Any,
) -> EventEnvelope:
    """An EntityChangeEvent_v1 envelope shaped like the ones GMS emits for a workflow request."""
    params: Dict[str, Any] = {
        "workflowUrn": f"urn:li:actionWorkflow:{workflow}",
        "workflowId": workflow,
        "actorUrn": REQUESTER,
    }
    if action_request_type is not None:
        params["actionRequestType"] = action_request_type
    if operation is not None:
        params["operation"] = operation
    if result is not None:
        params["result"] = result
    if entity_urn is not None:
        params["entityUrn"] = entity_urn
    if fields is not None:
        params["fields"] = fields if fields_as_dict else json.dumps(fields)
    params.update(extra)
    return EventEnvelope.from_json(
        json.dumps(
            {
                "event_type": event_type,
                "event": {
                    "entityType": entity_type,
                    "entityUrn": REQUEST,
                    "category": "LIFECYCLE",
                    "operation": ece_operation,
                    "auditStamp": {"time": 0, "actor": REQUESTER},
                    "version": 0,
                    "parameters": params,
                },
                "meta": {},
            }
        )
    )


class FakeGraph:
    """Stand-in for DataHubGraph: records GraphQL calls, serves canned aspects."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self.aspects: Dict[Tuple[str, str], Any] = {}
        self.emitted: List[MetadataChangeProposalWrapper] = []
        self.responses: Dict[str, Any] = {}
        self.raise_on: Dict[str, Exception] = {}
        self.raw: Dict[str, Dict[str, Any]] = {}  # urn -> {aspectName: value}, served by get_entity_raw
        self.reads: List[str] = []

    def execute_graphql(self, query: str, variables: Optional[Dict[str, Any]] = None, **_: Any) -> Dict[str, Any]:
        operation = query.split("{", 1)[1].split("(", 1)[0].strip()
        if operation in self.raise_on:
            raise self.raise_on.pop(operation)
        self.calls.append((operation, variables or {}))
        return {operation: self.responses.get(operation, True)}

    def get_aspect(self, entity_urn: str, aspect_type: Any, version: int = 0) -> Any:
        return self.aspects.get((entity_urn, aspect_type.ASPECT_NAME))

    def get_entity_raw(self, entity_urn: str, aspects: Optional[List[str]] = None) -> Dict[str, Any]:
        self.reads.append(entity_urn)
        found = self.raw.get(entity_urn, {})
        return {"urn": entity_urn, "aspects": {n: {"value": v} for n, v in found.items() if not aspects or n in aspects}}

    def emit(self, item: MetadataChangeProposalWrapper) -> None:
        self.emitted.append(item)
        if item.aspect is not None:
            self.aspects[(item.entityUrn, item.aspect.ASPECT_NAME)] = item.aspect

    def put(self, urn: str, aspect: Any) -> None:
        self.aspects[(urn, aspect.ASPECT_NAME)] = aspect

    def operations(self) -> List[str]:
        return [op for op, _ in self.calls]
