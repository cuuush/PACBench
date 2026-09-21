# Pilot operations (hard-won, 2026-09-21)

How to launch and monitor pilots/renders on this machine. Read this
before (re)launching anything long-running.

## Interpreter pin

Background shells resolve `python3` to Homebrew 3.14, which lacks the
repo's third-party packages (`hindsight_api`, …). The suite and all
provider work run under `/usr/local/bin/python3` (3.12). **Every
background launch MUST use the absolute path**:

```
cd ~/code/PACBench && /usr/local/bin/python3 -m pacbench run-checkpoint …
```

`ModuleNotFoundError: hindsight_api` on an otherwise good command =
wrong interpreter, not a repo bug.

## Credentials

- Hermes driver: PACBench `.env` via `creds.load_driver` (never live profiles).
- Hindsight internal LLM: explicit `HINDSIGHT_API_LLM_API_KEY` wins;
  otherwise the adapter falls back to the repo `.env` META credential
  (`load_config(DEFAULT_ENV)`); fail-closed when neither exists.

## Long jobs: poll-only

Renders and pilots run minutes–hours. Launch with notify, then LEAVE
THEM ALONE — two pilots were killed ~5 min in by turn interruption,
wasting spent calls. Check progress read-only:

```
# render bundle growth (no interruption)
/usr/local/bin/python3 -c "import json; d=json.load(open('runs/render-race-week-substantial-v1.json')); print(len(d['records']))"
ps aux | grep batch_render | grep -v grep
```

## Resume

`batch_render` resumes from the bundle: re-run the identical command
with the same `--out`; finished beats are skipped, not re-spent.

## Smoke before pilot

New provider path? One checkpoint first, then the life:

```
/usr/local/bin/python3 -m pacbench run-checkpoint --life poker-collision \
  --checkpoint poker-collision/eric-now --provider hindsight --runs runs/
```

Artifacts land in `runs/checkpoint-<UTC>/` (`manifest.json`,
`grade.json`, `turns.json`, `retrieval.json`). Empty dir + gone
process = fail-fast (read the command's tail output).
