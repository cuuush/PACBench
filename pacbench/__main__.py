"""PACBench runner CLI (W4 slice): collection + isolated Hermes turns.

Subcommands:

- ``list-lives`` — offline: lives and their checkpoints (no Hermes, no model).
- ``smoke`` — one bare turn through the isolated clone (wiring proof).
- ``run-checkpoint`` — one checkpoint end to end: ingest exposed evidence at
  collection times, replay prior user turns through real isolated Hermes,
  ask the checkpoint question through Hermes, grade, persist artifacts.

Only ``run-checkpoint --provider memcal`` constructs a provider adapter, via a
lazy import of ``provider_adapters`` (outside the generic package, so the
memcal-free boundary test keeps passing). Homes default to scratch dirs under
the run dir (never live profiles, §42).

Auth: Hermes model calls need credentials. By default the CLI provisions a
home with no ``auth.json`` (turns will fail honestly). Pass ``--auth-from-live``
to copy ``~/.hermes/auth.json`` read-only into the scratch home (0600, never
written back, redacted from artifacts).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _cmd_list_lives(args: argparse.Namespace) -> int:
    from pacbench import corpus
    lives = corpus.all_lives() if args.all else corpus.development_lives()
    for life in lives:
        print(f"{life.id}  [{life.bet}/{life.role}]  "
              f"{len(life.observations)} obs  {len(life.checkpoints)} checkpoints")
        for cp in life.checkpoints:
            print(f"    {cp.id}  @ {cp.at}  ({cp.family})")
    return 0


def _scratch_run_dir(base: Path, prefix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = base / f"{prefix}-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def _cmd_smoke(args: argparse.Namespace) -> int:
    from pacbench import live
    run_dir = _scratch_run_dir(Path(args.runs), "smoke")
    hermes_home = run_dir / "hermes-home"
    auth_src = Path.home() / ".hermes" if args.auth_from_live else None
    try:
        summary = live.run_smoke(
            hermes_home=hermes_home, run_dir=run_dir, model=args.model,
            model_provider=args.model_provider, timeout_s=args.timeout,
            auth_src=auth_src)  # type: ignore[arg-type]
    except RuntimeError as exc:
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
        print(f"artifacts: {run_dir}")
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"artifacts: {run_dir}")
    return 0


def _load_memcal_adapter(home: Path):
    from provider_adapters.memcal_adapter import MemcalAdapter
    return MemcalAdapter(home)


def _cmd_run_checkpoint(args: argparse.Namespace) -> int:
    from pacbench import corpus, live
    try:
        life = corpus.get_life(args.life)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    matches = [c for c in life.checkpoints if c.id == args.checkpoint]
    if not matches:
        print(f"error: unknown checkpoint {args.checkpoint!r} for life {args.life!r}",
              file=sys.stderr)
        return 2
    checkpoint = matches[0]
    if args.provider != "memcal":
        print(f"error: unknown provider {args.provider!r} (W4 supports: memcal)",
              file=sys.stderr)
        return 2
    run_dir = _scratch_run_dir(Path(args.runs), "checkpoint")
    hermes_home = run_dir / "hermes-home"
    try:
        adapter = _load_memcal_adapter(run_dir / "memcal-home")
    except RuntimeError as exc:
        print(f"SETUP FAILED: {exc}", file=sys.stderr)
        return 1
    auth_src = Path.home() / ".hermes" if args.auth_from_live else None
    try:
        summary = live.run_checkpoint_live(
            life=life, checkpoint=checkpoint, adapter=adapter,
            hermes_home=hermes_home, run_dir=run_dir, model=args.model,
            model_provider=args.model_provider, timeout_s=args.timeout,
            auth_src=auth_src,  # type: ignore[arg-type]
            with_maintenance=args.with_maintenance)
    except RuntimeError as exc:
        print(f"RUN FAILED: {exc}", file=sys.stderr)
        print(f"artifacts: {run_dir}")
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"artifacts: {run_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pacbench",
        description="PACBench W4 runner: collection + isolated Hermes turns.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-lives", help="list lives and checkpoints (offline)")
    p_list.add_argument("--all", action="store_true",
                        help="include non-development lives")
    p_list.set_defaults(func=_cmd_list_lives)

    p_smoke = sub.add_parser("smoke", help="one bare turn through isolated Hermes")
    p_smoke.add_argument("--runs", default=str(REPO / "runs"))
    p_smoke.add_argument("--model", default="grok-4.6")
    p_smoke.add_argument("--model-provider", default="xai-oauth",
                         help="inference provider for the driver model")
    p_smoke.add_argument("--timeout", type=float, default=600.0)
    p_smoke.add_argument("--auth-from-live", action="store_true",
                         help="copy ~/.hermes/auth.json read-only into scratch home")
    p_smoke.set_defaults(func=_cmd_smoke)

    p_run = sub.add_parser("run-checkpoint", help="one checkpoint via Hermes + provider")
    p_run.add_argument("--life", required=True)
    p_run.add_argument("--checkpoint", required=True)
    p_run.add_argument("--provider", default="memcal")
    p_run.add_argument("--runs", default=str(REPO / "runs"))
    p_run.add_argument("--model", default="grok-4.6")
    p_run.add_argument("--model-provider", default="xai-oauth",
                       help="inference provider for the driver model")
    p_run.add_argument("--timeout", type=float, default=600.0)
    p_run.add_argument("--auth-from-live", action="store_true",
                       help="copy ~/.hermes/auth.json read-only into scratch home")
    p_run.add_argument("--with-maintenance", action="store_true",
                       help="run provider scheduled processing before the checkpoint")
    p_run.set_defaults(func=_cmd_run_checkpoint)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
