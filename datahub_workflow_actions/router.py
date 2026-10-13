"""WorkflowRouter: the one Action in the recipe.

Each EntityChangeEvent_v1 is parsed once into a WorkflowEvent, matched against the rules for its
workflow id and phase, and every step of a matching rule is bound and run in order. Config is
validated at create() so a bad recipe never starts consuming, and the startup log prints the
privileges the service account needs.

Error contract per step: PermanentError or invalid bound values -> log, stop this event's remaining
steps, return (the framework acks). Anything else propagates (the framework retries, then writes
the envelope to the failed-events file).
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Type

from datahub.configuration.common import ConfigModel
from datahub_actions.action.action import Action
from datahub_actions.event.event_envelope import EventEnvelope
from datahub_actions.pipeline.pipeline_context import PipelineContext
from pydantic import ValidationError, field_validator, model_validator

from datahub_workflow_actions.binding import resolve, validate_refs
from datahub_workflow_actions.events import WorkflowEvent, parse, short_workflow_id
from datahub_workflow_actions.primitive import PermanentError, Primitive, logger_for
from datahub_workflow_actions.registry import resolve_primitive

logger = logger_for("router")

Phase = Literal["CREATED", "STEP", "COMPLETED", "CANCELLED"]
When = Literal["ACCEPTED", "REJECTED", "ANY"]
READ_PRIVILEGE = "View Entity Page"  # every primitive reads before it writes


class Rule(ConfigModel):
    phase: Phase
    when: Optional[When] = None
    step_id: Optional[str] = None
    do: List[Dict[str, Optional[Dict[str, Any]]]]

    @field_validator("do", mode="before")
    @classmethod
    def _bare_names(cls, value: Any) -> Any:
        # `- my.mod:Cls` with no params is a YAML string; treat it as `{my.mod:Cls: {}}`.
        return [{item: {}} if isinstance(item, str) else item for item in value] if isinstance(value, list) else value

    @model_validator(mode="after")
    def _shape(self) -> "Rule":
        if self.when is not None and self.phase != "COMPLETED":
            raise ValueError("`when` is only valid with `phase: COMPLETED`")
        if self.step_id is not None and self.phase != "STEP":
            raise ValueError("`step_id` is only valid with `phase: STEP`")
        if not self.do:
            raise ValueError("`do` must list at least one primitive")
        for i, step in enumerate(self.do):
            if len(step) != 1:
                raise ValueError(f"do[{i}] must be a single-key mapping: `- <primitive>: {{<params>}}`")
        return self

    def effective_when(self) -> str:
        return self.when or "ACCEPTED"


class RouterConfig(ConfigModel):
    workflows: Dict[str, List[Rule]]


@dataclass
class CompiledStep:
    name: str
    cls: Type[Primitive]
    params: Dict[str, Any]


@dataclass
class CompiledRule:
    phase: str
    when: str
    step_id: Optional[str]
    steps: List[CompiledStep]


def compile_config(config: RouterConfig) -> Dict[str, List[CompiledRule]]:
    compiled: Dict[str, List[CompiledRule]] = defaultdict(list)
    for key, rules in config.workflows.items():
        workflow_id = short_workflow_id(key)
        for r_i, rule in enumerate(rules):
            steps = []
            for s_i, step in enumerate(rule.do):
                ((name, params),) = step.items()
                params = params or {}
                where = f"workflows[{key!r}][{r_i}].do[{s_i}] ({name})"
                try:
                    cls = resolve_primitive(name)
                    validate_refs(params)
                    cls.model_validate(params)  # unresolved `field:`/`event:` strings type-check as strings
                except (ValueError, ValidationError) as e:
                    raise ValueError(f"{where}: {e}") from e
                steps.append(CompiledStep(name, cls, params))
            compiled[workflow_id].append(CompiledRule(rule.phase, rule.effective_when(), rule.step_id, steps))
    return dict(compiled)


def manifest(compiled: Dict[str, List[CompiledRule]]) -> str:
    by_target: Dict[str, set] = defaultdict(set)
    for rules in compiled.values():
        for rule in rules:
            for step in rule.steps:
                privileges = step.cls.model_validate(step.params).privileges()
                by_target[step.cls.target].update(privileges + [READ_PRIVILEGE])
    return "; ".join(f"{', '.join(sorted(privs))} on {target}" for target, privs in sorted(by_target.items()))


def _matches(rule: CompiledRule, event: WorkflowEvent) -> bool:
    if rule.phase != event.phase:
        return False
    if rule.phase == "COMPLETED" and rule.when != "ANY" and rule.when != event.result:
        return False
    if rule.phase == "STEP" and rule.step_id is not None and rule.step_id != event.step_id:
        return False
    return True


class WorkflowRouter(Action):
    @classmethod
    def create(cls, config_dict: dict, ctx: PipelineContext) -> "Action":
        try:
            config = RouterConfig.model_validate(config_dict or {})
        except ValidationError as e:
            raise ValueError(f"invalid workflow-actions config: {e}") from e
        return cls(config, ctx)

    def __init__(self, config: RouterConfig, ctx: PipelineContext) -> None:
        if ctx.graph is None:
            raise ValueError("the recipe needs a `datahub:` block so primitives can write to GMS")
        self.graph = ctx.graph.graph
        self.rules = compile_config(config)
        logger.info(
            "workflow-actions ready for %d workflow(s); privileges required: %s",
            len(self.rules),
            manifest(self.rules) or "none",
        )

    def act(self, envelope: EventEnvelope) -> None:
        event = parse(envelope)
        if event is None:
            return
        for rule in self.rules.get(event.workflow_id, []):
            if not _matches(rule, event):
                continue
            for step in rule.steps:
                try:
                    primitive = step.cls.model_validate(resolve(step.params, event))
                except ValidationError as e:
                    logger.error("Request %s (%s/%s) %s: invalid values, not retried: %s", event.request_urn, event.workflow_id, event.phase, step.name, e)
                    return
                try:
                    primitive.run(self.graph, event)
                except PermanentError as e:
                    logger.error("Request %s (%s/%s) %s: %s; not retried", event.request_urn, event.workflow_id, event.phase, step.name, e)
                    return

    def close(self) -> None:
        pass
