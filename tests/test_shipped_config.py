import json
import pathlib
import re

import pytest
from datahub.configuration.config_loader import load_config_file

from datahub_workflow_actions.router import RouterConfig, compile_config

ROOT = pathlib.Path(__file__).resolve().parents[1]


def kafka_recipe() -> dict:
    return load_config_file(ROOT / "recipes" / "kafka.yaml", resolve_env_vars=True)


def test_kafka_recipe_only_reads_platform_events_and_prefilters_workflow_events() -> None:
    recipe = kafka_recipe()
    assert recipe["source"]["config"]["topic_routes"].keys() == {"pe"}
    assert int(recipe["source"]["config"]["connection"]["consumer_config"]["max.poll.interval.ms"]) >= 300000
    predicate = recipe["filters"][0]["config"]["filter"]["EntityChangeEvent_v1"]["event"][0]
    assert predicate == {"entityType": "actionRequest", "category": "LIFECYCLE", "parameters": {"actionRequestType": "WORKFLOW_FORM_REQUEST"}}
    assert recipe["action"]["type"] == "datahub_workflow_actions.router:WorkflowRouter"
    assert recipe["options"]["failure_mode"] == "CONTINUE"


def test_kafka_recipe_config_compiles() -> None:
    compiled = compile_config(RouterConfig.model_validate(kafka_recipe()["action"]["config"]))
    assert set(compiled) == {"domain-create", "domain-edit", "dataset-certification"}


@pytest.mark.parametrize("name", ["domain-create", "domain-edit", "dataset-certification"])
def test_workflow_json_pins_urn_and_recipe_bindings_match_its_form(name) -> None:
    workflow = json.loads((ROOT / "workflows" / f"{name}.json").read_text())
    assert workflow["urn"] == f"urn:li:actionWorkflow:{name}"
    form_fields = {f["id"] for f in workflow["trigger"]["form"]["fields"]}
    rules = kafka_recipe()["action"]["config"]["workflows"][name]
    bound = set(re.findall(r"field:([A-Za-z0-9_]+)", json.dumps(rules)))
    assert bound <= form_fields, f"recipe binds {bound - form_fields} which {name} does not collect"


def test_cloud_recipe_points_at_the_router() -> None:
    recipe = load_config_file(ROOT / "recipes" / "cloud.yaml", resolve_env_vars=True)
    spec = recipe["source"]["config"]["action_spec"]
    assert spec["type"] == "datahub_workflow_actions.router:WorkflowRouter"
    compile_config(RouterConfig.model_validate(spec["config"]))
