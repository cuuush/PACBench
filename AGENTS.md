# Gateway operating rules

All Spark inference for PACBench goes through this local gateway. Never bypass a budget refusal, waive unknown costs, change the request date, or reset a ledger to free spending. The hard limit is $10 per UTC day, with no lifetime cap. Atomic reservations cover the configured model's full input and output context at conservative uncached prices; successful usage settles to observed token counts. Missing usage/errors retain their full bounds on their request dates. Active requests span midnight. Daily monetary refusals latch until the next UTC day.

Upstream HTTP 401/403 also latches a shared access circuit: refuse further paid requests before reserving or forwarding. Only a successful free upstream model inventory clears it; a restart does not. Clearing auth never changes costs or a daily budget refusal.

Serve only the configured Spark Contributor model. The supported bound is 1,048,576 input/output tokens at $0.10/$0.20 per million. Revalidate pricing and limits before adding a model. Do not change native prompts, reasoning or output limits. Native SSE is passed through and final usage is observed; chat requests ask for the API's usage chunk. No prompts, credentials or raw upstream errors enter the ledger/logs.

Run `python -m pytest -q` before starting inference. Tests exercise local transports and accounting, not model quality. Start with `python -m muse_gateway --state /workspace/shared/muse-gateway`; the managed `META` binding is read directly and never copied into Git. Respect the managed HTTP proxy and CA trust. State contains the private client token, address and SQLite ledger; keep it outside this repository. The exclusive server lock is required before recovering abandoned workers.

This checkout was rebuilt on 2026-10-19 after the environment restored an older snapshot without the prior gateway checkout or private ledger. Do not claim historical ledger recovery. PACBench's published earlier call receipts remain separate evidence. No inference was made in the restored workspace before the replacement boundary was tested.
