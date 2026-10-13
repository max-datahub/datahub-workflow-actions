import pytest

from datahub_workflow_actions.binding import EVENT_PARAMS, resolve, validate_refs
from datahub_workflow_actions.events import WorkflowEvent

EV = WorkflowEvent(
    request_urn="urn:li:actionRequest:r",
    workflow_id="domain-create",
    phase="COMPLETED",
    result="ACCEPTED",
    step_id=None,
    entity_urn="urn:li:domain:finance",
    actor_urn="urn:li:corpuser:alice",
    fields={"name": ["Payments"], "owners": ["urn:li:corpuser:a", "urn:li:corpGroup:b"], "empty": []},
)


def test_literal_passes_through() -> None:
    assert resolve("urn:li:tag:CERTIFIED", EV) == "urn:li:tag:CERTIFIED"
    assert resolve(3, EV) == 3
    assert resolve(True, EV) is True


def test_field_resolves_to_list() -> None:
    assert resolve("field:name", EV) == ["Payments"]
    assert resolve("field:owners", EV) == ["urn:li:corpuser:a", "urn:li:corpGroup:b"]


def test_missing_or_empty_field_resolves_to_empty_list() -> None:
    assert resolve("field:nope", EV) == []
    assert resolve("field:empty", EV) == []


def test_event_params() -> None:
    assert resolve("event:entityUrn", EV) == "urn:li:domain:finance"
    assert resolve("event:actorUrn", EV) == "urn:li:corpuser:alice"
    assert resolve("event:requestUrn", EV) == "urn:li:actionRequest:r"
    assert resolve("event:workflowId", EV) == "domain-create"
    assert resolve("event:result", EV) == "ACCEPTED"
    assert resolve("event:stepId", EV) is None
    assert set(EVENT_PARAMS) == {"entityUrn", "actorUrn", "requestUrn", "workflowId", "stepId", "result"}


def test_resolves_recursively_through_dicts_and_lists() -> None:
    params = {"entity": "event:entityUrn", "values": {"urn:li:structuredProperty:x": "field:name"}, "tags": ["field:owners", "urn:li:tag:t"]}
    assert resolve(params, EV) == {
        "entity": "urn:li:domain:finance",
        "values": {"urn:li:structuredProperty:x": ["Payments"]},
        "tags": [["urn:li:corpuser:a", "urn:li:corpGroup:b"], "urn:li:tag:t"],
    }


def test_validate_refs_rejects_unknown_event_name() -> None:
    with pytest.raises(ValueError, match="event:nope"):
        validate_refs({"entity": "event:nope"})
    validate_refs({"entity": "event:entityUrn", "x": ["field:anything", "literal"]})  # no raise
