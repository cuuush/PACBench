# Muse budget gateway

A local OpenAI-compatible gateway for Spark Contributor. PACBench uses one shared
gateway for authoring, judges, Hermes and memory-provider inference.

The gateway enforces **$10 per UTC day**, with no lifetime cap. It reserves a
conservative worst-case bound before forwarding each request and settles reported
usage afterward. Completed requests without usable usage retain their full bound
on their original request date; active reservations span midnight. A daily budget
refusal stays latched until the next UTC day.

## Install and run

Python 3.11+ on a Unix host is required. Runtime code uses the standard library.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Supply `META` through your secret manager or environment settings, then run:

```sh
.venv/bin/python -m muse_gateway --state /path/to/private/gateway-state
```

The default listener is `127.0.0.1:8099`; `--port` selects another port. A genuinely
new installation creates its private state on first startup. For an existing
installation, restore its original state before starting: a new empty ledger is
not recovery of previous spending.

The state directory contains `ledger.sqlite3`, `client-token`, `address.json` and
`server.lock`. Keep it outside Git and preserve it separately from this repository.
For a simple consistent backup, stop the gateway before copying the whole state
directory. Never commit credentials, ledgers or private state.

## Clients and PACBench

Use the base URL from `address.json` with `/p/<project>/v1`, the local bearer token
from `client-token`, and model `muse-spark-1.3-contributor`. Native
`chat/completions`, `responses`, streaming and a filtered `models` inventory are
supported. The authenticated `/health` endpoint reports budget and access status.

PACBench expects this checkout at `/workspace/muse-gateway` and private state at
`/workspace/shared/muse-gateway`; set `MUSE_GATEWAY_STATE` on PACBench clients if
the state directory differs. Every inference client must use the same gateway.

Upstream 401/403 responses latch an access circuit. Only a successful free model
inventory clears it; restarting never resets spending or a budget refusal.
Protocol diagnostics store fixed shapes and types, not prompts or tool arguments.

## Tests

```sh
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

Tests use local mock upstreams and temporary ledgers, without paid inference.
