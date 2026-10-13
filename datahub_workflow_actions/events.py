"""Turn an EntityChangeEvent_v1 envelope into a WorkflowEvent, or None if it is not one.

Event contract (GMS WorkflowFormRequest*ChangeEventGenerator):
  entityType=actionRequest, category=LIFECYCLE, parameters.actionRequestType=WORKFLOW_FORM_REQUEST
  CREATE   -> request submitted            (no `operation` param)
  MODIFY   -> step completed (`stepId`) or cancelled (`operation` param = CANCELLED)
  COMPLETED-> request finished (`operation` param = COMPLETE, `result` = ACCEPTED|REJECTED)
`fields` is a JSON string of {fieldId: [value, ...]}; every value is a string.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

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
    if not workflow_ref or phase is None:
        logger.debug("Ignoring actionRequest event %s (operation=%s)", event.entityUrn, event.operation)
        return None
    return WorkflowEvent(
        request_urn=event.entityUrn,
        workflow_id=short_workflow_id(workflow_ref),
        phase=phase,
        result=params.get("result") if phase == "COMPLETED" else None,
        step_id=params.get("stepId") if phase == "STEP" else None,
        entity_urn=params.get("entityUrn"),
        actor_urn=params.get("actorUrn"),
        fields=_fields(params.get("fields")),
    )
