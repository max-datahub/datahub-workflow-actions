from types import SimpleNamespace
from typing import Optional

import pytest
from datahub.configuration.common import OperationalError

import tests.sample_primitive as sample
from datahub_workflow_actions.router import WorkflowRouter, manifest
from tests.conftest import DATASET, REQUEST, FakeGraph, envelope

ECHO = "tests.sample_primitive:Echo"


def router(config: dict, graph: Optional[FakeGraph] = None) -> WorkflowRouter:
    return WorkflowRouter.create(config, SimpleNamespace(graph=SimpleNamespace(graph=graph or FakeGraph())))


@pytest.fixture(autouse=True)
def clear_runs():
    sample.RUNS.clear()
    yield
    sample.RUNS.clear()


def test_graph_is_required() -> None:
    with pytest.raises(ValueError, match="datahub"):
        WorkflowRouter.create({"workflows": {}}, SimpleNamespace(graph=None))


@pytest.mark.parametrize(
    "config, match",
    [
        ({"workflows": {"w": [{"on": "COMPLETED", "do": [{"add_tag": {}}]}]}}, "add_tag"),
        ({"workflows": {"w": [{"on": "COMPLETED", "do": [{"nope.module:X": {}}]}]}}, "nope.module"),
        ({"workflows": {"w": [{"on": "CREATED", "when": "ANY", "do": [ECHO]}]}}, "when"),
        ({"workflows": {"w": [{"on": "COMPLETED", "step_id": "s", "do": [ECHO]}]}}, "step_id"),
        ({"workflows": {"w": [{"on": "COMPLETED", "do": [{ECHO: {"value": "event:nope"}}]}]}}, "event:nope"),
        ({"workflows": {"w": [{"on": "COMPLETED", "do": [{ECHO: {"colour": "red"}}]}]}}, "colour"),
        ({"workflows": {"w": [{"on": "COMPLETED", "do": [{"update_domain": {"entity": "event:entityUrn"}}]}]}}, "nothing to update"),
        ({"workflows": {"w": [{"on": "COMPLETED", "do": [{ECHO: {}, "add_tags": {}}]}]}}, "single-key"),
        ({"workflows": {"w": [{"on": "COMPLETED", "do": []}]}}, "at least one"),
        ({"workflows": {"w": [{"on": "FINISHED", "do": [ECHO]}]}}, r"workflows\.w\.0\.on"),  # location, not value: ConfigModel hides inputs in errors
    ],
    ids=["unknown-primitive", "bad-import", "when-on-created", "step_id-on-completed", "unknown-event-ref", "unknown-param", "update-domain-nothing", "two-keys", "empty-do", "bad-phase"],
)
def test_invalid_config_fails_at_startup(config, match) -> None:
    with pytest.raises(ValueError, match=match):
        router(config)


def test_runs_steps_in_order_with_resolved_bindings() -> None:
    r = router({"workflows": {"dataset-certification": [{"on": "COMPLETED", "do": [
        {ECHO: {"value": "event:entityUrn", "values": "field:owners"}},
        {ECHO: {"value": "literal"}},
    ]}]}})
    r.act(envelope(fields={"owners": ["urn:li:corpuser:a", "urn:li:corpuser:b"]}))
    assert sample.RUNS == [(DATASET, ["urn:li:corpuser:a", "urn:li:corpuser:b"], REQUEST), ("literal", [], REQUEST)]


def test_when_defaults_to_accepted_and_any_covers_both_results() -> None:
    r = router({"workflows": {"dataset-certification": [
        {"on": "COMPLETED", "do": [{ECHO: {"value": "accepted-only"}}]},
        {"on": "COMPLETED", "when": "ANY", "do": [{ECHO: {"value": "any"}}]},
        {"on": "COMPLETED", "when": "REJECTED", "do": [{ECHO: {"value": "rejected-only"}}]},
        {"on": "CANCELLED", "do": [{ECHO: {"value": "cancelled"}}]},
    ]}})
    r.act(envelope(result="ACCEPTED"))
    r.act(envelope(result="REJECTED"))
    r.act(envelope(ece_operation="MODIFY", operation="CANCELLED", result=None))
    assert [v for v, _, _ in sample.RUNS] == ["accepted-only", "any", "any", "rejected-only", "cancelled"]


def test_created_and_step_rules() -> None:
    r = router({"workflows": {"dataset-certification": [
        {"on": "CREATED", "do": [{ECHO: {"value": "created"}}]},
        {"on": "STEP", "step_id": "legal-review", "do": [{ECHO: {"value": "legal"}}]},
        {"on": "STEP", "do": [{ECHO: {"value": "any-step"}}]},
    ]}})
    r.act(envelope(ece_operation="CREATE", operation=None, result=None))
    r.act(envelope(ece_operation="MODIFY", operation=None, result=None, stepId="owner-approval", stepResult="ACCEPTED"))
    r.act(envelope(ece_operation="MODIFY", operation=None, result=None, stepId="legal-review", stepResult="ACCEPTED"))
    assert [v for v, _, _ in sample.RUNS] == ["created", "any-step", "legal", "any-step"]


def test_workflow_key_matches_urn_or_short_id() -> None:
    r = router({"workflows": {"urn:li:actionWorkflow:dataset-certification": [{"on": "COMPLETED", "do": [{ECHO: {"value": "hit"}}]}]}})
    r.act(envelope(workflow="dataset-certification"))
    assert len(sample.RUNS) == 1
    r2 = router({"workflows": {"dataset-certification": [{"on": "COMPLETED", "do": [{ECHO: {"value": "hit"}}]}]}})
    env = envelope(workflow="dataset-certification")
    env.event.safe_parameters.pop("workflowId")  # only the URN form present
    r2.act(env)
    assert len(sample.RUNS) == 2


def test_unknown_workflow_and_non_workflow_events_do_nothing() -> None:
    r = router({"workflows": {"other": [{"on": "COMPLETED", "do": [ECHO]}]}})
    r.act(envelope(workflow="dataset-certification"))
    r.act(envelope(entity_type="dataset"))
    assert sample.RUNS == []


def test_permanent_error_stops_remaining_steps_and_is_not_raised() -> None:
    r = router({"workflows": {"dataset-certification": [{"on": "COMPLETED", "do": [
        {ECHO: {"fail": "permanent"}},
        {ECHO: {"value": "never"}},
    ]}]}})
    r.act(envelope())
    assert sample.RUNS == []


def test_invalid_resolved_values_are_permanent() -> None:
    # update_domain with every field empty at run time: valid config, request carried nothing
    r = router({"workflows": {"domain-edit": [{"on": "COMPLETED", "do": [
        {"update_domain": {"entity": "event:entityUrn", "description": "field:description"}},
        {ECHO: {"value": "never"}},
    ]}]}})
    r.act(envelope(workflow="domain-edit", entity_urn="urn:li:domain:x", fields={}))
    assert sample.RUNS == []


def test_transport_errors_propagate_for_retry() -> None:
    r = router({"workflows": {"dataset-certification": [{"on": "COMPLETED", "do": [{ECHO: {"fail": "transport"}}]}]}})
    with pytest.raises(OperationalError):
        r.act(envelope())


def test_real_primitive_through_router() -> None:
    graph = FakeGraph()
    r = router({"workflows": {"dataset-certification": [{"on": "COMPLETED", "do": [
        {"add_tags": {"entity": "event:entityUrn", "tags": ["urn:li:tag:CERTIFIED"]}},
    ]}]}}, graph)
    r.act(envelope())
    assert graph.calls == [("batchAddTags", {"input": {"tagUrns": ["urn:li:tag:CERTIFIED"], "resources": [{"resourceUrn": DATASET}]}})]


def test_manifest_groups_privileges_by_target() -> None:
    r = router({"workflows": {
        "dataset-certification": [{"on": "COMPLETED", "do": [
            {"add_tags": {"entity": "event:entityUrn", "tags": ["urn:li:tag:CERTIFIED"]}},
            {"set_structured_properties": {"entity": "event:entityUrn", "values": {"urn:li:structuredProperty:x": "field:x"}}},
        ]}],
        "domain-create": [{"on": "COMPLETED", "do": [{"create_domain": {"name": "field:name"}}]}],
        "domain-edit": [{"on": "COMPLETED", "do": [{"update_domain": {"entity": "event:entityUrn", "description": "field:description"}}]}],
    }})
    assert manifest(r.rules) == (
        "Create Domains, View Entity Page on domain (created); "
        "Edit Documentation, View Entity Page on domain (launching); "
        "Edit Properties, Edit Tags, View Entity Page on launching entity"
    )
