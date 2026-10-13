"""Contract every primitive follows. Read the module docstring of router.py for how they are run.

Rules (also in CLAUDE.md):
- params are pydantic fields; unknown keys are rejected (ConfigModel forbids extras)
- run() is idempotent: a second delivery of the same event must be a no-op or a skip
- raise PermanentError for anything a retry cannot fix; let transport errors propagate
- write through GraphQL (gql) so granular privileges apply; declare them in required_privileges
"""

import logging
from typing import Any, ClassVar, Dict, List, Optional

from datahub.configuration.common import ConfigModel, GraphError, OperationalError
from datahub.ingestion.graph.client import DataHubGraph
from pydantic import BeforeValidator
from typing_extensions import Annotated

from datahub_workflow_actions.events import WorkflowEvent


class PermanentError(Exception):
    """The event cannot be applied and retrying will not help. Logged and acked, never retried."""


def first_str(value: Any) -> Optional[str]:
    if isinstance(value, list):
        return str(value[0]) if value else None
    return None if value is None else str(value)


def as_list(value: Any) -> List[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    return [str(v) for v in items if v not in (None, "")]


# A param that takes one value: a bound `field:<id>` is a list, so take its first element.
FirstStr = Annotated[Optional[str], BeforeValidator(first_str)]
# A param that takes many values: a literal scalar is wrapped, empties are dropped.
StrList = Annotated[List[str], BeforeValidator(as_list)]


def logger_for(name: str) -> logging.Logger:
    # datahub's run-log filter only surfaces INFO from `datahub*` loggers.
    return logging.getLogger(f"datahub_actions.workflow_actions.{name}")


class Primitive(ConfigModel):
    required_privileges: ClassVar[List[str]] = []
    # Where the privileges apply, for the startup manifest: "launching entity", "domain (created)", ...
    target: ClassVar[str] = "launching entity"

    def privileges(self) -> List[str]:
        """Privileges this configured instance needs. Override when they depend on the bound params."""
        return list(self.required_privileges)

    def run(self, graph: DataHubGraph, event: WorkflowEvent) -> None:
        raise NotImplementedError


def gql(graph: DataHubGraph, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    """execute_graphql with the error split the router relies on.

    GMS answers semantic rejections (authorization, validation, not found) as GraphQL `errors`,
    which the client raises as GraphError: permanent. Transport failures (5xx, connection) are
    OperationalError, a GraphError subclass that must stay retryable, so it is checked first.
    """
    try:
        return graph.execute_graphql(query, variables=variables)
    except OperationalError:
        raise
    except GraphError as e:
        raise PermanentError(str(e)) from e


def require_urn(value: Optional[str], prefix: str, what: str) -> str:
    if not value or not value.startswith(prefix):
        raise PermanentError(f"{what} must be a {prefix}* URN, got {value!r}")
    return value
