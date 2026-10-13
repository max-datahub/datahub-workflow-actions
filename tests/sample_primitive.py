"""A custom primitive loaded by import path in tests (the customer-extension path)."""

from typing import ClassVar, List, Optional, Tuple

from datahub.configuration.common import OperationalError

from datahub_workflow_actions.primitive import FirstStr, PermanentError, Primitive, StrList

RUNS: List[Tuple[Optional[str], List[str], str]] = []


class Echo(Primitive):
    required_privileges: ClassVar[List[str]] = ["Echo Things"]
    value: FirstStr = None
    values: StrList = []
    fail: Optional[str] = None  # "permanent" | "transport"

    def run(self, graph, event) -> None:
        if self.fail == "permanent":
            raise PermanentError("echo refused")
        if self.fail == "transport":
            raise OperationalError("gms unreachable")
        RUNS.append((self.value, list(self.values), event.request_urn))


class NotAPrimitive:
    pass
