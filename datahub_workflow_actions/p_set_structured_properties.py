"""set_structured_properties: upsert values on one asset via upsertStructuredProperties.

Per-property replace: listed properties get exactly the given values, unlisted ones are untouched,
so a second run is a no-op. Values are coerced from the form's strings using the property's
definition (fetched once per process): DATE form fields arrive as epoch millis, often as a Java
double string such as "1.793367303267E12"; the date property wants "YYYY-MM-DD".
"""

import datetime
import re
from typing import Any, ClassVar, Dict, List

from datahub.ingestion.graph.client import DataHubGraph
from datahub.metadata.schema_classes import StructuredPropertyDefinitionClass

from datahub_workflow_actions.events import WorkflowEvent
from datahub_workflow_actions.primitive import FirstStr, PermanentError, Primitive, StrList, gql, logger_for, require_urn

logger = logger_for("set_structured_properties")

UPSERT = (
    "mutation($input: UpsertStructuredPropertiesInput!) "
    "{ upsertStructuredProperties(input: $input) { properties { structuredProperty { urn } } } }"
)
DATE_TYPE = "urn:li:dataType:datahub.date"
NUMBER_TYPE = "urn:li:dataType:datahub.number"
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

_DEFINITIONS: Dict[str, StructuredPropertyDefinitionClass] = {}


def definition(graph: DataHubGraph, urn: str) -> StructuredPropertyDefinitionClass:
    if urn not in _DEFINITIONS:
        found = graph.get_aspect(urn, StructuredPropertyDefinitionClass)
        if found is None:
            raise PermanentError(f"unknown structured property {urn}")
        _DEFINITIONS[urn] = found
    return _DEFINITIONS[urn]


def to_date(value: str) -> str:
    if ISO_DATE.fullmatch(value):
        return value
    try:
        millis = int(float(value))
    except ValueError:
        raise PermanentError(f"cannot convert {value!r} to a date") from None
    return datetime.datetime.fromtimestamp(millis / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d")


def _value(defn: StructuredPropertyDefinitionClass, raw: str) -> Dict[str, Any]:
    if defn.valueType == NUMBER_TYPE:
        try:
            return {"numberValue": float(raw)}
        except ValueError:
            raise PermanentError(f"cannot convert {raw!r} to a number") from None
    if defn.valueType == DATE_TYPE:
        return {"stringValue": to_date(raw)}
    return {"stringValue": raw}


class SetStructuredProperties(Primitive):
    required_privileges: ClassVar[List[str]] = ["Edit Properties"]
    entity: FirstStr
    values: Dict[str, StrList]

    def run(self, graph: DataHubGraph, event: WorkflowEvent) -> None:
        entity = require_urn(self.entity, "urn:li:", "entity")
        params = []
        for urn, raw_values in self.values.items():
            require_urn(urn, "urn:li:structuredProperty:", "structured property")
            if not raw_values:
                logger.info("No value for %s on %s; skipping (request %s)", urn, entity, event.request_urn)
                continue
            defn = definition(graph, urn)
            params.append({"structuredPropertyUrn": urn, "values": [_value(defn, v) for v in raw_values]})
        if not params:
            return
        gql(graph, UPSERT, {"input": {"assetUrn": entity, "structuredPropertyInputParams": params}})
        logger.info("Set %s on %s (request %s)", ", ".join(p["structuredPropertyUrn"] for p in params), entity, event.request_urn)
