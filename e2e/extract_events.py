"""Split a pipeline log that includes e2e/capture.yaml's hello_world output into one JSON file per
workflow event, named <out-dir>/NNN-<ECE operation>[-<params.operation>][-<result>][-step].json.
Reads the log on stdin:

  docker logs <container> 2>&1 | python e2e/extract_events.py e2e/out/events
"""

import json
import pathlib
import sys
from typing import Any, Dict, List

MARK = "Hello world! Received event:"


def label(event: Dict[str, Any]) -> str:
    params = event.get("parameters") or {}
    parts = [event.get("operation"), params.get("operation"), params.get("result"), "step" if params.get("stepId") else None]
    return "-".join(p for p in parts if p)


def blocks(lines: List[str]) -> List[Dict[str, Any]]:
    """hello_world prints json.dumps(indent=4) right after the marker; the other pipeline's log
    records ("[2026-...] INFO ...") can interleave, so they are dropped from the block."""
    out, buf, inside = [], [], False
    for line in lines:
        if line.startswith(MARK):
            inside, buf = True, []
        elif inside and not line.startswith("["):
            buf.append(line)
            if line.rstrip() == "}":
                inside = False
                try:
                    out.append(json.loads("\n".join(buf)))
                except json.JSONDecodeError as e:
                    print(f"skipped one unparsable block: {e}", file=sys.stderr)
    return out


def main() -> None:
    out_dir = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "e2e/out/events")
    out_dir.mkdir(parents=True, exist_ok=True)
    envelopes = blocks(sys.stdin.read().splitlines())
    for i, envelope in enumerate(envelopes, 1):
        path = out_dir / f"{i:03d}-{label(envelope.get('event') or {})}.json"
        path.write_text(json.dumps(envelope, indent=2) + "\n")
        print(path)
    print(f"{len(envelopes)} event(s) written to {out_dir}")


if __name__ == "__main__":
    main()
