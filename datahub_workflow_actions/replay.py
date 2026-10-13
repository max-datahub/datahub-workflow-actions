"""Re-feed a failed-events file through the router.

    python -m datahub_workflow_actions.replay /var/datahub/actions/failed/workflow-actions/failed_events.log -c recipes/kafka.yaml

Each line of the file is one event envelope as the framework wrote it. Primitives are idempotent,
so replaying lines that already half-applied is safe. Exit status is 1 if any line failed.
"""

import argparse
import sys
from typing import Iterable, Optional, Sequence, Tuple

from datahub.configuration.config_loader import load_config_file
from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
from datahub_actions.api.action_graph import AcrylDataHubGraph
from datahub_actions.event.event_envelope import EventEnvelope
from datahub_actions.pipeline.pipeline_context import PipelineContext

from datahub_workflow_actions.router import WorkflowRouter


def build_router(recipe: dict) -> WorkflowRouter:
    datahub = recipe.get("datahub") or {}
    graph = DataHubGraph(DatahubClientConfig(server=datahub["server"], token=datahub.get("token") or None))
    config = (recipe.get("action") or {}).get("config")
    return WorkflowRouter.create(config, PipelineContext("replay", AcrylDataHubGraph(graph)))  # type: ignore[return-value]


def replay(lines: Iterable[str], router: WorkflowRouter) -> Tuple[int, int]:
    ok = failed = 0
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            router.act(EventEnvelope.from_json(line))
        except Exception as e:  # keep going; the summary and exit status report it
            failed += 1
            print(f"line {number}: FAILED {e}")
        else:
            ok += 1
            print(f"line {number}: ok")
    return ok, failed


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Replay a workflow-actions failed_events.log through the router.")
    parser.add_argument("file", help="failed_events.log written by the actions pipeline")
    parser.add_argument("-c", "--recipe", required=True, help="the pipeline recipe (its datahub: and action.config: blocks are used)")
    args = parser.parse_args(argv)
    router = build_router(load_config_file(args.recipe, resolve_env_vars=True))
    with open(args.file, encoding="utf-8") as handle:
        ok, failed = replay(handle, router)
    print(f"{ok} ok, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
