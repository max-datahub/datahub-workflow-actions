import pytest

from datahub_workflow_actions.events import WorkflowEvent
from datahub_workflow_actions.p_add_tags import AddTags
from datahub_workflow_actions.p_remove_tags import RemoveTags
from datahub_workflow_actions.primitive import PermanentError
from tests.conftest import DATASET, FakeGraph

EV = WorkflowEvent("urn:li:actionRequest:r", "dataset-certification", "COMPLETED", "ACCEPTED", None, DATASET, "urn:li:corpuser:a", {})
TAG = "urn:li:tag:CERTIFIED"


@pytest.mark.parametrize("cls, operation", [(AddTags, "batchAddTags"), (RemoveTags, "batchRemoveTags")])
def test_calls_batch_mutation_with_resource_ref(cls, operation) -> None:
    graph = FakeGraph()
    cls(entity=DATASET, tags=[TAG]).run(graph, EV)
    assert graph.calls == [(operation, {"input": {"tagUrns": [TAG], "resources": [{"resourceUrn": DATASET}]}})]


@pytest.mark.parametrize("cls", [AddTags, RemoveTags])
def test_second_run_makes_the_same_idempotent_call(cls) -> None:
    graph = FakeGraph()
    primitive = cls(entity=[DATASET], tags=TAG)  # bound shapes: list for entity, scalar for tags
    primitive.run(graph, EV)
    primitive.run(graph, EV)
    assert graph.operations() == [graph.operations()[0]] * 2


@pytest.mark.parametrize("cls", [AddTags, RemoveTags])
def test_empty_tag_list_is_a_noop(cls) -> None:
    graph = FakeGraph()
    cls(entity=DATASET, tags=[]).run(graph, EV)  # e.g. an optional form field left empty
    assert graph.calls == []


@pytest.mark.parametrize("cls", [AddTags, RemoveTags])
def test_missing_entity_is_permanent(cls) -> None:
    with pytest.raises(PermanentError, match="entity"):
        cls(entity=None, tags=[TAG]).run(FakeGraph(), EV)


@pytest.mark.parametrize("cls", [AddTags, RemoveTags])
def test_non_tag_urn_is_permanent(cls) -> None:
    with pytest.raises(PermanentError, match="tag"):
        cls(entity=DATASET, tags=["urn:li:glossaryTerm:x"]).run(FakeGraph(), EV)


def test_privileges() -> None:
    assert AddTags(entity=DATASET, tags=[TAG]).privileges() == ["Edit Tags"]
    assert RemoveTags(entity=DATASET, tags=[TAG]).privileges() == ["Edit Tags"]
