import pytest

from datahub_workflow_actions.p_add_tags import AddTags
from datahub_workflow_actions.p_create_domain import CreateDomain
from datahub_workflow_actions.p_remove_tags import RemoveTags
from datahub_workflow_actions.p_set_structured_properties import SetStructuredProperties
from datahub_workflow_actions.p_update_domain import UpdateDomain
from datahub_workflow_actions.registry import BUILTINS, resolve_primitive
from tests.sample_primitive import Echo


def test_builtins() -> None:
    assert BUILTINS == {
        "add_tags": AddTags,
        "remove_tags": RemoveTags,
        "set_structured_properties": SetStructuredProperties,
        "create_domain": CreateDomain,
        "update_domain": UpdateDomain,
    }
    assert resolve_primitive("create_domain") is CreateDomain


def test_import_path_colon_and_dot_forms() -> None:
    assert resolve_primitive("tests.sample_primitive:Echo") is Echo
    assert resolve_primitive("tests.sample_primitive.Echo") is Echo


def test_unknown_name_lists_builtins() -> None:
    with pytest.raises(ValueError, match="add_tags"):
        resolve_primitive("add_tag")


def test_import_path_to_non_primitive_is_rejected() -> None:
    with pytest.raises(ValueError, match="not a Primitive"):
        resolve_primitive("tests.sample_primitive:NotAPrimitive")


def test_unimportable_path_is_rejected() -> None:
    with pytest.raises(ValueError, match="no_such_module"):
        resolve_primitive("no_such_module.here:Thing")
