"""Builtin primitive names, plus import-path loading for customer primitives.

A name containing `.` or `:` is imported with the same helper the actions framework uses for
`action.type`, so `my_company.wf:NotifyCmdb` works from any module on PYTHONPATH.
"""

from typing import Dict, Type

from datahub.ingestion.api.registry import import_path

from datahub_workflow_actions.p_add_tags import AddTags
from datahub_workflow_actions.p_create_domain import CreateDomain
from datahub_workflow_actions.p_remove_tags import RemoveTags
from datahub_workflow_actions.p_set_structured_properties import SetStructuredProperties
from datahub_workflow_actions.p_update_domain import UpdateDomain
from datahub_workflow_actions.primitive import Primitive

BUILTINS: Dict[str, Type[Primitive]] = {
    "add_tags": AddTags,
    "remove_tags": RemoveTags,
    "set_structured_properties": SetStructuredProperties,
    "create_domain": CreateDomain,
    "update_domain": UpdateDomain,
}


def resolve_primitive(name: str) -> Type[Primitive]:
    if name in BUILTINS:
        return BUILTINS[name]
    if "." not in name and ":" not in name:
        raise ValueError(f"unknown primitive {name!r}; builtins are {sorted(BUILTINS)}")
    try:
        cls = import_path(name)
    except Exception as e:
        raise ValueError(f"cannot import primitive {name!r}: {e}") from e
    if not (isinstance(cls, type) and issubclass(cls, Primitive)):
        raise ValueError(f"{name!r} is not a Primitive subclass")
    return cls
