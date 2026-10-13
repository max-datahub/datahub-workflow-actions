import pytest
from datahub.metadata.schema_classes import StructuredPropertyDefinitionClass

import datahub_workflow_actions.p_set_structured_properties as mod
from datahub_workflow_actions.events import WorkflowEvent
from datahub_workflow_actions.p_set_structured_properties import SetStructuredProperties, to_date
from datahub_workflow_actions.primitive import PermanentError
from tests.conftest import DATASET, FakeGraph

EV = WorkflowEvent("urn:li:actionRequest:r", "dataset-certification", "COMPLETED", "ACCEPTED", None, DATASET, "urn:li:corpuser:a", {})
EXPIRY = "urn:li:structuredProperty:certification_expiry"
TIER = "urn:li:structuredProperty:tier"
SCORE = "urn:li:structuredProperty:score"


def defn(value_type: str) -> StructuredPropertyDefinitionClass:
    return StructuredPropertyDefinitionClass(qualifiedName="x", valueType=f"urn:li:dataType:datahub.{value_type}", entityTypes=["urn:li:entityType:datahub.dataset"])


@pytest.fixture(autouse=True)
def clear_cache():
    mod._DEFINITIONS.clear()
    yield
    mod._DEFINITIONS.clear()


def graph_with_definitions() -> FakeGraph:
    graph = FakeGraph()
    graph.put(EXPIRY, defn("date"))
    graph.put(TIER, defn("string"))
    graph.put(SCORE, defn("number"))
    return graph


def test_to_date_accepts_java_double_strings_millis_and_iso() -> None:
    assert to_date("1.793367303267E12") == "2026-10-30"  # as GMS serialises DATE form fields
    assert to_date("1793367303267") == "2026-10-30"
    assert to_date("2026-10-30") == "2026-10-30"
    with pytest.raises(PermanentError, match="date"):
        to_date("next tuesday")


def test_coerces_each_value_by_definition_type() -> None:
    graph = graph_with_definitions()
    SetStructuredProperties(entity=DATASET, values={EXPIRY: ["1.793367303267E12"], TIER: "GOLD", SCORE: ["4.5"]}).run(graph, EV)
    assert graph.operations() == ["upsertStructuredProperties"]
    params = graph.calls[0][1]["input"]
    assert params["assetUrn"] == DATASET
    assert {p["structuredPropertyUrn"]: p["values"] for p in params["structuredPropertyInputParams"]} == {
        EXPIRY: [{"stringValue": "2026-10-30"}],
        TIER: [{"stringValue": "GOLD"}],
        SCORE: [{"numberValue": 4.5}],
    }


def test_empty_values_are_skipped_and_no_call_when_nothing_left() -> None:
    graph = graph_with_definitions()
    SetStructuredProperties(entity=DATASET, values={TIER: []}).run(graph, EV)  # optional field left empty
    assert graph.calls == []


def test_unknown_property_is_permanent() -> None:
    with pytest.raises(PermanentError, match="urn:li:structuredProperty:nope"):
        SetStructuredProperties(entity=DATASET, values={"urn:li:structuredProperty:nope": ["x"]}).run(FakeGraph(), EV)


def test_non_numeric_value_for_number_property_is_permanent() -> None:
    with pytest.raises(PermanentError, match="number"):
        SetStructuredProperties(entity=DATASET, values={SCORE: ["high"]}).run(graph_with_definitions(), EV)


def test_definition_is_cached_per_process() -> None:
    graph = graph_with_definitions()
    primitive = SetStructuredProperties(entity=DATASET, values={TIER: ["GOLD"]})
    primitive.run(graph, EV)
    graph.aspects.clear()  # a second run must not need the graph for the definition
    primitive.run(graph, EV)
    assert graph.operations() == ["upsertStructuredProperties"] * 2


def test_missing_entity_is_permanent() -> None:
    with pytest.raises(PermanentError, match="entity"):
        SetStructuredProperties(entity=[], values={TIER: ["GOLD"]}).run(graph_with_definitions(), EV)


def test_privileges() -> None:
    assert SetStructuredProperties(entity=DATASET, values={TIER: ["GOLD"]}).privileges() == ["Edit Properties"]
