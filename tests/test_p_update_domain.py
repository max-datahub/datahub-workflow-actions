import pytest
from datahub.metadata.schema_classes import DomainPropertiesClass, OwnerClass, OwnershipClass, OwnershipTypeClass
from pydantic import ValidationError

from datahub_workflow_actions.events import WorkflowEvent
from datahub_workflow_actions.p_create_domain import DEFAULT_OWNERSHIP_TYPE, owner_input
from datahub_workflow_actions.p_update_domain import UpdateDomain
from datahub_workflow_actions.primitive import PermanentError
from tests.conftest import PARENT, FakeGraph

DOMAIN = "urn:li:domain:finance.payments"
EV = WorkflowEvent("urn:li:actionRequest:r", "domain-edit", "COMPLETED", "ACCEPTED", None, DOMAIN, "urn:li:corpuser:a", {})
A, B, C = "urn:li:corpuser:a", "urn:li:corpuser:b", "urn:li:corpGroup:c"
OTHER_TYPE = "urn:li:ownershipType:__system__technical_owner"


def graph_with_domain(owners=(A, B)) -> FakeGraph:
    graph = FakeGraph()
    graph.put(DOMAIN, DomainPropertiesClass(name="Payments", description="Card payments", parentDomain=PARENT))
    graph.put(
        DOMAIN,
        OwnershipClass(
            owners=[OwnerClass(owner=o, type=OwnershipTypeClass.CUSTOM, typeUrn=DEFAULT_OWNERSHIP_TYPE) for o in owners]
            + [OwnerClass(owner="urn:li:corpuser:tech", type=OwnershipTypeClass.CUSTOM, typeUrn=OTHER_TYPE)]
        ),
    )
    return graph


def test_requires_at_least_one_writable_field() -> None:
    with pytest.raises(ValidationError, match="nothing to update"):
        UpdateDomain(entity=DOMAIN)
    with pytest.raises(ValidationError, match="nothing to update"):
        UpdateDomain(entity=DOMAIN, description=[], owners=[], parent=[], name=[])


def test_description_change_calls_update_description() -> None:
    graph = graph_with_domain()
    UpdateDomain(entity=[DOMAIN], description=["Payments and refunds"]).run(graph, EV)
    assert graph.calls == [("updateDescription", {"input": {"description": "Payments and refunds", "resourceUrn": DOMAIN}})]


def test_unchanged_description_is_a_noop() -> None:
    graph = graph_with_domain()
    UpdateDomain(entity=DOMAIN, description="Card payments").run(graph, EV)
    assert graph.calls == []


def test_owners_replace_within_ownership_type_only() -> None:
    graph = graph_with_domain(owners=(A, B))
    UpdateDomain(entity=DOMAIN, owners=[B, C]).run(graph, EV)
    assert graph.calls == [
        ("batchRemoveOwners", {"input": {"ownerUrns": [A], "ownershipTypeUrn": DEFAULT_OWNERSHIP_TYPE, "resources": [{"resourceUrn": DOMAIN}]}}),
        ("batchAddOwners", {"input": {"owners": [owner_input(C, DEFAULT_OWNERSHIP_TYPE)], "ownershipTypeUrn": DEFAULT_OWNERSHIP_TYPE, "resources": [{"resourceUrn": DOMAIN}]}}),
    ]


def test_owner_replace_converges_on_second_run() -> None:
    graph = graph_with_domain(owners=(B, C))  # state after the first delivery was applied
    UpdateDomain(entity=DOMAIN, owners=[B, C]).run(graph, EV)
    assert graph.calls == []


def test_empty_owner_list_leaves_owners_alone() -> None:
    graph = graph_with_domain()
    UpdateDomain(entity=DOMAIN, owners=[], description="New").run(graph, EV)
    assert graph.operations() == ["updateDescription"]


def test_parent_change_moves_domain_and_same_parent_skips() -> None:
    graph = graph_with_domain()
    UpdateDomain(entity=DOMAIN, parent="urn:li:domain:treasury").run(graph, EV)
    assert graph.calls == [("moveDomain", {"input": {"resourceUrn": DOMAIN, "parentDomain": "urn:li:domain:treasury"}})]
    graph.calls.clear()
    UpdateDomain(entity=DOMAIN, parent=PARENT).run(graph, EV)
    assert graph.calls == []


def test_rename_emits_domain_properties_with_other_fields_preserved() -> None:
    graph = graph_with_domain()
    UpdateDomain(entity=DOMAIN, name="Payments & Refunds").run(graph, EV)
    assert graph.calls == []
    assert len(graph.emitted) == 1
    props = graph.emitted[0].aspect
    assert (props.name, props.description, props.parentDomain) == ("Payments & Refunds", "Card payments", PARENT)
    graph.emitted.clear()
    UpdateDomain(entity=DOMAIN, name="Payments & Refunds").run(graph, EV)  # second run: no-op
    assert graph.emitted == []


def test_missing_domain_is_permanent() -> None:
    with pytest.raises(PermanentError, match="does not exist"):
        UpdateDomain(entity="urn:li:domain:nope", description="x").run(FakeGraph(), EV)


def test_entity_must_be_a_domain() -> None:
    with pytest.raises(PermanentError, match="entity"):
        UpdateDomain(entity="urn:li:dataset:x", description="x").run(FakeGraph(), EV)


def test_privileges_follow_bound_fields() -> None:
    assert UpdateDomain(entity=DOMAIN, description="field:description").privileges() == ["Edit Documentation"]
    assert UpdateDomain(entity=DOMAIN, owners="field:owners", parent="field:parent").privileges() == ["Edit Owners", "Manage Domains"]
    assert UpdateDomain(entity=DOMAIN, name="field:name").privileges() == ["Edit Entity"]
    assert UpdateDomain.target == "domain (launching)"
