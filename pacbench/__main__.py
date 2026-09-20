"""PACBench runner CLI (W5): collection + isolated Hermes turns, full pilot.

Subcommands:

- ``list-lives`` — offline: lives and their checkpoints (no Hermes, no model).
- ``smoke`` — one bare turn through the isolated clone (wiring proof).
- ``run-checkpoint`` — one checkpoint end to end (debugging aid, not a result).
- ``run-pilot`` — the complete 3-life development pilot (§45).

Isolation (§42, §45.19): scored runs never read or copy auth/config from the
user's live Hermes profile. The driver model and credential come only from
PACBench-owned ``.env`` (``MODEL`` + ``META``) via ``pacbench.creds``. There is
no ``--auth-from-live`` option and no code path that copies live auth: homes
are always fresh scratch dirs under the run directory.
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


def _load_driver():
    from pacbench import creds
    try:
        return creds.load_driver()
    except Exception as exc:
        print(f"driver error: {exc}", file=sys.stderr)
        raise SystemExit(2)


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
    try:
        driver = _load_driver()
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    print(f"driver: {driver!r}")
    run_dir = _scratch_run_dir(Path(args.runs), "smoke")
    hermes_home = run_dir / "hermes-home"
    try:
        summary = live.run_smoke(
            hermes_home=hermes_home, run_dir=run_dir, model=driver.model_id,
            model_provider=driver.provider, timeout_s=args.timeout)
    except RuntimeError as exc:
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
        print(f"artifacts: {run_dir}")
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"artifacts: {run_dir}")
    return 0


def _load_adapter(home: Path, provider: str, dream_model: str | None):
    if provider == "memcal":
        from provider_adapters.memcal_adapter import MemcalAdapter
        return MemcalAdapter(home, dream_model=dream_model)
    if provider == "lexical":
        from provider_adapters.lexical_adapter import LexicalAdapter
        return LexicalAdapter(home)
    raise RuntimeError(f"unknown provider {provider!r}")


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
    if args.provider not in ("memcal", "lexical"):
        print(f"error: unknown provider {args.provider!r} (W5 supports: memcal, lexical)",
              file=sys.stderr)
        return 2
    try:
        driver = _load_driver()
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    print(f"driver: {driver!r}")
    run_dir = _scratch_run_dir(Path(args.runs), "checkpoint")
    hermes_home = run_dir / "hermes-home"
    try:
        adapter = _load_adapter(run_dir / f"{args.provider}-home",
                                args.provider, driver.model_id)
    except RuntimeError as exc:
        print(f"SETUP FAILED: {exc}", file=sys.stderr)
        return 1
    try:
        summary = live.run_checkpoint_live(
            life=life, checkpoint=checkpoint, adapter=adapter,
            hermes_home=hermes_home, run_dir=run_dir, model=driver.model_id,
            model_provider=driver.provider, timeout_s=args.timeout,
            with_maintenance=args.with_maintenance)
    except RuntimeError as exc:
        print(f"RUN FAILED: {exc}", file=sys.stderr)
        print(f"artifacts: {run_dir}")
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"artifacts: {run_dir}")
    return 0


def _cmd_run_pilot(args: argparse.Namespace) -> int:
    from pacbench import corpus, live
    if args.provider not in ("memcal", "lexical"):
        print(f"error: unknown provider {args.provider!r} (W5 supports: memcal, lexical)",
              file=sys.stderr)
        return 2
    try:
        driver = _load_driver()
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    print(f"driver: {driver!r}")
    pilot_dir = _scratch_run_dir(Path(args.runs), "pilot")
    lives = corpus.development_lives()
    print(f"lives: {[l.id for l in lives]}")

    def make_adapter(home: Path):
        return _load_adapter(home, args.provider, driver.model_id)

    try:
        report = live.run_pilot(
            lives=lives, provider=args.provider, make_adapter=make_adapter,
            pilot_dir=pilot_dir, model=driver.model_id,
            model_provider=driver.provider,
            driver_configured_model=driver.configured_model,
            timeout_s=args.timeout, repetitions=args.repetitions)
    except RuntimeError as exc:
        print(f"PILOT FAILED: {exc}", file=sys.stderr)
        print(f"artifacts: {pilot_dir}")
        return 1
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"artifacts: {pilot_dir}")
    # Exit 0 only when every life is valid; incomplete/invalid is honest non-zero.
    return 0 if report["summary"]["pilot_state"] == "valid" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pacbench",
        description="PACBench W5 runner: collection + isolated Hermes turns + pilot.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-lives", help="list lives and checkpoints (offline)")
    p_list.add_argument("--all", action="store_true",
                        help="include non-development lives")
    p_list.set_defaults(func=_cmd_list_lives)

    p_smoke = sub.add_parser("smoke", help="one bare turn through isolated Hermes")
    p_smoke.add_argument("--runs", default=str(REPO / "runs"))
    p_smoke.add_argument("--timeout", type=float, default=600.0)
    p_smoke.set_defaults(func=_cmd_smoke)

    p_run = sub.add_parser("run-checkpoint", help="one checkpoint via Hermes + provider")
    p_run.add_argument("--life", required=True)
    p_run.add_argument("--checkpoint", required=True)
    p_run.add_argument("--provider", default="memcal")
    p_run.add_argument("--runs", default=str(REPO / "runs"))
    p_run.add_argument("--timeout", type=float, default=600.0)
    p_run.add_argument("--with-maintenance", action="store_true",
                       help="run provider scheduled processing before the checkpoint")
    p_run.set_defaults(func=_cmd_run_checkpoint)

    p_pilot = sub.add_parser("run-pilot", help="full development pilot")
    p_pilot.add_argument("--provider", default="memcal")
    p_pilot.add_argument("--runs", default=str(REPO / "runs"))
    p_pilot.add_argument("--timeout", type=float, default=900.0)
    p_pilot.add_argument("--repetitions", type=int, default=1,
                         help="independent realizations per life (§40); "
                              "each runs once into its own artifact dir")
    p_pilot.set_defaults(func=_cmd_run_pilot)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
