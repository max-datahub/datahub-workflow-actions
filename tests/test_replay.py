import json
import pathlib
from types import SimpleNamespace

import datahub_workflow_actions.replay as replay_mod
from datahub_workflow_actions.replay import build_router, replay
from datahub_workflow_actions.router import WorkflowRouter
from tests.conftest import DATASET, FakeGraph

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "completed_accepted.json"
CONFIG = {"workflows": {"dataset-certification": [{"phase": "COMPLETED", "do": [{"add_tags": {"entity": "event:entityUrn", "tags": ["urn:li:tag:CERTIFIED"]}}]}]}}


def test_replay_feeds_each_line_and_counts_failures(capsys) -> None:
    graph = FakeGraph()
    router = WorkflowRouter.create(CONFIG, SimpleNamespace(graph=SimpleNamespace(graph=graph)))
    one_line = json.dumps(json.loads(FIXTURE.read_text()))
    ok, failed = replay([one_line, "", "{not json", one_line + "\n"], router)
    assert (ok, failed) == (2, 1)
    assert graph.operations() == ["batchAddTags", "batchAddTags"]
    assert graph.calls[0][1]["input"]["resources"] == [{"resourceUrn": DATASET}]
    out = capsys.readouterr().out
    assert "line 3: FAILED" in out and "line 4: ok" in out


def test_build_router_uses_recipe_datahub_block(monkeypatch) -> None:
    seen = {}

    class FakeDataHubGraph:
        def __init__(self, config):
            seen["server"], seen["token"] = config.server, config.token

    monkeypatch.setattr(replay_mod, "DataHubGraph", FakeDataHubGraph)
    router = build_router({"datahub": {"server": "http://gms:8080", "token": ""}, "action": {"type": "x", "config": CONFIG}})
    assert isinstance(router, WorkflowRouter)
    assert seen == {"server": "http://gms:8080", "token": None}
