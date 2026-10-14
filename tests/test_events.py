import re

import pytest

from datahub_workflow_actions.events import PHASES, complete_from_request, parse, short_workflow_id
from tests.conftest import DATASET, REQUEST, REQUESTER, FakeGraph, envelope, fixture_envelope

load = fixture_envelope


def test_short_workflow_id_accepts_urn_and_id() -> None:
    assert short_workflow_id("urn:li:actionWorkflow:dataset-certification") == "dataset-certification"
    assert short_workflow_id("dataset-certification") == "dataset-certification"


@pytest.mark.parametrize(
    "fixture, phase, result, step_id",
    [
        ("completed_accepted.json", "COMPLETED", "ACCEPTED", None),
        ("completed_rejected.json", "COMPLETED", "REJECTED", None),
        ("created.json", "CREATED", None, None),
        ("step_completed.json", "STEP", None, "owner-approval"),
    ],
)
def test_phase_derivation_from_fixtures(fixture, phase, result, step_id) -> None:
    ev = parse(load(fixture))
    assert ev is not None
    assert (ev.phase, ev.result, ev.step_id) == (phase, result, step_id)
    assert ev.workflow_id == "dataset-certification"
    assert ev.entity_urn == DATASET
    assert re.fullmatch(r"1\.\d+E12", ev.fields["certification_expiration"][0])  # DATE fields: a Java double string
    assert ev.phase in PHASES


def test_bare_cancel_event_keeps_its_phase_without_a_workflow() -> None:
    ev = parse(load("cancelled.json"))  # the real event: no workflow, entity, actor or fields
    assert ev is not None
    assert (ev.phase, ev.workflow_id, ev.entity_urn, ev.actor_urn, ev.fields) == ("CANCELLED", "", None, None, {})
    assert ev.request_urn.startswith("urn:li:actionRequest:")


def test_complete_from_request_reads_workflow_entity_actor_and_fields() -> None:
    bare = parse(load("cancelled.json"))
    assert bare is not None
    graph = FakeGraph()
    graph.raw[bare.request_urn] = {"actionRequestInfo": {
        "resource": DATASET,
        "createdBy": REQUESTER,
        "params": {"workflowFormRequest": {
            "workflow": "urn:li:actionWorkflow:dataset-certification",
            "fields": [
                {"id": "justification", "values": [{"string": "ok"}]},
                {"id": "certification_expiration", "values": [{"double": 1794575161199.0}]},
                {"id": "owners", "values": [{"string": "urn:li:corpuser:a"}, {"string": "urn:li:corpuser:b"}]},
            ],
        }},
    }}
    ev = complete_from_request(graph, bare)
    assert ev is not None
    assert (ev.phase, ev.workflow_id, ev.entity_urn, ev.actor_urn) == ("CANCELLED", "dataset-certification", DATASET, REQUESTER)
    assert ev.fields == {"justification": ["ok"], "certification_expiration": ["1794575161199.0"], "owners": ["urn:li:corpuser:a", "urn:li:corpuser:b"]}


def test_complete_from_request_ignores_a_request_without_a_workflow_form() -> None:
    bare = parse(load("cancelled.json"))
    assert bare is not None
    graph = FakeGraph()
    graph.raw[bare.request_urn] = {"actionRequestInfo": {"type": "TAG_ASSOCIATION", "params": {}}}
    assert complete_from_request(graph, bare) is None


def test_fields_accepted_as_string_or_dict() -> None:
    as_string = parse(envelope(fields={"name": ["Payments"]}))
    as_dict = parse(envelope(fields={"name": ["Payments"]}, fields_as_dict=True))
    assert as_string is not None and as_dict is not None
    assert as_string.fields == as_dict.fields == {"name": ["Payments"]}


def test_missing_fields_param_gives_empty_dict() -> None:
    ev = parse(envelope())
    assert ev is not None and ev.fields == {}


def test_request_and_actor_urns() -> None:
    ev = parse(envelope())
    assert ev is not None
    assert (ev.request_urn, ev.actor_urn) == (REQUEST, REQUESTER)


def test_workflow_id_falls_back_to_workflowId_param() -> None:
    env = envelope()
    params = env.event.safe_parameters
    params.pop("workflowUrn")
    ev = parse(env)
    assert ev is not None and ev.workflow_id == "dataset-certification"


@pytest.mark.parametrize(
    "env",
    [
        envelope(entity_type="dataset"),
        envelope(action_request_type=None),
        envelope(action_request_type="TAG_ASSOCIATION"),
        envelope(ece_operation="MODIFY", operation=None, result=None),  # MODIFY without stepId or CANCELLED
    ],
    ids=["not-actionRequest", "no-actionRequestType", "proposal-not-workflow", "unclassifiable-modify"],
)
def test_non_workflow_events_are_ignored(env) -> None:
    assert parse(env) is None


def test_non_ece_event_type_is_ignored() -> None:
    class Other:
        event_type = "MetadataChangeLogEvent_v1"
        event = None

    assert parse(Other()) is None  # type: ignore[arg-type]


def test_home_launched_request_has_no_entity() -> None:
    ev = parse(envelope(entity_urn=None))
    assert ev is not None and ev.entity_urn is None
