"""The whole binding language. Three forms, no expressions, no defaults, no transforms.

  literal          -> itself
  field:<id>       -> the submitted values of that form field, always a list (missing -> [])
  event:<param>    -> one attribute of the WorkflowEvent (see EVENT_PARAMS)

Type shaping (first element, wrapping) is the primitive's job via FirstStr / StrList.
Value transforms that depend on the target (dates for structured properties) live in primitives.
Do not add a fourth form here without updating the spec and CLAUDE.md.
"""

from typing import Any, Dict

from datahub_workflow_actions.events import WorkflowEvent

FIELD = "field:"
EVENT = "event:"

EVENT_PARAMS: Dict[str, str] = {
    "entityUrn": "entity_urn",
    "actorUrn": "actor_urn",
    "requestUrn": "request_urn",
    "workflowId": "workflow_id",
    "stepId": "step_id",
    "result": "result",
}


def validate_refs(value: Any) -> None:
    """Config-time check: every `event:` reference names a known parameter."""
    if isinstance(value, dict):
        for v in value.values():
            validate_refs(v)
    elif isinstance(value, list):
        for v in value:
            validate_refs(v)
    elif isinstance(value, str) and value.startswith(EVENT):
        name = value[len(EVENT) :]
        if name not in EVENT_PARAMS:
            raise ValueError(f"unknown binding {value!r}; event: parameters are {sorted(EVENT_PARAMS)}")


def resolve(value: Any, event: WorkflowEvent) -> Any:
    if isinstance(value, dict):
        return {k: resolve(v, event) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, event) for v in value]
    if isinstance(value, str):
        if value.startswith(FIELD):
            return list(event.fields.get(value[len(FIELD) :], []))
        if value.startswith(EVENT):
            return getattr(event, EVENT_PARAMS[value[len(EVENT) :]])
    return value
