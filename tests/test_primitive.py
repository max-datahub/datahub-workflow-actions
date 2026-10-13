from typing import ClassVar, List

import pytest
from datahub.configuration.common import GraphError, OperationalError
from pydantic import ValidationError

from datahub_workflow_actions.primitive import FirstStr, PermanentError, Primitive, StrList, gql, require_urn
from tests.conftest import FakeGraph


class Sample(Primitive):
    required_privileges: ClassVar[List[str]] = ["Edit Tags"]
    entity: FirstStr
    tags: StrList = []

    def run(self, graph, event) -> None:  # pragma: no cover - contract only
        pass


def test_first_str_takes_first_element_or_none() -> None:
    assert Sample(entity=["urn:a", "urn:b"]).entity == "urn:a"
    assert Sample(entity="urn:a").entity == "urn:a"
    assert Sample(entity=[]).entity is None
    assert Sample(entity=None).entity is None


def test_str_list_wraps_scalars_and_drops_empties() -> None:
    assert Sample(entity="x", tags="urn:li:tag:a").tags == ["urn:li:tag:a"]
    assert Sample(entity="x", tags=["urn:li:tag:a", "", None]).tags == ["urn:li:tag:a"]
    assert Sample(entity="x", tags=None).tags == []


def test_unknown_param_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Sample(entity="x", colour="red")


def test_privileges_default_to_class_list() -> None:
    assert Sample(entity="x").privileges() == ["Edit Tags"]
    assert Sample.target == "launching entity"


def test_gql_turns_graphql_errors_into_permanent_errors() -> None:
    graph = FakeGraph()
    graph.raise_on["batchAddTags"] = GraphError("Unauthorized to perform this action")
    with pytest.raises(PermanentError, match="Unauthorized"):
        gql(graph, "mutation($input: X!) { batchAddTags(input: $input) }", {"input": {}})


def test_gql_lets_transport_errors_propagate() -> None:
    graph = FakeGraph()
    graph.raise_on["batchAddTags"] = OperationalError("503 from GMS")
    with pytest.raises(OperationalError):
        gql(graph, "mutation($input: X!) { batchAddTags(input: $input) }", {"input": {}})


def test_gql_returns_data() -> None:
    graph = FakeGraph()
    graph.responses["createDomain"] = "urn:li:domain:x"
    assert gql(graph, "mutation($input: X!) { createDomain(input: $input) }", {"input": {}}) == {"createDomain": "urn:li:domain:x"}


def test_require_urn() -> None:
    assert require_urn("urn:li:domain:x", "urn:li:domain:", "parent") == "urn:li:domain:x"
    with pytest.raises(PermanentError, match="parent"):
        require_urn("urn:li:dataset:x", "urn:li:domain:", "parent")
    with pytest.raises(PermanentError, match="parent"):
        require_urn(None, "urn:li:domain:", "parent")
