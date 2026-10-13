import pytest
from datahub.configuration.common import GraphError
from datahub.metadata.schema_classes import DomainPropertiesClass

from datahub_workflow_actions.events import WorkflowEvent
from datahub_workflow_actions.p_create_domain import DEFAULT_OWNERSHIP_TYPE, CreateDomain, domain_id, owner_input, slug
from datahub_workflow_actions.primitive import PermanentError
from tests.conftest import PARENT, FakeGraph

EV = WorkflowEvent("urn:li:actionRequest:r", "domain-create", "COMPLETED", "ACCEPTED", None, PARENT, "urn:li:corpuser:a", {})
USER = "urn:li:corpuser:alice"
GROUP = "urn:li:corpGroup:stewards"


def test_slug_and_domain_id() -> None:
    assert slug("Ñandú  Pagos!") == "nandu-pagos"
    assert domain_id("Payments", None) == "payments"
    assert domain_id("Payments", "urn:li:domain:finance") == "finance.payments"
    assert domain_id("Payments", "urn:li:domain:finance.eu") == "finance.eu.payments"


def test_owner_input_maps_users_and_groups() -> None:
    assert owner_input(USER, DEFAULT_OWNERSHIP_TYPE) == {"ownerUrn": USER, "ownerEntityType": "CORP_USER", "ownershipTypeUrn": DEFAULT_OWNERSHIP_TYPE}
    assert owner_input(GROUP, "urn:li:ownershipType:t")["ownerEntityType"] == "CORP_GROUP"
    with pytest.raises(PermanentError, match="owner"):
        owner_input("urn:li:dataset:x", DEFAULT_OWNERSHIP_TYPE)


def test_creates_domain_under_parent_with_owners() -> None:
    graph = FakeGraph()
    CreateDomain(name=["Payments"], description=["Card payments"], parent=[PARENT], owners=[USER, GROUP]).run(graph, EV)
    assert graph.calls == [
        (
            "createDomain",
            {
                "input": {
                    "id": "finance.payments",
                    "name": "Payments",
                    "description": "Card payments",
                    "parentDomain": PARENT,
                    "owners": [owner_input(USER, DEFAULT_OWNERSHIP_TYPE), owner_input(GROUP, DEFAULT_OWNERSHIP_TYPE)],
                }
            },
        )
    ]


def test_top_level_domain_without_owners() -> None:
    graph = FakeGraph()
    CreateDomain(name="Payments", owners=[]).run(graph, EV)  # empty optional form fields
    assert graph.calls[0][1]["input"] == {"id": "payments", "name": "Payments", "description": None, "parentDomain": None, "owners": []}


def test_existing_domain_with_same_name_is_skipped() -> None:
    graph = FakeGraph()
    graph.put("urn:li:domain:finance.payments", DomainPropertiesClass(name="payments", parentDomain=PARENT))
    CreateDomain(name="Payments", parent=PARENT).run(graph, EV)
    assert graph.calls == []


def test_existing_domain_with_other_name_is_permanent() -> None:
    graph = FakeGraph()
    graph.put("urn:li:domain:finance.payments", DomainPropertiesClass(name="Legacy Payments", parentDomain=PARENT))
    with pytest.raises(PermanentError, match="already holds"):
        CreateDomain(name="Payments", parent=PARENT).run(graph, EV)


def test_gms_sibling_name_rejection_is_permanent() -> None:
    graph = FakeGraph()
    graph.raise_on["createDomain"] = GraphError('"Payments" already exists in this domain. Please pick a unique name.')
    with pytest.raises(PermanentError, match="already exists"):
        CreateDomain(name="Payments", parent=PARENT).run(graph, EV)


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"name": []}, "name"),
        ({"name": "   "}, "name"),
        ({"name": "!!!"}, "name"),
        ({"name": "Payments", "parent": "urn:li:dataset:x"}, "parent"),
        ({"name": "Payments", "owners": ["urn:li:dataset:x"]}, "owner"),
    ],
    ids=["missing-name", "blank-name", "unsluggable-name", "parent-not-domain", "owner-not-user-or-group"],
)
def test_bad_form_values_are_permanent(kwargs, match) -> None:
    with pytest.raises(PermanentError, match=match):
        CreateDomain(**kwargs).run(FakeGraph(), EV)


def test_privileges_and_target() -> None:
    assert CreateDomain(name="x").privileges() == ["Create Domains"]
    assert CreateDomain.target == "domain (created)"
