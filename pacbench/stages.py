"""Small authoring stages: emit specs, never automatically spend or pad dialogue."""
from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

from .semantic_experiment import resolve_spec

TOPICS = (
    "Maya and the user compare their commutes, a delayed bus, and an ordinary rainy workday.",
    "Devon and the user trade stories about a disappointing lunch and a temperamental coffee machine.",
    "Maya and the user talk about a film they watched and disagree mildly about the ending.",
    "Devon and the user discuss a laptop update, a slow download, and their afternoon at work.",
    "Maya and the user swap stories about a neighbour's dog and an uneventful walk home.",
)


def build_stage(base: dict, extra_messages: int) -> dict:
    if extra_messages not in (0, 100):
        raise ValueError("start with 0 or 100 additional messages; review before 1000")
    if base.get("week_of"):
        raise ValueError("start with a small scene spec")
    spec = copy.deepcopy(base)
    spec["id"] = f"{base['id']}-plus-{extra_messages}"
    scenes, target, _ = resolve_spec(base, Path("stage-validation"))
    # Insert earlier ordinary conversations; the pizza plan stays intact.
    # Exchanges span topics and two ongoing threads.
    count = extra_messages // 20
    first = min(datetime.fromisoformat(s.at) for s in scenes)
    added = []
    for i in range(count):
        at = first - timedelta(days=(count - i + 4) // 5, hours=4 - i % 5)
        added.append({"id": f"ordinary-{i + 1:02d}", "channel": "imessage",
                      "thread": "maya" if i % 2 == 0 else target.thread,
                      "at": at.isoformat(), "message_range": [18, 22],
                      "scene": TOPICS[i % len(TOPICS)] +
                      " Continue naturally from any earlier messages. Do not make new plans "
                      "or restate the pizza plan; include normal topic drift."})
    spec["scenes"] = added + spec["scenes"]
    resolve_spec(spec, Path("stage-validation"))
    return spec


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("--extra-messages", type=int, choices=(0, 100), default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    spec = build_stage(json.loads(args.base.read_text()), args.extra_messages)
    # A deliberate command per stage; never run providers or generate text here.
    with args.output.open("x") as f:
        json.dump(spec, f, indent=2)
        f.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
