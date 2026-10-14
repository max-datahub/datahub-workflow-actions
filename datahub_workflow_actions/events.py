"""Turn an EntityChangeEvent_v1 envelope into a WorkflowEvent, or None if it is not one.

Event contract (GMS WorkflowFormRequest*ChangeEventGenerator):
  entityType=actionRequest, category=LIFECYCLE, parameters.actionRequestType=WORKFLOW_FORM_REQUEST
  CREATE   -> request submitted            (no `operation` param)
  MODIFY   -> step completed (`stepId`) or cancelled (`operation` param = CANCELLED)
  COMPLETED-> request finished (`operation` param = COMPLETE, `result` = ACCEPTED|REJECTED)
`fields` is a JSON string of {fieldId: [value, ...]}; every value is a string.

The CANCELLED event is bare on fork builds through v2.3.x: only `operation`, `cancelledBy` and
`note`, no workflow, entity, actor or fields. `parse` keeps it with an empty workflow_id and the
router completes it from the request's actionRequestInfo aspect (`complete_from_request`).
"""

import dataclasses
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from datahub.ingestion.graph.client import DataHubGraph
from datahub_actions.event.event_envelope import EventEnvelope

logger = logging.getLogger("datahub_actions.workflow_actions.events")

ENTITY_CHANGE_EVENT = "EntityChangeEvent_v1"
PHASES = ("CREATED", "STEP", "COMPLETED", "CANCELLED")


@dataclass(frozen=True)
class WorkflowEvent:
    request_urn: str
    workflow_id: str
    phase: str
    result: Optional[str]
    step_id: Optional[str]
    entity_urn: Optional[str]
    actor_urn: Optional[str]
    fields: Dict[str, List[str]]


def short_workflow_id(ref: str) -> str:
    """`urn:li:actionWorkflow:<id>` and a bare `<id>` both normalise to `<id>`."""
    return ref.rsplit(":", 1)[-1]


def _phase(ece_operation: str, params: Dict[str, Any]) -> Optional[str]:
    operation = params.get("operation")
    if operation == "CANCELLED":
        return "CANCELLED"
    if operation == "COMPLETE":
        return "COMPLETED"
    if params.get("stepId"):
        return "STEP"
    if ece_operation == "CREATE":
        return "CREATED"
    return None


def _fields(raw: Any) -> Dict[str, List[str]]:
    if not raw:
        return {}
    decoded = json.loads(raw) if isinstance(raw, str) else raw
    return {key: [str(v) for v in values] for key, values in decoded.items()}


def parse(envelope: EventEnvelope) -> Optional[WorkflowEvent]:
    if envelope.event_type != ENTITY_CHANGE_EVENT:
        return None
    event = envelope.event
    if event.entityType != "actionRequest":
        return None
    params = event.safe_parameters
    if params.get("actionRequestType") != "WORKFLOW_FORM_REQUEST":
        return None
    workflow_ref = params.get("workflowUrn") or params.get("workflowId")
    phase = _phase(event.operation, params)
    if phase is None or (not workflow_ref and phase != "CANCELLED"):
        logger.debug("Ignoring actionRequest event %s (operation=%s)", event.entityUrn, event.operation)
        return None
    return WorkflowEvent(
        request_urn=event.entityUrn,
        workflow_id=short_workflow_id(workflow_ref) if workflow_ref else "",
        phase=phase,
        result=params.get("result") if phase == "COMPLETED" else None,
        step_id=params.get("stepId") if phase == "STEP" else None,
        entity_urn=params.get("entityUrn"),
        actor_urn=params.get("actorUrn"),
        fields=_fields(params.get("fields")),
    )


def complete_from_request(graph: DataHubGraph, event: WorkflowEvent) -> Optional[WorkflowEvent]:
    """Fill workflow, entity, actor and fields in from the request's actionRequestInfo aspect.
    None (logged) when the request carries no workflow form; transport errors propagate."""
    raw = graph.get_entity_raw(event.request_urn, aspects=["actionRequestInfo"])
    info = ((raw.get("aspects") or {}).get("actionRequestInfo") or {}).get("value") or {}
    form = (info.get("params") or {}).get("workflowFormRequest") or {}
    if not form.get("workflow"):
        logger.warning("Request %s: %s event without a workflow reference and no workflow form on the request; ignored", event.request_urn, event.phase)
        return None
    # Stored values are typed unions such as {"string": "..."} or {"double": 1.79e12}; the event
    # contract is strings, and the primitives coerce for their target.
    fields = {f["id"]: [str(v) for value in f.get("values", []) for v in value.values()] for f in form.get("fields", [])}
    return dataclasses.replace(
        event,
        workflow_id=short_workflow_id(form["workflow"]),
        entity_urn=info.get("resource"),
        actor_urn=info.get("createdBy"),
        fields=fields,
    )
